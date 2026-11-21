"""Model-independent lyric validation and render input versioning."""
import hashlib
import json
import math
import re
from difflib import SequenceMatcher
from backend.generation_params import generation_params


def units(text):
    clean = text.replace('<SP>', ' ')
    found = re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*", clean)
    remaining = re.sub(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*|[\s，。！？、；：,.!?;:—\-…‘’“”\"()（）]", '', clean)
    if remaining:
        raise ValueError(f'无法识别字符“{remaining[:20]}”。请将数字写成汉字，或移除特殊符号。')
    if not found:
        raise ValueError('歌词缺少可演唱内容。')
    return found


def replacement_unit_map(original, replacement):
    """Map each original lyric unit to a replacement unit, allowing many-to-one groups."""
    before, after = units(original), units(replacement)
    if len(before) == len(after):
        return list(range(len(before)))
    matcher = SequenceMatcher(None, [x.casefold() for x in before], [x.casefold() for x in after], autojunk=False)
    mapping = [None] * len(before)
    old_cursor = new_cursor = 0
    for block in matcher.get_matching_blocks():
        old_count, new_count = block.a - old_cursor, block.b - new_cursor
        if old_count or new_count:
            if new_count < 1 or old_count < new_count:
                raise ValueError(f'原词片段“{"".join(before[old_cursor:block.a]) or "空"}”与新词片段“{"".join(after[new_cursor:block.b]) or "空"}”无法自动对应（{old_count} → {new_count} 个发音单位）。请调整新词或手动分配 MIDI 槽位。')
            for offset in range(old_count):
                mapping[old_cursor + offset] = new_cursor + min(new_count - 1, offset * new_count // old_count)
        for offset in range(block.size):
            mapping[block.a + offset] = block.b + offset
        old_cursor, new_cursor = block.a + block.size, block.b + block.size
    if any(index is None for index in mapping):
        raise ValueError('新歌词无法与原唱音符建立连续映射。')
    for target_index in set(mapping):
        if mapping.count(target_index) > 1 and not re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*", after[target_index]):
            raise ValueError('多个原唱字目前只能合并映射到一个英文单词。')
    return mapping


def changed_source_units(original, replacement):
    before, after = units(original), units(replacement)
    mapping = replacement_unit_map(original, replacement)
    return [i for i, target_index in enumerate(mapping) if before[i].casefold() != after[target_index].casefold() or mapping.count(target_index) > 1]


def potential_reference_changes(line):
    """Conservatively include source syllables that a slot option may borrow."""
    before, after = units(line['original']), units(line['text'])
    mapping = replacement_unit_map(line['original'], line['text'])
    changed = set(changed_source_units(line['original'], line['text']))
    controls = {item['index']: item for item in (line.get('midi_slots') or [])} if line.get('mode') == 'score' else {}
    for target, word in enumerate(after):
        indices = [i for i, mapped in enumerate(mapping) if mapped == target]
        control = controls.get(target, {})
        if control.get('slots'):
            changed.update(indices)
        if len(indices) < 2 or control.get('gap_policy', 'auto') == 'keep':
            continue
        following = indices[-1] + 1
        if following < len(before) and mapping[following] != target and not re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*", after[mapping[following]]):
            changed.add(following)
    return sorted(changed)


class LineValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        first = issues[0]
        super().__init__(f'有 {len(issues)} 句未通过校验。第 {first["number"]} 句：{first["reason"]}')


def validate_alignment(project, segment_ids, *, for_render=False):
    if not project.get('assets', {}).get('source'):
        raise ValueError('请先导入原唱干声。')
    if not isinstance(segment_ids, list) or not segment_ids or len(segment_ids) > 100 or any(not isinstance(x,str) for x in segment_ids) or len(set(segment_ids)) != len(segment_ids):
        raise ValueError('请选择 1–100 个不同的歌词片段。')
    if project['duration'] > 1800:
        raise ValueError('首版支持 30 分钟以内的工程。')
    selected = [x for x in project['lines'] if x['id'] in segment_ids]
    if len(selected) != len(segment_ids):
        raise ValueError('歌词片段不存在。')
    issues = []
    numbers = {line['id']: i for i, line in enumerate(project['lines'], 1)}
    for line in selected:
        try:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', line['id']):
                raise ValueError('歌词片段 ID 无效。')
            if not .12-1e-9 <= line['end']-line['start'] <= 20:
                raise ValueError('每个片段须为 0.12–20 秒，请调整句子边界。')
            units(line['original'])
            if for_render:
                validate_render_line(line)
        except ValueError as exc:
            issues.append({'segment_id':line['id'], 'number':numbers[line['id']], 'reason':str(exc)})
    if issues:
        raise LineValidationError(issues)
    return selected


def validate_render_line(line):
    if line.get('mode') == 'score' and 'assignment' in (line.get('midi_note_edits') or {}):
        from backend.midi_slots import validate_midi_note_edits
        validate_midi_note_edits(line['midi_note_edits'], line)
    elif line.get('mode') != 'ying':
        replacement_unit_map(line['original'], line['text'])
    if line.get('restore') and line.get('mode')!='score':
        raise ValueError('原句音色转换仅支持 MIDI 模式。')



def validate_render(project, segment_ids):
    return validate_alignment(project, segment_ids, for_render=True)



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
    if not alignment or (line.get('mode') == 'score' and line.get('midi_note_edits')): return False
    before = units(line['original'])
    try: changed = potential_reference_changes(line)
    except ValueError: return False
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
    parameters = generation_params(line.get('mode'), line.get('generation_params'))
    if parameters != generation_params(line.get('mode')): values['generation_params'] = parameters
    if line.get('mode') == 'score' and line.get('midi_slots'):
        values['midi_slots'] = line['midi_slots']
    if line.get('mode') == 'score' and line.get('midi_note_edits'):
        values['midi_note_edits'] = line['midi_note_edits']
    for key in ('start', 'end'):
        values[key] = round(float(values[key]), 6)
    payload = {'source': project.get('assets', {}).get('source', {}).get('id'),
               'line': values,
               'pipeline': 'soulx-v1-aligned-rosvot-fp16-32'}
    try:
        mapped = replacement_unit_map(line['original'], line['text'])
        if len(set(mapped)) < len(mapped): payload['english_grouping'] = 'controlled-slots-mask-v11'
    except (KeyError, TypeError, ValueError):
        pass
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


def repair_score_pitches(pitches, note_types, durations, f0=None):
    """Repair missing note pitches using local sung F0, then nearby notes."""
    if not pitches or len(pitches)!=len(note_types) or len(pitches)!=len(durations):
        raise ValueError('MIDI 音符数据不完整，请重新分析音频。')
    if any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) or x<=0 for x in durations):
        raise ValueError('MIDI 音符时长无效。')
    values=[int(x) for x in pitches];kinds=[int(x) for x in note_types]
    valid=[i for i,(pitch,kind) in enumerate(zip(values,kinds)) if kind!=1 and 0<pitch<=127]
    invalid=[i for i,(pitch,kind) in enumerate(zip(values,kinds)) if kind!=1 and not 0<pitch<=127]
    centers=[];edges=[0.];cursor=0.
    for duration in durations:
        centers.append(cursor+duration/2);cursor+=duration;edges.append(cursor)
    for i in invalid:
        local=[]
        if f0 is not None and len(f0) and cursor>0:
            first=max(0,round(edges[i]/cursor*len(f0)))
            last=min(len(f0),round(edges[i+1]/cursor*len(f0)))
            frames=list(f0[first:last])
            local=sorted(float(x) for x in frames if math.isfinite(float(x)) and 50<=float(x)<=2000)
            if len(local)<2 or len(local)<len(frames)*.5:local=[]
        if local:
            middle=len(local)//2
            median=local[middle] if len(local)%2 else (local[middle-1]+local[middle])/2
            values[i]=max(1,min(127,round(69+12*math.log2(median/440))))
        elif valid:
            nearest=min(valid,key=lambda j:abs(centers[j]-centers[i]))
            values[i]=values[nearest]
        else:
            raise ValueError('未检测到有效 MIDI 音高，请检查人声或改用旋律模式。')
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
