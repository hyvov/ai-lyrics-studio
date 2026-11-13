"""Subprocess worker: reads shared model files, writes only to its job directory.

Uses the independent Apache-2.0 SoulX-Singer checkout; never imports Studio backend.
"""
from __future__ import annotations
import argparse
import copy
import gc
import inspect
import json
import os
import re
import secrets
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runtime/worker-deps'))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'runtime/matplotlib-cache'))
from backend.singing import units, replacement_unit_map, changed_source_units, repair_alignment, repair_score_pitches
from backend.timeline import timings_to_slots
from backend.audio_compose import match_vocal_level
from backend.score_alignment import reconcile_note_timing
from backend.generation_params import generation_params


def report(stage, **data):
    print('STUDIO_EVENT ' + json.dumps({'stage': stage, **data}, ensure_ascii=False), flush=True)


def clear_gpu():
    import torch
    gc.collect()
    torch.cuda.empty_cache()


def adapt_llama():
    """Bridge upstream adaptive norms to the installed Transformers attention API."""
    import torch
    from transformers.models.llama.modeling_llama import LlamaAttention, LlamaRotaryEmbedding
    from soulxsinger.models.modules.llama import LlamaNARDecoderLayer
    if 'position_embeddings' not in inspect.signature(LlamaAttention.forward).parameters:
        return

    def forward(self, hidden_states, cond_embedding, attention_mask=None, position_ids=None,
                past_key_value=None, output_attentions=False, use_cache=False, **kwargs):
        if past_key_value is not None or use_cache:
            # Upstream passes use_cache=True by default even though no cached values
            # are consumed between its diffusion steps. Never retain a KV cache.
            past_key_value = None
        residual = hidden_states
        hidden_states = self.input_layernorm(hidden_states, cond_embedding=cond_embedding)
        if not hasattr(self, '_lyrics_rotary'):
            self._lyrics_rotary = LlamaRotaryEmbedding(self.self_attn.config).to(hidden_states.device)
        self.self_attn.config._attn_implementation = 'sdpa'
        positions = self._lyrics_rotary(hidden_states, position_ids)
        attention = self.self_attn(hidden_states=hidden_states, attention_mask=attention_mask,
                                   position_ids=position_ids, position_embeddings=positions,
                                   past_key_value=None, use_cache=False, output_attentions=False)
        hidden_states = residual + attention[0]
        hidden_states = hidden_states + self.mlp(self.post_attention_layernorm(hidden_states, cond_embedding=cond_embedding))
        output = (hidden_states,)
        if output_attentions:
            output += (None,)
        if use_cache:
            output += (None,)
        return output
    LlamaNARDecoderLayer.forward = forward


def align_words(aligner, waveform, text, sr, diagnostic_path=None):
    import numpy as np
    original = units(text)
    # The aligner accepts a space separated mixed-language transcript and returns
    # CJK characters / English words, in seconds relative to this cropped audio.
    result = aligner.align(audio=(waveform, sr), text=' '.join(original), language='Chinese')[0]
    if diagnostic_path:
        Path(diagnostic_path).write_text(json.dumps({'engine': type(aligner).__name__, 'confidence': getattr(result, 'confidence', None), 'words': [{'text': x.text, 'start': float(x.start_time), 'end': float(x.end_time)} for x in result.items]}, ensure_ascii=False), encoding='utf-8')
    if len(result.items) != len(original):
        raise ValueError('逐字对齐数量与原词不符，请校正原词或调整句子边界。')
    duration = len(waveform) / sr
    words, spans, indices = [], [], []
    repaired, repaired_count = repair_alignment([(x.start_time, x.end_time) for x in result.items], duration)
    if repaired_count:
        report('alignment_warning', message=f'{repaired_count} 个短音节按邻近边界估算，建议生成后试听校验。')
    cursor = 0.
    for index, (word, item) in enumerate(zip(original, result.items)):
        if units(item.text)[0].lower() != word.lower():
            raise ValueError('逐字对齐文本不匹配，请检查原词。')
        a, b = repaired[index]
        if not np.isfinite(a + b) or a < cursor - .04 or b > duration + .08 or b - a < .02 - .000001:
            raise ValueError(f'逐字对齐出现异常时间（{word}: {a:.3f}–{b:.3f}，前字结束 {cursor:.3f}），请修正原词或边界。')
        a, b = max(cursor, a), min(duration, b)
        gap = a - cursor
        if gap >= .04:
            words.append('<SP>'); spans.append(gap); indices.append(None)
        elif gap > 0 and spans:
            spans[-1] += gap
        elif gap > 0:
            a = 0.
        words.append(word); spans.append(b - a); indices.append(index)
        cursor = b
    if duration - cursor >= .02:
        words.append('<SP>'); spans.append(duration - cursor); indices.append(None)
    elif spans:
        spans[-1] += duration - cursor
    if not spans or any(x <= 0 for x in spans):
        raise ValueError('无法建立有效的逐字时长。')
    return words, spans, indices


def map_target_notes(original, replacement, note_words, durations, pitches, types, f0, mode, midi_slots=None):
    """Keep source note events while assigning one English word across grouped attacks."""
    from preprocess.tools.g2p import g2p_transform
    import numpy as np
    from backend.midi_slots import validate_midi_slots
    source_units, target_units = units(original), units(replacement)
    unit_map = replacement_unit_map(original, replacement)
    source_index = -1
    event_sources, target_words = [], []
    for word, kind in zip(note_words, types):
        if word == '<SP>':
            event_sources.append(None); target_words.append('<SP>'); continue
        if int(kind) == 2:
            source_index += 1
        if source_index < 0 or source_index >= len(source_units) or word.casefold() != source_units[source_index].casefold():
            raise ValueError('音符槽位与原词无法一致映射，请校正原词或边界。')
        event_sources.append(source_index)
        target_words.append(target_units[unit_map[source_index]])
    if source_index + 1 != len(source_units):
        raise ValueError('音符分析没有覆盖全部原唱歌词，请检查边界。')
    target_types = [int(kind) for kind in types]
    target_pitches = [int(pitch) for pitch in pitches]
    target_durations = [float(duration) for duration in durations]
    target_f0 = np.asarray(f0, dtype=np.float32).copy()
    target_phonemes = g2p_transform(target_words, 'Mandarin')
    controls = {entry['index']: entry for entry in (validate_midi_slots(midi_slots, replacement) or [])} if mode == 'score' else {}
    affected = set(changed_source_units(original, replacement))
    merges = []
    for target_index, target_word in enumerate(target_units):
        grouped_sources = [i for i, mapped in enumerate(unit_map) if mapped == target_index]
        control = controls.get(target_index)
        if len(grouped_sources) < 2 and not control:
            continue
        events = [i for i, source in enumerate(event_sources) if source in grouped_sources]
        attacks = [i for i in events if int(types[i]) == 2]
        if len(grouped_sources) >= 2 and len(attacks) < 2:
            raise ValueError(f'英文词“{target_word}”映射的原唱音符不足，无法跨音符演唱。')
        token = g2p_transform([target_word], 'Mandarin')[0]
        if not token.startswith('en_'):
            raise ValueError('多个原唱字目前只能合并映射到一个英文单词。')
        if any(note_words[i] == '<SP>' for i in range(events[0], events[-1] + 1)):
            raise ValueError(f'英文词“{target_word}”对应的音符之间有原唱停顿，请调整边界后再生成。')
        # Use the following source syllable's note for the English coda when
        # a long silent slot follows it. Then move that syllable into the gap.
        # For 分开的时候 -> rest的时候, REST uses 分/开/的's three notes,
        # 的 moves into the gap, and 时/候 retain their original notes.
        moved_lyric = None
        following = next((i for i in range(events[-1] + 1, len(event_sources))
                          if event_sources[i] is not None), None)
        rest_slot = following + 1 if following is not None else None
        policy = control['gap_policy'] if control else 'auto'
        available_gap = (following is not None and following == events[-1] + 1 and rest_slot < len(note_words)
                         and note_words[rest_slot] == '<SP>' and float(durations[rest_slot]) >= .02
                         and not re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*", target_words[following]))
        if policy == 'move' and not available_gap:
            raise ValueError(f'英文词“{target_word}”后没有可借用的相邻空档，请调整原词或选择保持原位置。')
        move_next = available_gap and (policy == 'move' or policy == 'auto' and len(grouped_sources) >= 2 and float(durations[rest_slot]) >= .25)
        if move_next:
            moved_lyric = (target_words[following], target_phonemes[following], target_pitches[following])
            events.append(following)
            affected.add(event_sources[following])
        phones = token[3:].split('-')
        chunks = []
        vowels = {'AA','AE','AH','AO','AW','AY','EH','ER','EY','IH','IY','OW','OY','UH','UW'}
        vowel_index = next((i for i,phone in enumerate(phones) if re.sub(r'\d+$','',phone) in vowels), None)
        if control and control['slots']:
            if len(control['slots']) != len(events):
                raise ValueError(f'英文词“{target_word}”实际占用 {len(events)} 个 MIDI 音符，填写了 {len(control["slots"])} 个音素槽位，请调整。')
            chunks = [slot.split('-') for slot in control['slots']]
            affected.update(grouped_sources)
        elif (len(events) == 3 and vowel_index is not None and vowel_index > 0
                and len(phones) - vowel_index - 1 == 2):
            # Keep the English onset and vowel together, then give each coda
            # consonant its own source note (REST -> R-EH1 | S | T).
            chunks = [phones[:vowel_index + 1], [phones[vowel_index + 1]], [phones[vowel_index + 2]]]
        elif len(events) == 2 and vowel_index is not None and vowel_index > 0 and vowel_index + 1 < len(phones):
            # Give the first attack both the onset and vowel, then put the coda
            # on the second genuine attack (e.g. REST -> R-EH1 | S-T). This
            # avoids squeezing a vowel plus multiple final consonants into a
            # short second note right before the following Chinese syllable.
            chunks = [phones[:vowel_index + 1], phones[vowel_index + 1:]]
        else:
            for offset in range(len(events)):
                start = offset * len(phones) // len(events)
                end = (offset + 1) * len(phones) // len(events)
                chunks.append(phones[start:end] or [phones[-1]])
        for offset, (event_index, chunk) in enumerate(zip(events,chunks)):
            prefix = 'en_cont_' if offset < len(events) - 1 else 'en_last_'
            target_words[event_index] = target_word
            target_phonemes[event_index] = prefix + '-'.join(chunk)
            # Keep genuine source-note attacks (type 2). Only preserve type 3
            # where the source itself was a slur; otherwise coda onsets such as
            # /s/ get suppressed by treating them as a held vowel.
            target_types[event_index] = int(types[event_index])
        if moved_lyric:
            target_words[rest_slot], target_phonemes[rest_slot] = moved_lyric[0], moved_lyric[1]
            target_types[rest_slot] = 2
            target_pitches[rest_slot] = moved_lyric[2]
            # The destination used to be silence, so its original melody F0 is
            # zero. Extend the donor lyric's voiced contour into the new slot;
            # otherwise melody-mode synthesis keeps the relocated character mute.
            total_duration = float(sum(durations))
            if total_duration > 0 and target_f0.size:
                cuts = np.rint(np.cumsum([0.0] + [float(x) for x in durations])
                               / total_duration * target_f0.size).astype(int)
                src_a, src_b = int(cuts[following]), int(cuts[following + 1])
                dst_a, dst_b = int(cuts[rest_slot]), int(cuts[rest_slot + 1])
                donor = target_f0[src_a:src_b]
                voiced = np.flatnonzero(np.isfinite(donor) & (donor > 0))
                if dst_b > dst_a and voiced.size:
                    src_x = voiced / max(1, donor.size - 1)
                    dst_x = np.linspace(0.0, 1.0, dst_b - dst_a)
                    target_f0[dst_a:dst_b] = np.interp(dst_x, src_x, donor[voiced])
            # The standard SoulX preprocessor inserts BOW/EOW at every note.
            # In melody mode, keeping one English word on one merged note avoids
            # turning its phoneme chunks into three apparent syllables. The F0
            # contour remains frame-aligned and retains the original melody.
            if mode == 'melody' and len(events) == 3:
                first = events[0]
                target_words[first] = target_word
                target_phonemes[first] = 'en_' + '-'.join(phones)
                target_types[first] = int(types[first])
                merges.append((first, tuple(events)))
    remove_indices = set()
    for first, events in merges:
        target_durations[first] = sum(target_durations[i] for i in events)
        remove_indices.update(events[1:])
    for remove_index in sorted(remove_indices, reverse=True):
        for values in (target_words, target_phonemes, target_types, target_pitches, target_durations):
            values.pop(remove_index)
    return target_words, target_phonemes, target_types, target_pitches, target_f0, target_durations, sorted(affected)


def expand_reference_mask_for_note_edits(words, types, source_durations, target_durations, affected):
    """Mask original syllables touched by edited target-note time intervals."""
    indices, unit = [], -1
    for word, kind in zip(words, types):
        if word != '<SP>' and int(kind) == 2:
            unit += 1
        indices.append(None if word == '<SP>' else unit)
    source_edges, target_edges = [0.0], [0.0]
    for duration in source_durations: source_edges.append(source_edges[-1] + float(duration))
    for duration in target_durations: target_edges.append(target_edges[-1] + float(duration))
    affected = set(affected)
    intervals = [(target_edges[i], target_edges[i+1]) for i, index in enumerate(indices)
                 if index in affected or abs(source_edges[i]-target_edges[i]) > .001
                 or abs(source_edges[i+1]-target_edges[i+1]) > .001]
    for i, index in enumerate(indices):
        if index is not None and any(source_edges[i] < end and source_edges[i+1] > start for start,end in intervals):
            affected.add(index)
    return sorted(affected)


def mask_changed_reference(prompt, original, replacement, waveform, sr, force=False, affected_source_indices=None):
    """Remove edited syllables from a same-phrase timbre reference."""
    import numpy as np
    before = units(original)
    try: changed = set(changed_source_units(original, replacement)) | set(affected_source_indices or [])
    except ValueError:
        if force: raise
        return None
    if not changed or len(changed) == len(before):
        if force: raise ValueError('没有可用的独立音色参考句；请导入包含其他唱句的歌词和原唱。')
        return None
    unchanged = len(before) - len(changed)
    if not force and (unchanged < 2 or len(changed) / len(before) > .6 and unchanged < 4): return None
    result = copy.deepcopy(prompt)
    words = result['text'].split(); durations = [float(x) for x in result['duration'].split()]
    pitches = result['note_pitch'].split(); types = result['note_type'].split()
    samples = waveform.copy(); slot = -1; cursor = 0.; ranges = []
    for i, (word, duration, kind) in enumerate(zip(words, durations, types)):
        if word != '<SP>' and kind == '2': slot += 1
        if word != '<SP>' and slot in changed:
            start, end = max(0., cursor-.025), min(len(samples)/sr, cursor+duration+.035)
            if ranges and start <= ranges[-1][1]: ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else: ranges.append((start, end))
            words[i], pitches[i], types[i] = '<SP>', '0', '1'
        cursor += duration
    if slot + 1 != len(before): raise ValueError('音色参考的音符与歌词不一致。')
    retained = len(samples) / sr - sum(end-start for start,end in ranges)
    if not force and (retained < 1.6 or retained / (len(samples)/sr) < .5): return None
    for start, end in ranges: samples[round(start*sr):round(end*sr)] = 0
    f0 = [float(x) for x in result['f0'].split()]
    for i in range(len(f0)):
        if any(start <= i * cursor / max(1,len(f0)-1) <= end for start,end in ranges): f0[i] = 0.
    from preprocess.tools.g2p import g2p_transform
    result.update(text=' '.join(words), phoneme=' '.join(g2p_transform(words,'Mandarin')),
                  note_pitch=' '.join(pitches), note_type=' '.join(types), f0=' '.join(map(str,f0)))
    return result, samples


def run(config):
    import numpy as np
    import librosa
    import soundfile as sf
    import torch
    core, models = Path(config['core']), Path(config['models'])
    sys.path.insert(0, str(core))
    os.chdir(core)
    if not torch.cuda.is_available():
        raise ValueError('未检测到可用的 NVIDIA CUDA 显卡。')
    torch.set_num_threads(4)
    device = 'cuda:0'
    jobdir = Path(config['job_dir'])
    # Crop directly from a private source copy; no upstream preprocessing writes
    # next to the source asset or into the shared installed model directory.
    audio, sr = librosa.load(config['source'], sr=24000, mono=True)
    segments = config['segments']
    target_ids = {line['id'] for line in segments}
    analysis_segments = segments + config.get('reference_segments', [])
    prepared = []
    hfa, qwen = None, None
    timing_results = []
    if config.get('hfa'):
        try:
            from backend.hfa import HfaAligner
            hfa = HfaAligner(config['hfa'])
        except Exception as exc:
            report('alignment_warning', message='歌声对齐组件不可用，改用备用对齐：' + str(exc))
    for position, line in enumerate(analysis_segments, 1):
        crop = audio[round(line['start'] * sr):round(line['end'] * sr)]
        if len(crop) < round(sr * .12)-1 or np.max(np.abs(crop)) < .001:
            raise ValueError('当前句音频过短或接近静音，请调整边界。')
        saved = config.get('saved_alignments', {}).get(line['id'])
        aligned = timings_to_slots(line, saved['unit_timings']) if saved else None
        engine = saved.get('engine', 'cached') if saved else None
        if saved:
            report('alignment_cached', segment_id=line['id'], position=position, total=len(segments))
        if aligned is None and hfa is not None:
            report('singing_alignment', segment_id=line['id'], position=position, total=len(segments))
            try:
                aligned = align_words(hfa, crop, line['original'], sr, jobdir / (line['id'] + '-hfa-alignment.json'))
                engine = 'HubertFA'
            except Exception as exc:
                report('alignment_warning', message=f'第 {position} 句歌声对齐未通过校验，尝试备用模型：{exc}')
        if aligned is None:
            report('alignment', segment_id=line['id'], position=position, total=len(segments))
            if qwen is None:
                from qwen_asr import Qwen3ForcedAligner
                qwen = Qwen3ForcedAligner.from_pretrained(str(models / 'Qwen3-ForcedAligner-0.6B'),
                                                        dtype=torch.float16, device_map=device, local_files_only=True)
            try:
                aligned = align_words(qwen, crop, line['original'], sr, jobdir / (line['id'] + '-qwen-alignment.json'))
                engine = 'Qwen'
            except ValueError as exc:
                raise ValueError(f'第 {position} 句对齐失败：{exc}') from exc
        words, spans, indices = aligned
        cursor, unit_timings = line['start'], []
        for word, span, index in zip(words, spans, indices):
            if index is not None:
                unit_timings.append({'text':word,'start':round(cursor,6),'end':round(cursor+span,6)})
            cursor += span
        if line['id'] in target_ids:
            timing_results.append({'segment_id':line['id'],'engine':engine,'unit_timings':saved['unit_timings'] if saved and engine == 'manual' else unit_timings})
        path = jobdir / (line['id'] + '-reference.wav')
        sf.write(path, crop, sr)
        prepared.append((line, path, crop, words, spans, indices))
    (jobdir / 'alignment-result.json').write_text(json.dumps(timing_results,ensure_ascii=False),encoding='utf-8')
    del hfa, qwen
    clear_gpu()
    if config.get('operation') == 'align':
        report('complete', count=len(timing_results))
        return

    report('pitch')
    from preprocess.tools.f0_extraction import F0Extractor
    extractor = F0Extractor(str(models / 'SoulX-Singer-Preprocess/rmvpe/rmvpe.pt'), device=device, verbose=False)
    f0s = []
    for line, path, crop, words, spans, indices in prepared:
        f0 = extractor.process(str(path), verbose=False)
        if np.mean(f0 > 0) < .05:
            raise ValueError('音频中未检测到足够的演唱音高，请导入清晰的原唱干声。')
        f0s.append(f0)
    del extractor
    clear_gpu()

    report('notes')
    from preprocess.tools.note_transcription.model import NoteTranscriber
    pre = models / 'SoulX-Singer-Preprocess/rosvot'
    transcriber = NoteTranscriber(str(pre / 'rosvot/model.pt'), str(pre / 'rwbd/model.pt'), device=device, verbose=False)
    from preprocess.tools.g2p import g2p_transform
    metadata = []
    for position, ((line, path, crop, words, spans, indices), f0) in enumerate(zip(prepared, f0s),1):
        item = dict(item_name=line['id'], wav_fn=str(path), origin_wav_fn=str(path), words=words, word_durs=spans, language='Mandarin')
        notes = transcriber.process(item, apply_rwbd=False, verbose=False)
        note_words, durations = notes['note_text'], notes['note_dur']
        pitches, types = notes['note_pitch'], notes['note_type']
        if not note_words or len({len(note_words), len(durations), len(pitches), len(types)}) != 1:
            raise ValueError('音符转录失败，请调整边界或换用清晰干声。')
        note_words,durations,pitches,types,timing_repaired = reconcile_note_timing(words,spans,note_words,durations,pitches,types)
        if timing_repaired and line['id'] in target_ids:
            report('alignment_warning',message=f'第 {position} 个处理片段的音符已按逐字时间校正，请试听。')
        if line['mode']=='score':
            pitches,repaired=repair_score_pitches(pitches,types,durations,f0)
            if repaired and line['id'] in target_ids:
                report('pitch_warning',message=f'第 {position} 个处理片段有 {repaired} 个无效 MIDI 音高，已参考原唱音高或邻近音符修复，请试听。')
        target_words, target_phonemes, target_types, target_pitches, target_f0, target_durations, affected = map_target_notes(
            line['original'], line['text'], note_words, durations, pitches, types, f0, line['mode'],
            line.get('midi_slots') if line['id'] in target_ids else None)
        total = len(crop) / sr
        note_total = sum(durations)
        if not note_total or abs(note_total - total) > .25:
            raise ValueError('音符总时长与片段时长不一致，请调整边界。')
        durations = [float(d) * total / note_total for d in durations]
        target_durations = [float(d) * total / note_total for d in target_durations]
        if line['mode'] == 'score' and line.get('midi_note_edits'):
            from backend.midi_slots import apply_midi_note_edits
            target_durations, target_pitches = apply_midi_note_edits(
                line['midi_note_edits'], line, Path(config['source']).name, note_words, durations, types,
                target_words, target_durations, target_pitches)
            affected = expand_reference_mask_for_note_edits(note_words, types, durations, target_durations, affected)
        prompt = dict(index=line['id'], language='Mandarin', time=[0, round(total * 1000)],
                      duration=' '.join(f'{d:.6f}' for d in durations), text=' '.join(note_words),
                      phoneme=' '.join(g2p_transform(note_words, 'Mandarin')), note_pitch=' '.join(map(str, pitches)),
                      note_type=' '.join(map(str, types)), f0=' '.join(f'{float(v):.3f}' for v in f0))
        target = {**prompt, 'text': ' '.join(target_words), 'phoneme': ' '.join(target_phonemes),
                  'duration': ' '.join(f'{d:.6f}' for d in target_durations),
                  'note_type': ' '.join(map(str,target_types)),
                  'note_pitch': ' '.join(map(str,target_pitches)),
                  'f0': ' '.join(f'{float(v):.3f}' for v in target_f0)}
        (jobdir / (line['id'] + '-metadata.json')).write_text(json.dumps({'prompt': prompt, 'target': target, 'masked_source_units': affected}, ensure_ascii=False), encoding='utf-8')
        metadata.append((line, path, prompt, target, affected))
    del transcriber
    clear_gpu()

    report('loading_singer')
    adapt_llama()
    from cli.inference import build_model
    from soulxsinger.utils.file_utils import load_config
    from backend.soulx_processor import StudioDataProcessor
    cfg = load_config(str(core / 'soulxsinger/config/soulxsinger.yaml'))
    model = build_model(str(models / 'SoulX-Singer/model.pt'), cfg, device=device, use_fp16=True)
    processor = StudioDataProcessor(cfg.audio.hop_size, cfg.audio.sample_rate, str(core / 'soulxsinger/utils/phoneme/phone_set.json'), device=device)
    outputs = []
    by_id = {line['id']: (path, prompt) for line, path, prompt, _, _ in metadata}
    metadata = [entry for entry in metadata if entry[0]['id'] in target_ids]
    for i, (line, path, prompt, target, affected) in enumerate(metadata):
        report('generating', segment_id=line['id'], position=i + 1, total=len(metadata))
        ref_id = config.get('references', {}).get(line['id'])
        samples, _ = sf.read(path, dtype='float32')
        masked = mask_changed_reference(prompt, line['original'], line['text'], samples, sr, affected_source_indices=affected)
        if masked is not None:
            prompt, samples = masked
            prompt_path = jobdir / (line['id'] + '-masked-reference.wav')
            sf.write(prompt_path, samples, sr)
            report('reference_masked', segment_id=line['id'])
        elif ref_id in by_id:
            prompt_path, prompt = by_id[ref_id]
        else:
            prompt, samples = mask_changed_reference(prompt, line['original'], line['text'], samples, sr, force=True, affected_source_indices=affected)
            prompt_path = jobdir / (line['id'] + '-masked-reference.wav')
            sf.write(prompt_path, samples, sr)
            report('alignment_warning', message='未找到独立参考句，已遮盖原句中改动的字，请试听确认。')
        inputs = {'prompt': processor.process(copy.deepcopy(prompt), str(prompt_path)), 'target': processor.process(copy.deepcopy(target), None)}
        parameters = generation_params(line['mode'],line.get('generation_params'))
        seed_used = parameters['seed'] if parameters['seed'] >= 0 else secrets.randbelow(2147483648)
        torch.manual_seed(seed_used)
        with torch.inference_mode():
            generated = model.infer(inputs, auto_shift=False, pitch_shift=0, n_steps=parameters['steps'], cfg=parameters['cfg'],
                                    control=line['mode'], use_fp16=True).squeeze().float().cpu().numpy()
        required = round((line['end'] - line['start']) * sr)
        if len(generated) < required - min(sr * .25, required * .2) or not np.isfinite(generated).all() or np.max(np.abs(generated)) < .0001:
            raise ValueError('生成音频异常或长度不足，未发布该结果。')
        generated = np.pad(generated[:required], (0, max(0, required - len(generated))))
        output = jobdir / (line['id'] + '-generated.wav')
        sf.write(output, generated, sr, subtype='PCM_16')
        outputs.append({'segment_id': line['id'], 'path': str(output), 'sample_rate': sr, 'frames': required, 'reference_safe': True, 'generation_params':parameters, 'seed_used':seed_used})
    if any(line.get('restore') for line,_,_,_,_ in metadata):
        import shutil
        from backend.svc_worker import run as convert_voice
        svc_config=config.get('svc')
        if not svc_config:raise ValueError('原句音色转换组件不可用。')
        del model,processor,inputs,generated
        clear_gpu()
        for (line,reference,_,_,_),output in zip(metadata,outputs):
            if not line.get('restore'):continue
            report('svc_restore',segment_id=line['id'])
            svc_dir=jobdir/(line['id']+'-svc');svc_dir.mkdir(exist_ok=True)
            convert_voice({**svc_config,'reference':str(reference),'target':output['path'],'job_dir':str(svc_dir)},nested=True)
            converted=svc_dir/'svc.wav'
            samples,converted_sr=sf.read(converted,dtype='float32')
            if converted_sr!=sr or samples.ndim!=1 or len(samples)!=output['frames'] or not np.isfinite(samples).all():
                raise ValueError('原句音色转换结果格式异常。')
            shutil.copyfile(converted,output['path'])
    report('composing')
    merged = audio.copy()
    all_takes = list(config.get('existing_takes', [])) + [
        {'start': line['start'], 'end': line['end'], 'path': output['path']}
        for (line, _, _, _, _), output in zip(metadata, outputs)
    ]
    for take in all_takes:
        clip, clip_sr = sf.read(take['path'], dtype='float32')
        if clip_sr != sr or clip.ndim != 1:
            raise ValueError('历史片段格式异常。')
        a, b = round(take['start'] * sr), min(len(merged), round(take['end'] * sr))
        clip = np.pad(clip[:b-a], (0, max(0, b-a-len(clip))))
        clip = match_vocal_level(clip,audio[a:b],sr)
        fade = min(round(.01 * sr), len(clip) // 4)
        envelope = np.ones(len(clip), dtype=np.float32)
        if fade:
            envelope[:fade] = np.linspace(0, 1, fade)
            envelope[-fade:] = np.linspace(1, 0, fade)
        merged[a:b] = merged[a:b] * (1-envelope) + clip * envelope
    sf.write(jobdir / 'rendered.wav', merged, sr, subtype='PCM_16')
    if config.get('accompaniment'):
        backing, _ = librosa.load(config['accompaniment'], sr=sr, mono=True)
        mix = merged + np.pad(backing[:len(merged)], (0, max(0, len(merged)-len(backing))))
        peak = np.max(np.abs(mix))
        if peak > .98:
            mix *= .98 / peak
        sf.write(jobdir / 'mixed.wav', mix, sr, subtype='PCM_16')
    (jobdir / 'result.json').write_text(json.dumps(outputs), encoding='utf-8')
    report('complete', count=len(outputs))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        run(config)
    except Exception as exc:
        traceback.print_exc()
        report('failed', error=str(exc))
        sys.exit(1)
