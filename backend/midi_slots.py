"""Validate per-word MIDI pronunciation and silence-slot choices."""
import re
import math

_VOWELS = {'AA','AE','AH','AO','AW','AY','EH','ER','EY','IH','IY','OW','OY','UH','UW'}
_CONSONANTS = {'B','CH','D','DH','F','G','HH','JH','K','L','M','N','NG','P','R','S','SH','T','TH','V','W','Y','Z','ZH'}
_PHONES = _CONSONANTS | {v + str(stress) for v in _VOWELS for stress in range(3)} | {'UW'}
_ENGLISH = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*\Z")
_UNITS = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*")


def validate_midi_slots(value, text):
    """Return validated controls; None denotes the original automatic behavior."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('MIDI 音素设置无效。')
    words = _UNITS.findall(text.replace('<SP>', ' '))
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'index', 'word', 'slots', 'gap_policy'}:
            raise ValueError('MIDI 音素设置格式无效。')
        index, word, slots, policy = (entry[k] for k in ('index', 'word', 'slots', 'gap_policy'))
        if type(index) is not int or not 0 <= index < len(words) or index in seen:
            raise ValueError('MIDI 音素设置中的歌词位置无效或重复。')
        if not isinstance(word, str) or not _ENGLISH.fullmatch(word) or words[index].casefold() != word.casefold():
            raise ValueError('MIDI 音素设置与当前英文歌词不一致，请重新设置。')
        if policy not in ('auto', 'keep', 'move'):
            raise ValueError('请选择有效的空档使用方式。')
        if not isinstance(slots, list) or len(slots) > 64:
            raise ValueError('音素槽位必须是最多 64 项的列表。')
        normalized = []
        for slot in slots:
            if not isinstance(slot, str) or not slot or len(slot) > 120:
                raise ValueError('每个 MIDI 槽位必须填写有效的英语音素。')
            chunk = slot.upper().strip()
            if any(phone not in _PHONES for phone in chunk.split('-')):
                raise ValueError(f'音素“{slot}”不在模型支持的英语音素表中。')
            normalized.append(chunk)
        seen.add(index)
        result.append({'index': index, 'word': word, 'slots': normalized, 'gap_policy': policy})
    return result


def validate_midi_note_edits(value, line):
    """Validate edited note boundaries against the source analysis they came from."""
    if value is None:
        return None
    required = {'source_id', 'original', 'start', 'end', 'events'}
    if not isinstance(value, dict) or set(value) not in (required, required | {'lyrics', 'assignment'}):
        raise ValueError('MIDI 音符编辑格式无效。')
    source_id = value['source_id']
    if not isinstance(source_id, str) or not re.fullmatch(r'[a-f0-9]{32}\.(?:wav|mp3|flac|m4a|ogg|aac|opus|webm)', source_id, re.I):
        raise ValueError('MIDI 音符对应的原唱素材无效。')
    if value['original'] != line['original'] or value['start'] != line['start'] or value['end'] != line['end']:
        raise ValueError('原词或整句边界已变化，请重新分析 MIDI 音符。')
    events = value['events']
    if not isinstance(events, list) or not 1 <= len(events) <= 512:
        raise ValueError('MIDI 音符数量无效。')
    previous = float(line['start'])
    result = []
    for event in events:
        if not isinstance(event, dict) or set(event) != {'text', 'type', 'source_duration', 'end', 'pitch'}:
            raise ValueError('MIDI 音符设置格式无效。')
        text, kind, duration, end, pitch = (event[k] for k in ('text','type','source_duration','end','pitch'))
        if not isinstance(text, str) or not text or len(text) > 120 or type(kind) is not int or kind not in (1,2,3):
            raise ValueError('MIDI 音符对应的原词或类型无效。')
        if any(isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) for x in (duration,end)) or duration <= 0:
            raise ValueError('MIDI 音符时长或边界无效。')
        if end - previous < .02 - 1e-7 or end > line['end'] + 1e-6:
            raise ValueError('MIDI 音符边界须依次排列，每个音符至少 20 毫秒。')
        if pitch is not None and (type(pitch) is not int or not 1 <= pitch <= 127):
            raise ValueError('MIDI 音高须为空或 1–127 的整数。')
        result.append({'text':text, 'type':kind, 'source_duration':float(duration), 'end':round(float(end),6), 'pitch':pitch})
        previous = float(end)
    if abs(previous - line['end']) > .001:
        raise ValueError('最后一个 MIDI 音符必须结束在当前句末尾。')
    normalized = {'source_id':source_id, 'original':line['original'], 'start':line['start'], 'end':line['end'], 'events':result}
    if 'assignment' in value:
        from backend.singing import units
        words = units(line['text'])
        assignment = value['assignment']
        if value['lyrics'] != line['text'] or not isinstance(assignment, list) or len(assignment) != len(events):
            raise ValueError('歌词已改变，请重新分配 MIDI 槽位。')
        previous_word = -1
        for i, target in enumerate(assignment):
            if target is None:
                if result[i]['pitch'] is not None:
                    raise ValueError('空拍不能指定音高。')
                continue
            if type(target) is not int or not 0 <= target < len(words) or target not in (previous_word, previous_word + 1):
                raise ValueError('请按新词顺序分配音符，每个字词至少占一个槽位。')
            if target == previous_word and assignment[i-1] is None:
                raise ValueError('同一个字词的音符须连续。')
            previous_word = target
        if previous_word != len(words) - 1:
            raise ValueError('还有新词未分配音符。')
        for control in line.get('midi_slots') or []:
            if control['slots'] and len(control['slots']) != assignment.count(control['index']):
                raise ValueError(f'请为 {control["word"]} 的每个音符填写一组音素，或留空自动分配。')
        normalized.update(lyrics=line['text'], assignment=assignment)
    return normalized


def apply_midi_note_edits(value, line, source_id, words, durations, types, target_words, target_durations, target_pitches):
    """Apply edits to target notes only, preserving the prompt and total duration."""
    value = validate_midi_note_edits(value, line)
    if value is None:
        return target_durations, target_pitches
    events = value['events']
    if value['source_id'] != source_id or len(events) != len(words) or len(target_words) != len(words):
        raise ValueError('原唱或音符分析已变化，请重新打开 MIDI 音符编辑。')
    if any(e['text'] != word or e['type'] != int(kind) or abs(e['source_duration'] - float(duration)) > .03
           for e, word, duration, kind in zip(events, words, durations, types)):
        raise ValueError('MIDI 音符分析与保存时不同，请重新检查音符边界。')
    previous = float(line['start'])
    out_durations, out_pitches = [], list(target_pitches)
    for i, event in enumerate(events):
        out_durations.append(event['end'] - previous)
        previous = event['end']
        if event['pitch'] is not None:
            if target_words[i] == '<SP>':
                raise ValueError(f'第 {i+1} 个位置仍为空拍，不能设置唱音高。')
            out_pitches[i] = event['pitch']
    return out_durations, out_pitches
