"""Word timing validation and cache identity, independent of model libraries."""
import hashlib
import json
import math
from backend.singing import units


def alignment_fingerprint(project, line):
    value = [project.get('assets', {}).get('source', {}).get('id'), line['original'],
             round(float(line['start']), 6), round(float(line['end']), 6)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def validate_timings(line, timings):
    expected = units(line['original'])
    if not isinstance(timings, list) or len(timings) != len(expected):
        raise ValueError('逐字时间数量必须与原唱的汉字/英文单词数量一致。')
    normalized, previous = [], line['start']
    for word, timing in zip(expected, timings):
        if not isinstance(timing, dict) or timing.get('text') != word:
            raise ValueError('逐字时间中的文字必须与原词一致。')
        a, b = timing.get('start'), timing.get('end')
        if any(isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) for x in (a,b)):
            raise ValueError('逐字时间须为有效数字。')
        if a < line['start'] - .000001 or b > line['end'] + .000001 or a < previous - .000001 or b-a < .02 - .000001:
            raise ValueError('逐字时间不能越出当前句、相互重叠或短于 20 毫秒。')
        normalized.append({'text': word, 'start':round(float(a),6), 'end':round(float(b),6)})
        previous = b
    return normalized


def cached_alignment(project, line):
    entry = project.get('alignments', {}).get(line['id'])
    if entry and entry.get('fingerprint') == alignment_fingerprint(project,line):
        return entry
    return None


def timings_to_slots(line, timings):
    timings = validate_timings(line, timings)
    words, spans, indices = [], [], []
    cursor = line['start']
    for index, timing in enumerate(timings):
        gap = timing['start']-cursor
        prefix = 0.
        if gap >= .02:
            words.append('<SP>');spans.append(gap);indices.append(None)
        elif gap > .000001:
            if spans: spans[-1] += gap
            else: prefix = gap
        words.append(timing['text']);spans.append(timing['end']-timing['start']+prefix);indices.append(index)
        cursor = timing['end']
    tail = line['end']-cursor
    if tail >= .02:
        words.append('<SP>');spans.append(tail);indices.append(None)
    elif tail > 0 and spans:
        spans[-1] += tail
    return words,spans,indices
