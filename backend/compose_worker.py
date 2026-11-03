"""Rebuild the mixed track from existing valid takes without loading a model."""
import argparse
import json
import sys
import traceback
from pathlib import Path


def report(stage,**data):
    print('STUDIO_EVENT '+json.dumps({'stage':stage,**data},ensure_ascii=False),flush=True)


def run(config):
    import librosa
    import numpy as np
    import soundfile as sf
    sr=24000;directory=Path(config['job_dir'])
    report('composing')
    merged,_=librosa.load(config['source'],sr=sr,mono=True)
    if not len(merged) or not np.isfinite(merged).all():raise ValueError('原唱音频为空或无效。')
    for take in config['takes']:
        clip,clip_sr=sf.read(take['path'],dtype='float32')
        if clip_sr!=sr or clip.ndim!=1 or not np.isfinite(clip).all():raise ValueError('历史演唱片段格式异常。')
        a,b=round(take['start']*sr),min(len(merged),round(take['end']*sr))
        if a<0 or a>=b:raise ValueError('演唱片段时间超出原唱音频。')
        clip=np.pad(clip[:b-a],(0,max(0,b-a-len(clip))))
        fade=min(round(.01*sr),len(clip)//4);envelope=np.ones(len(clip),dtype=np.float32)
        if fade:
            envelope[:fade]=np.linspace(0,1,fade);envelope[-fade:]=np.linspace(1,0,fade)
        merged[a:b]=merged[a:b]*(1-envelope)+clip*envelope
    sf.write(directory/'rendered.wav',merged,sr,subtype='PCM_16')
    if config.get('accompaniment'):
        backing,_=librosa.load(config['accompaniment'],sr=sr,mono=True)
        if not np.isfinite(backing).all():raise ValueError('伴奏音频包含无效采样。')
        mix=merged+np.pad(backing[:len(merged)],(0,max(0,len(merged)-len(backing))))
        peak=np.max(np.abs(mix))
        if peak>.98:mix*=.98/peak
        sf.write(directory/'mixed.wav',mix,sr,subtype='PCM_16')
    report('complete')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    try:run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc();report('failed',error=str(exc));sys.exit(1)
