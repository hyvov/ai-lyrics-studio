"""Local YingMusic-Singer-Plus worker; reuses the user's existing checkout and weights."""
import argparse
import json
import os
import shutil
import secrets
import sys
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.generation_params import generation_params


def report(stage,**values):
    print('STUDIO_EVENT '+json.dumps({'stage':stage,**values},ensure_ascii=False),flush=True)


def run(config):
    import numpy as np
    import soundfile as sf
    ying=Path(config['ying']['root']);sys.path.insert(0,str(ying));os.chdir(ying)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['WANDB_MODE']='disabled'
    import espeakng_loader
    espeak_dir=Path(os.environ['LOCALAPPDATA'])/'AI-Lyrics-Studio'/'espeak'
    espeak_dir.mkdir(parents=True,exist_ok=True)
    if not (espeak_dir/'espeak-ng-data'/'phontab').is_file():
        shutil.copytree(espeakng_loader.get_data_path(),espeak_dir/'espeak-ng-data',dirs_exist_ok=True)
    if not (espeak_dir/'espeak-ng.dll').is_file():
        shutil.copy2(espeakng_loader.get_library_path(),espeak_dir/'espeak-ng.dll')
    os.environ['ESPEAK_DATA_PATH']=str(espeak_dir)
    os.environ['PHONEMIZER_ESPEAK_LIBRARY']=str(espeak_dir/'espeak-ng.dll')
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    EspeakWrapper.set_library(str(espeak_dir/'espeak-ng.dll'))
    import torch
    import torchaudio
    if not torch.cuda.is_available():raise ValueError('YingMusic 需要 NVIDIA CUDA 显卡。')
    torch.set_num_threads(4)
    from src.YingMusicSinger.infer.YingMusicSinger import YingMusicSinger
    report('loading_ying')
    model=YingMusicSinger.from_pretrained(str(ying),local_files_only=True).to('cuda').eval()
    audio,sr=sf.read(config['source'],always_2d=True,dtype='float32')
    if not len(audio) or not np.isfinite(audio).all():raise ValueError('原唱音频为空或无效。')
    directory=Path(config['job_dir']);outputs=[];takes=list(config.get('existing_takes',[]))
    for i,line in enumerate(config['segments'],1):
        report('generating',segment_id=line['id'],position=i,total=len(config['segments']))
        start,end=round(line['start']*sr),round(line['end']*sr)
        crop=audio[start:end]
        if not len(crop) or np.max(np.abs(crop))<.001:raise ValueError('唱句音频为空或接近静音。')
        ref=directory/(line['id']+'-ying-reference.wav');sf.write(ref,crop,sr,subtype='PCM_24')
        parameters=generation_params('ying',line.get('generation_params'))
        seed_used=parameters['seed'] if parameters['seed']>=0 else secrets.randbelow(2147483648)
        with torch.inference_mode():
            generated,rate=model(ref_audio_path=str(ref),melody_audio_path=str(ref),ref_text=line['original'],target_text=line['text'],
                lrc_align_mode='sentence_level',sil_len_to_end=parameters['silence'],t_shift=parameters['t_shift'],nfe_step=parameters['steps'],cfg_strength=parameters['cfg'],seed=seed_used)
        generated=generated.detach().float().cpu()
        if generated.ndim!=2 or generated.shape[-1]<1 or not torch.isfinite(generated).all():raise ValueError('生成的人声音频异常。')
        generated=generated.mean(dim=0,keepdim=True)
        if int(rate)!=24000:generated=torchaudio.functional.resample(generated,int(rate),24000)
        samples=generated.squeeze(0).numpy();required=round((line['end']-line['start'])*24000)
        if not len(samples) or np.max(np.abs(samples))<.0001:raise ValueError('生成的人声为空。')
        if abs(len(samples)-required)>min(24000*.3,required*.1):
            report('alignment_warning',message=f'第 {i} 句的生成时长与原句不同，已按边界截取或补静音，请试听。')
        samples=np.pad(samples[:required],(0,max(0,required-len(samples))))
        path=directory/(line['id']+'-generated.wav');sf.write(path,samples,24000,subtype='PCM_16')
        outputs.append({'segment_id':line['id'],'path':str(path),'sample_rate':24000,'frames':required,'reference_safe':True,'generation_params':parameters,'seed_used':seed_used})
        takes.append({'start':line['start'],'end':line['end'],'path':str(path)})
    del model
    torch.cuda.empty_cache()
    from backend.compose_worker import run as compose
    compose({**config,'takes':takes})
    (directory/'result.json').write_text(json.dumps(outputs),encoding='utf-8')
    report('complete',count=len(outputs))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    try:run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc();report('failed',error=str(exc));sys.exit(1)
