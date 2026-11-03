"""Offline two-stage separation using the locally installed pymss models."""
import argparse
import gc
import json
import os
import sys
import traceback
from pathlib import Path


def report(stage, **data):
    print('STUDIO_EVENT ' + json.dumps({'stage': stage, **data}, ensure_ascii=False), flush=True)


def run(config):
    sys.path.insert(0, config['deps'])
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from pymss import MSSeparator
    from pymss.model_registry import resolve_model
    if not torch.cuda.is_available():
        raise ValueError('未检测到可用的 NVIDIA CUDA 显卡。')
    report('separation_decode')
    audio, sr = librosa.load(config['source'], sr=44100, mono=False)
    if audio.ndim == 1:
        audio = np.stack([audio, audio])
    if audio.shape[0] != 2 or not audio.shape[1] or not np.isfinite(audio).all():
        raise ValueError('歌曲音频无法解码为有效的单声道或立体声音频。')
    if len(audio[0]) > 1800 * sr:
        raise ValueError('歌曲不能超过 30 分钟。')

    def separate(model, source, stems, stage):
        report(stage)
        resolved = resolve_model(model, model_dir=config['models'], require_supported=True, require_exists=True)
        engine = MSSeparator(model_type=resolved['model_type'], model_path=resolved['model_path'],
            config_path=resolved.get('config_path'), device='cuda', device_ids=[0], output_format='wav',
            use_tta=False, logger=None, debug=False, inference_params={'standardize': False, 'normalize': False})
        try:
            return {str(k).lower(): v for k, v in engine.separate(source, pbar=False, stems=stems).items()}
        finally:
            engine.close()
            engine.del_cache()
            del engine
            gc.collect()
            torch.cuda.empty_cache()

    stems = separate('Kim_MelBandRoformer.ckpt', audio, ['vocals', 'other'], 'separation_vocal')
    if 'vocals' not in stems or 'other' not in stems:
        raise ValueError('分离模型没有返回人声和伴奏。')
    vocal, backing = stems['vocals'], stems['other']
    refined = separate('dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt', vocal.T, ['dry'], 'separation_dry')
    if 'dry' not in refined:
        raise ValueError('去混响模型没有返回干声。')
    vocal = refined['dry']
    directory = Path(config['job_dir'])
    for kind, samples in [('source', vocal), ('accompaniment', backing)]:
        if samples.ndim != 2 or len(samples) == 0 or not np.isfinite(samples).all():
            raise ValueError('分离输出为空或包含无效数据。')
        sf.write(directory / (kind + '.wav'), samples, sr, subtype='PCM_16')
    (directory / 'separation-result.json').write_text(json.dumps({'duration': audio.shape[1] / sr}), encoding='utf-8')
    report('complete')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc()
        report('failed', error=str(exc))
        sys.exit(1)
