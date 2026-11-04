"""Model-independent lyric validation and render input versioning."""
import hashlib
import json
import math
import re
from difflib import SequenceMatcher


def units(text):
    clean = text.replace('<SP>', ' ')
    found = re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*", clean)
    remaining = re.sub(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*|[\s，。！？、；：,.!?;:—\-…‘’“”\"()（）]", '', clean)
    if remaining:
        raise ValueError('当前生成仅支持中文汉字、英文单词和常用标点，请将数字写成汉字。')
    if not found:
        raise ValueError('歌词缺少可演唱内容。')
    return found


def validate_alignment(project, segment_ids):
    if not project.get('assets', {}).get('source'):
        raise ValueError('请先导入原唱干声。')
    if not isinstance(segment_ids, list) or not segment_ids or len(segment_ids) > 100 or any(not isinstance(x,str) for x in segment_ids) or len(set(segment_ids)) != len(segment_ids):
        raise ValueError('请选择 1–100 个不同的歌词片段。')
    if project['duration'] > 1800:
        raise ValueError('首版支持 30 分钟以内的工程。')
    selected = [x for x in project['lines'] if x['id'] in segment_ids]
    if len(selected) != len(segment_ids):
        raise ValueError('歌词片段不存在。')
    for line in selected:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', line['id']):
            raise ValueError('歌词片段 ID 无效。')
        if not .12-1e-9 <= line['end']-line['start'] <= 20:
            raise ValueError('每个片段须为 0.12–20 秒，请调整句子边界。')
        units(line['original'])
    return selected


def validate_render(project, segment_ids):
    selected = validate_alignment(project, segment_ids)
    for line in selected:
        if line.get('mode') != 'ying' and len(units(line['original'])) != len(units(line['text'])):
            raise ValueError('首版需保持新词与原词的发音单位数一致（一个汉字或英文单词算一个单位）。')
        if line.get('restore') and line.get('mode')!='score':
            raise ValueError('原句音色转换仅支持 MIDI 模式。')
    return selected


def choose_reference_phrase(lines, target):
    """Rank non-overlapping source phrases without leaking the target lyric."""
    original = [x.casefold() for x in units(target['original'])]
    replacement = units(target['text'])
    english_ratio = sum(bool(re.fullmatch(r'[A-Za-z]+(?:\'[A-Za-z]+)*', x)) for x in replacement) / len(replacement)
    candidates = []
    for other in lines:
        if other['id'] == target['id'] or not (other['end'] <= target['start'] or other['start'] >= target['end']): continue
        length = other['end'] - other['start']
        if not 1 <= length <= 20: continue
        try: words = [x.casefold() for x in units(other['original'])]
        except ValueError: continue
        if words == original: continue
        ratio = sum(bool(re.fullmatch(r'[a-z]+(?:\'[a-z]+)*', x)) for x in words) / len(words)
        similarity = SequenceMatcher(None, original, words).ratio()
        midpoint = (other['start'] + other['end']) / 2
        target_midpoint = (target['start'] + target['end']) / 2
        rank = (bool(ratio) != bool(english_ratio), abs(ratio-english_ratio), similarity >= .65,
                not 3 <= length <= 8, similarity*.5 + min(2.5, abs(midpoint-target_midpoint)/25))
        candidates.append((rank, other))
    return min(candidates, key=lambda item:item[0])[1] if candidates else None


def prefer_masked_reference(line, alignment):
    """Use verified word spans to avoid analyzing an unnecessary reference phrase."""
    if not alignment: return False
    before, after = units(line['original']), units(line['text'])
    if len(before) != len(after): return False
    changed = [i for i,(a,b) in enumerate(zip(before,after)) if a.casefold()!=b.casefold()]
    unchanged = len(before)-len(changed)
    if not changed or unchanged < 2 or (len(changed)/len(before) > .6 and unchanged < 4): return False
    timings = alignment.get('unit_timings',[])
    if len(timings) != len(before): return False
    start,end = line['start'],line['end']
    ranges = []
    for index in changed:
        span = timings[index]
        a,b=max(start,float(span['start'])-.025),min(end,float(span['end'])+.035)
        if b<=a: return False
        if ranges and a<=ranges[-1][1]: ranges[-1]=(ranges[-1][0],max(b,ranges[-1][1]))
        else: ranges.append((a,b))
    total=end-start
    remaining=total-sum(b-a for a,b in ranges)
    return total>0 and remaining>=1.6 and remaining/total>=.5


def fingerprint(project, line):
    values = {k: line.get(k) for k in ('id', 'original', 'text', 'start', 'end', 'mode', 'restore')}
    for key in ('start', 'end'):
        values[key] = round(float(values[key]), 6)
    payload = {'source': project.get('assets', {}).get('source', {}).get('id'),
               'line': values,
               'pipeline': 'soulx-v1-aligned-rosvot-fp16-32'}
    if line.get('mode')=='score':payload['score_pitch_repair']=1
    if line.get('mode')=='ying':payload['pipeline']='yingmusic-plus-local-v1'
    from backend.timeline import cached_alignment
    alignment = cached_alignment(project, line)
    if line.get('mode') != 'ying' and alignment and alignment.get('engine') == 'manual':
        payload['manual_timings'] = alignment['unit_timings']
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
        raise ValueError('原唱与提供的原词未能成功对齐。请确认音频含有实际演唱，并核对原词和句子范围；单字改词也需要先完成原唱对齐。')
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


def repair_score_pitches(pitches, note_types, durations):
    """Replace unvoiced score notes with the closest valid score pitch."""
    if not pitches or len(pitches)!=len(note_types) or len(pitches)!=len(durations):
        raise ValueError('MIDI 音符数据不完整，请重新分析音频。')
    if any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) or x<=0 for x in durations):
        raise ValueError('MIDI 音符时长无效。')
    values=[int(x) for x in pitches];kinds=[int(x) for x in note_types]
    valid=[i for i,(pitch,kind) in enumerate(zip(values,kinds)) if kind!=1 and 0<pitch<=127]
    invalid=[i for i,(pitch,kind) in enumerate(zip(values,kinds)) if kind!=1 and not 0<pitch<=127]
    if invalid and not valid:raise ValueError('未检测到有效 MIDI 音高，请检查人声或改用旋律模式。')
    centers=[];cursor=0.
    for duration in durations:
        centers.append(cursor+duration/2);cursor+=duration
    for i in invalid:
        nearest=min(valid,key=lambda j:abs(centers[j]-centers[i]))
        values[i]=values[nearest]
    return values,len(invalid)

def composition_fingerprint(project):
    payload = {'duration': round(float(project['duration']), 6), 'assets': {k: v.get('id') for k, v in project.get('assets', {}).items() if k in ('source','accompaniment')},
               'lines': [fingerprint(project, x) for x in project['lines']]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def svc_fingerprint(project, target_kind):
    if target_kind not in ('source','rendered'):
        raise ValueError('无效的音色转换目标。')
    target=(project.get('assets',{}).get('source',{}).get('id') if target_kind=='source'
            else project.get('composite',{}).get('rendered'))
    payload={'target_kind':target_kind,'target':target,'reference':project.get('assets',{}).get('reference',{}).get('id'),
             'composition':composition_fingerprint(project) if target_kind=='rendered' else None}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
