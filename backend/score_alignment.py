"""Keep note attacks aligned with verified lyric timing when transcription drifts."""
from __future__ import annotations


def reconcile_note_timing(words, spans, note_words, durations, pitches, types):
    if not words or len(words) != len(spans) or not note_words or len({len(note_words),len(durations),len(pitches),len(types)}) != 1:
        raise ValueError('歌词时间与音符数据不完整。')
    if any(float(x) <= 0 for x in spans) or any(float(x) <= 0 for x in durations):
        raise ValueError('歌词或音符时长无效。')
    total = sum(spans); note_total = sum(durations)
    if abs(total-note_total) > .25: raise ValueError('音符总时长与片段时长不一致，请调整边界。')
    durations = [float(x)*total/note_total for x in durations]
    word_bounds=[]; cursor=0.
    for word,span in zip(words,spans):
        word_bounds.append((cursor,cursor+span,word)); cursor+=span
    notes=[];cursor=0.
    for word,duration,pitch,kind in zip(note_words,durations,pitches,types):
        notes.append((cursor,cursor+duration,word,int(pitch),int(kind)));cursor+=duration
    attack_ranges=[]; current_word=None; valid_events=True
    for start,end,word,pitch,kind in notes:
        if kind==1 and word=='<SP>':
            continue
        if kind==2 and word!='<SP>':
            current_word=word; attack_ranges.append([word,start,end])
        elif kind==3 and word!='<SP>' and attack_ranges and current_word is not None and word.casefold()==current_word.casefold():
            attack_ranges[-1][2]=end
        else:
            valid_events=False
    spoken=[(a,b,w) for a,b,w in word_bounds if w!='<SP>']
    aligned=valid_events and len(attack_ranges)==len(spoken) and all(
        text.casefold()==word.casefold() and abs(a-start)<=max(.02,min(.06,(b-a)*.15)) and abs(b-end)<=max(.02,min(.06,(b-a)*.15))
        for (a,b,word),(text,start,end) in zip(spoken,attack_ranges))
    if aligned: return note_words,durations,pitches,types,False
    edges=sorted({0.,total,*[x for item in word_bounds for x in item[:2]],*[x for item in notes for x in item[:2]]})
    result=[]
    for left,right in zip(edges,edges[1:]):
        if right-left<1e-8: continue
        mid=(left+right)/2
        wi=next((i for i,(a,b,_) in enumerate(word_bounds) if a<=mid<b),None)
        ni=next((i for i,(a,b,*_) in enumerate(notes) if a<=mid<b),None)
        if wi is None or ni is None: continue
        word=word_bounds[wi][2]; pitch=notes[ni][3] if word!='<SP>' else 0
        if result and result[-1]['wi']==wi and result[-1]['pitch']==pitch:
            result[-1]['duration']+=right-left
        else: result.append({'wi':wi,'word':word,'pitch':pitch,'duration':right-left})
    # Merge sub-frame shards only within one lyric unit, never across a word boundary.
    i=0
    while i<len(result):
        if result[i]['duration']>=.020-1e-9: i+=1;continue
        recipient=i-1 if i and result[i-1]['wi']==result[i]['wi'] else i+1 if i+1<len(result) and result[i+1]['wi']==result[i]['wi'] else None
        if recipient is None: raise ValueError('歌词时间过短，无法生成有效音符，请校正逐字时间。')
        result[recipient]['duration']+=result[i]['duration'];result.pop(i);i=max(0,i-1)
    seen=set();out_types=[]
    for event in result:
        wi=event['wi'];out_types.append(1 if event['word']=='<SP>' else 3 if wi in seen else 2)
        if event['word']!='<SP>': seen.add(wi)
    if len(seen)!=len(spoken): raise ValueError('音符分析未覆盖全部歌词，请校正逐字时间。')
    return [x['word'] for x in result],[x['duration'] for x in result],[x['pitch'] for x in result],out_types,True
