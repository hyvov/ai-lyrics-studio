"""Model-independent lyric validation and render input versioning."""
import hashlib
import json
import re


def units(text):
    clean = text.replace('<SP>', ' ')
    found = re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*", clean)
    remaining = re.sub(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*|[\s，。！？、；：,.!?;:—\-…‘’“”\"()（）]", '', clean)
    if remaining:
        raise ValueError('当前生成仅支持中文汉字、英文单词和常用标点，请将数字写成汉字。')
    if not found:
        raise ValueError('歌词缺少可演唱内容。')
    return found


def validate_render(project, segment_ids):
    if not project.get('assets', {}).get('source'):
        raise ValueError('请先导入原唱干声。')
    if not isinstance(segment_ids, list) or not segment_ids or len(segment_ids) > 100 or any(not isinstance(x, str) for x in segment_ids) or len(set(segment_ids)) != len(segment_ids):
        raise ValueError('请选择 1–100 个不同的歌词片段。')
    if project['duration'] > 1800:
        raise ValueError('首版生成支持 30 分钟以内的工程。')
    selected = [x for x in project['lines'] if x['id'] in segment_ids]
    if len(selected) != len(segment_ids):
        raise ValueError('歌词片段不存在。')
    for line in selected:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', line['id']):
            raise ValueError('歌词片段 ID 无效。')
        if not 1 <= line['end'] - line['start'] <= 20:
            raise ValueError('每个生成片段须为 1–20 秒，请调整边界。')
        if len(units(line['original'])) != len(units(line['text'])):
            raise ValueError('首版需保持新词与原词的发音单位数一致（一个汉字或英文单词算一个单位）。')
        if line.get('restore'):
            raise ValueError('原句音色转换尚未接入，请关闭该选项。')
    return selected


def fingerprint(project, line):
    values = {k: line.get(k) for k in ('id', 'original', 'text', 'start', 'end', 'mode', 'restore')}
    for key in ('start', 'end'):
        values[key] = round(float(values[key]), 6)
    payload = {'source': project.get('assets', {}).get('source', {}).get('id'),
               'line': values,
               'pipeline': 'soulx-v1-aligned-rosvot-fp16-32'}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def repair_alignment(items, duration):
    """Repair a small number of collapsed aligner spans using adjacent time only.

    Singing often maps two fast syllables to one timestamp. Keep this explicit
    and reject widespread failures instead of inventing an entire timeline.
    """
    import math
    spans = [[float(x[0]), float(x[1])] for x in items]
    if any(not math.isfinite(a + b) or a < -.08 or b > duration + .08 or a > b + .04 for a, b in spans):
        raise ValueError('对齐时间超出片段。')
    bad = [i for i, (a, b) in enumerate(spans) if b - a < .02]
    if len(bad) > max(1, len(spans) // 3):
        raise ValueError('过多歌词无法对齐，请校正原词、缩短句子或调整边界。')
    for i in bad:
        a, b = spans[i]
        left = spans[i - 1] if i else None
        right = spans[i + 1] if i + 1 < len(spans) else None
        if right and right[1] - max(b, right[0]) >= .08:
            end = min(right[1] - .03, max(b, right[0]) + .06)
            spans[i] = [max(0., a), end]
            right[0] = end
        elif left and min(a, left[1]) - left[0] >= .08:
            start = max(left[0] + .03, min(a, left[1]) - .06)
            left[1] = start
            spans[i] = [start, min(duration, max(a + .02, b))]
        else:
            raise ValueError('歌词短音节无法修复，请校正原词或边界。')
    return spans, len(bad)

def composition_fingerprint(project):
    payload = {'duration': round(float(project['duration']), 6), 'assets': {k: v.get('id') for k, v in project.get('assets', {}).items()},
               'lines': [fingerprint(project, x) for x in project['lines']]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
