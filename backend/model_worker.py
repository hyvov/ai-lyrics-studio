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
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runtime/worker-deps'))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'runtime/matplotlib-cache'))
from backend.singing import units, repair_alignment


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


def align_words(aligner, waveform, text, sr):
    import numpy as np
    original = units(text)
    # The aligner accepts a space separated mixed-language transcript and returns
    # CJK characters / English words, in seconds relative to this cropped audio.
    result = aligner.align(audio=(waveform, sr), text=' '.join(original), language='Chinese')[0]
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
        if not np.isfinite(a + b) or a < cursor - .04 or b > duration + .08 or b - a < .02:
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
    prepared = []
    from qwen_asr import Qwen3ForcedAligner
    report('alignment')
    aligner = Qwen3ForcedAligner.from_pretrained(str(models / 'Qwen3-ForcedAligner-0.6B'),
                                                dtype=torch.float16, device_map=device, local_files_only=True)
    for line in segments:
        crop = audio[round(line['start'] * sr):round(line['end'] * sr)]
        if len(crop) < sr or np.max(np.abs(crop)) < .001:
            raise ValueError('当前句音频过短或接近静音，请调整边界。')
        words, spans, indices = align_words(aligner, crop, line['original'], sr)
        path = jobdir / (line['id'] + '-reference.wav')
        sf.write(path, crop, sr)
        prepared.append((line, path, crop, words, spans, indices))
    del aligner
    clear_gpu()

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
    for (line, path, crop, words, spans, indices), f0 in zip(prepared, f0s):
        item = dict(item_name=line['id'], wav_fn=str(path), origin_wav_fn=str(path), words=words, word_durs=spans, language='Mandarin')
        notes = transcriber.process(item, apply_rwbd=False, verbose=False)
        note_words, durations = notes['note_text'], notes['note_dur']
        pitches, types = notes['note_pitch'], notes['note_type']
        if not note_words or len({len(note_words), len(durations), len(pitches), len(types)}) != 1:
            raise ValueError('音符转录失败，请调整边界或换用清晰干声。')
        # Map type=2 word onsets to aligned original words. Slur notes (type=3)
        # retain the same original and new pronunciation; silence stays silence.
        target_units = units(line['text'])
        original_non_silent = [w for w in words if w != '<SP>']
        target_words, index = [], -1
        for word, kind in zip(note_words, types):
            if word == '<SP>':
                target_words.append(word)
            else:
                if kind == 2:
                    index += 1
                if index < 0 or index >= len(target_units) or word.lower() != original_non_silent[index].lower():
                    raise ValueError('音符槽位与原词无法一致映射，请缩短片段后重试。')
                target_words.append(target_units[index])
        if index + 1 != len(target_units):
            raise ValueError('音符分析没有覆盖全部歌词，请检查边界。')
        total = len(crop) / sr
        note_total = sum(durations)
        if not note_total or abs(note_total - total) > .25:
            raise ValueError('音符总时长与片段时长不一致，请调整边界。')
        durations = [float(d) * total / note_total for d in durations]
        prompt = dict(index=line['id'], language='Mandarin', time=[0, round(total * 1000)],
                      duration=' '.join(f'{d:.6f}' for d in durations), text=' '.join(note_words),
                      phoneme=' '.join(g2p_transform(note_words, 'Mandarin')), note_pitch=' '.join(map(str, pitches)),
                      note_type=' '.join(map(str, types)), f0=' '.join(f'{float(v):.3f}' for v in f0))
        target = {**prompt, 'text': ' '.join(target_words), 'phoneme': ' '.join(g2p_transform(target_words, 'Mandarin'))}
        (jobdir / (line['id'] + '-metadata.json')).write_text(json.dumps({'prompt': prompt, 'target': target}, ensure_ascii=False), encoding='utf-8')
        metadata.append((line, path, prompt, target))
    del transcriber
    clear_gpu()

    report('loading_singer')
    adapt_llama()
    from cli.inference import build_model
    from soulxsinger.utils.file_utils import load_config
    from soulxsinger.utils.data_processor import DataProcessor
    cfg = load_config(str(core / 'soulxsinger/config/soulxsinger.yaml'))
    model = build_model(str(models / 'SoulX-Singer/model.pt'), cfg, device=device, use_fp16=True)
    processor = DataProcessor(cfg.audio.hop_size, cfg.audio.sample_rate, str(core / 'soulxsinger/utils/phoneme/phone_set.json'), device=device)
    outputs = []
    for i, (line, path, prompt, target) in enumerate(metadata):
        report('generating', segment_id=line['id'], position=i + 1, total=len(metadata))
        inputs = {'prompt': processor.process(copy.deepcopy(prompt), str(path)), 'target': processor.process(copy.deepcopy(target), None)}
        with torch.inference_mode():
            generated = model.infer(inputs, auto_shift=False, pitch_shift=0, n_steps=32, cfg=3,
                                    control=line['mode'], use_fp16=True).squeeze().float().cpu().numpy()
        required = round((line['end'] - line['start']) * sr)
        if len(generated) < required - sr * .25 or not np.isfinite(generated).all() or np.max(np.abs(generated)) < .0001:
            raise ValueError('生成音频异常或长度不足，未发布该结果。')
        generated = np.pad(generated[:required], (0, max(0, required - len(generated))))
        output = jobdir / (line['id'] + '-generated.wav')
        sf.write(output, generated, sr, subtype='PCM_16')
        outputs.append({'segment_id': line['id'], 'path': str(output), 'sample_rate': sr, 'frames': required})
    report('composing')
    merged = audio.copy()
    all_takes = list(config.get('existing_takes', [])) + [
        {'start': line['start'], 'end': line['end'], 'path': output['path']}
        for (line, _, _, _), output in zip(metadata, outputs)
    ]
    for take in all_takes:
        clip, clip_sr = sf.read(take['path'], dtype='float32')
        if clip_sr != sr or clip.ndim != 1:
            raise ValueError('历史片段格式异常。')
        a, b = round(take['start'] * sr), min(len(merged), round(take['end'] * sr))
        clip = np.pad(clip[:b-a], (0, max(0, b-a-len(clip))))
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
