"""Produce editable lyric drafts from the locally installed FireRedASR2 model."""
import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path


def report(stage, **kwargs):
    print('STUDIO_EVENT '+json.dumps({'stage':stage, **kwargs},ensure_ascii=False),flush=True)


def clean_text(value):
    value=re.sub(r'<[^>]+>','',str(value)).strip()
    return re.sub(r'[^\u4e00-\u9fffA-Za-z\s\'，。！？、；：,.!?;:—\-…‘’“”"()（）]','',value).strip()


def result_drafts(result, start, end):
    """Use model token gaps as phrase hints; never fabricate aligned word times."""
    tokens=[]
    for raw in result.get('timestamp') or []:
        if not isinstance(raw,(tuple,list)) or len(raw)<3: continue
        text=clean_text(raw[0])
        try: a,b=float(raw[1])+start,float(raw[2])+start
        except (ValueError,TypeError): continue
        if not text or not math.isfinite(a) or not math.isfinite(b): continue
        a=max(start,min(end,a));b=max(a,min(end,b))
        if b>a and (not tokens or a>=tokens[-1][1]):tokens.append((text,a,b))
    if not tokens:
        text=clean_text(result.get('text') or '')
        return [{'start':round(start,3),'end':round(end,3),'text':text}] if re.search(r'[\u4e00-\u9fffA-Za-z]',text) else []
    groups=[];group=[]
    for item in tokens:
        if group and ((item[1]-group[-1][2]>=.35 and len(group)>=4) or len(group)>=22):
            groups.append(group);group=[]
        group.append(item)
    if group:groups.append(group)
    drafts=[]
    for i,group in enumerate(groups):
        lower=start if i==0 else (groups[i-1][-1][2]+group[0][1])/2
        upper=end if i==len(groups)-1 else (group[-1][2]+groups[i+1][0][1])/2
        a=max(lower,group[0][1]-.12);b=min(upper,group[-1][2]+.12)
        if b<=a:continue
        text=''
        for token,_,_ in group:
            if text and text[-1].isascii() and text[-1].isalpha() and token[0].isascii() and token[0].isalpha():text+=' '
            text+=token
        if re.search(r'[\u4e00-\u9fffA-Za-z]',text):drafts.append({'start':round(a,3),'end':round(b,3),'text':text})
    return drafts


def run(config):
    import librosa
    import numpy as np
    import torch
    from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config
    if not torch.cuda.is_available():
        raise ValueError('自动识词需要 NVIDIA CUDA 显卡。')
    report('asr_decode')
    audio,sr=librosa.load(config['source'],sr=16000,mono=True)
    if not len(audio) or len(audio)>1800*sr or not np.isfinite(audio).all():
        raise ValueError('音频为空、过长或包含无效采样。')
    intervals=librosa.effects.split(audio,top_db=35,frame_length=2048,hop_length=512)
    if not len(intervals):
        raise ValueError('没有检测到人声，请检查原唱音频。')
    # Join short pauses and limit each inference window to 14 seconds.
    ranges=[]; start,end=map(int,intervals[0])
    for a,b in intervals[1:]:
        a,b=int(a),int(b)
        if a-end>=int(.85*sr) or b-start>14*sr:
            ranges.append((start,end));start=a
        end=b
    ranges.append((start,end))
    chunks=[]
    for a,b in ranges:
        while b-a>14*sr:
            chunks.append((a,a+14*sr));a+=14*sr
        if b-a>=int(.25*sr):chunks.append((a,b))
    if not chunks:
        raise ValueError('没有检测到足够长的人声。')
    report('asr_load')
    torch.cuda.is_bf16_supported=lambda:False
    options=FireRedAsr2Config(use_gpu=True,use_half=True,beam_size=1,nbest=1,decode_max_len=0,
        softmax_smoothing=1.25,aed_length_penalty=.6,eos_penalty=1.,return_timestamp=True)
    model=FireRedAsr2.from_pretrained('aed',config['model'],options)
    drafts=[]
    for index,(a,b) in enumerate(chunks,1):
        report('asr_recognize',position=index,total=len(chunks))
        pcm=np.ascontiguousarray(audio[a:b]*32768.,dtype=np.float32)
        results=model.transcribe([str(index)],[[sr,pcm]])
        drafts.extend(result_drafts((results or [{}])[0],a/sr,b/sr))
    if not drafts:
        raise ValueError('没有识别到歌词，请检查原唱或导入 SRT/LRC。')
    Path(config['job_dir'],'transcription-result.json').write_text(json.dumps(drafts,ensure_ascii=False),encoding='utf-8')
    report('complete')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    try:
        run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc();report('failed',error=str(exc));sys.exit(1)
