"""Run the local optimized SoulX-Singer-SVC component in an isolated process."""
import argparse
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runtime/worker-deps'))


def report(stage, **values):
    print('STUDIO_EVENT '+json.dumps({'stage':stage,**values},ensure_ascii=False),flush=True)


def run(config, nested=False):
    import numpy as np
    import soundfile as sf
    import torch
    core=Path(config['core']);sys.path.insert(0,str(core));os.chdir(core)
    from backend.model_worker import adapt_llama,clear_gpu
    from preprocess.tools.f0_extraction import F0Extractor
    from soulxsinger.utils.audio_utils import load_wav
    from soulxsinger.utils.file_utils import load_config
    if not torch.cuda.is_available():raise ValueError('音色转换需要 NVIDIA CUDA 显卡。')
    directory=Path(config['job_dir']);modelroot=Path(config['svc_root'])
    report('svc_pitch')
    extractor=F0Extractor(str(Path(config['models'])/'SoulX-Singer-Preprocess/rmvpe/rmvpe.pt'),device='cuda:0',verbose=False)
    pitch=[]
    for name in ('reference','target'):
        output=directory/(name+'-f0.npy')
        extractor.process(config[name],f0_path=str(output),verbose=False)
        f0=np.load(output)
        if not len(f0) or not np.isfinite(f0).all() or np.mean(f0>0)<.03:
            raise ValueError('参考或目标音频缺少清晰演唱，请检查人声素材。')
        pitch.append(torch.from_numpy(f0).unsqueeze(0).to('cuda'))
    del extractor;clear_gpu()
    report('svc_load')
    adapt_llama()
    spec=importlib.util.spec_from_file_location('local_soulx_svc',config['svc_wrapper'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    cfg=load_config(str(core/'soulxsinger/config/soulxsinger.yaml'))
    model=module.SoulXSingerSVC(cfg,modelroot/'whisper-base','cuda').to('cuda')
    checkpoint=torch.load(modelroot/'SoulX-Singer/model-svc.pt',weights_only=False,map_location='cpu')
    model.load_state_dict(checkpoint['state_dict'],strict=True);del checkpoint
    model.half();model.mel.float();model.eval()
    prompt=load_wav(config['reference'],cfg.audio.sample_rate).to('cuda')
    target=load_wav(config['target'],cfg.audio.sample_rate).to('cuda')
    report('svc_generate')
    with torch.inference_mode():
        generated,_=model.infer(prompt,target,pitch[0],pitch[1],n_steps=32,cfg=1.,use_fp16=True,
            progress_callback=lambda completed,total:report('svc_generate',position=completed,total=total))
    data=generated.squeeze().float().cpu().numpy()
    if data.ndim!=1 or not len(data) or not np.isfinite(data).all() or np.max(np.abs(data))<.0001:
        raise ValueError('转换结果为空或异常。')
    sf.write(directory/'svc.wav',data,int(cfg.audio.sample_rate),subtype='PCM_16')
    if config.get('accompaniment'):
        import librosa
        report('svc_mix')
        backing,_=librosa.load(config['accompaniment'],sr=int(cfg.audio.sample_rate),mono=True)
        mixed=data+np.pad(backing[:len(data)],(0,max(0,len(data)-len(backing))))
        peak=np.max(np.abs(mixed))
        if peak>.98:mixed*=.98/peak
        sf.write(directory/'svc-mixed.wav',mixed,int(cfg.audio.sample_rate),subtype='PCM_16')
    if not nested:report('complete')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    try:run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc();report('failed',error=str(exc));sys.exit(1)
