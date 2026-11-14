"""Locate AI Lyrics Studio’s own runtime and model weights."""
from __future__ import annotations
import os
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_COMMIT = '81aeb3ae772c70093c3de74dc23c92d983801ae4'
HFA_COMMIT = 'e46590b490fca790e6cd2dacbc0bb7779babe383'

@dataclass(frozen=True)
class Runtime:
    studio: Path
    python: Path
    core: Path
    models: Path

    @property
    def component_home(self):
        return Path(os.environ.get('AI_LYRICS_COMPONENTS_HOME', self.studio / 'components')).resolve()

    @property
    def music_models(self):
        return self.component_home / 'music/models' if 'AI_LYRICS_COMPONENTS_HOME' in os.environ else self.studio / 'music/models'

    def gpu_installed(self):
        external = os.environ.get('AI_LYRICS_GPU_PACKAGES')
        base = Path(external) if external else self.python.parent / 'Lib/site-packages'
        return all((base / x).is_file() for x in ('torch/__init__.py', 'torch/lib/torch_cuda.dll', 'torchaudio/__init__.py'))

    @classmethod
    def discover(cls):
        home = Path(os.environ.get('AI_LYRICS_RUNTIME_HOME', ROOT / 'runtime/independent')).resolve()
        return cls(home, Path(os.environ.get('AI_LYRICS_PYTHON', home / 'python/python.exe')).resolve(),
                   Path(os.environ.get('AI_LYRICS_SOURCE_ROOT', ROOT / 'runtime/SoulX-Singer')).resolve(),
                   Path(os.environ.get('AI_LYRICS_MODELS_ROOT', home / 'pretrained_models')).resolve())

    def hfa_config(self):
        model = Path(os.environ.get('AI_LYRICS_HFA_MODEL', self.studio / 'components/hfa/model')).resolve()
        python = Path(os.environ.get('AI_LYRICS_HFA_PYTHON', model.parent / 'python')).resolve()
        source = Path(os.environ.get('AI_LYRICS_HFA_SOURCE', ROOT / 'runtime/Vocal2Midi')).resolve()
        required = [model / 'model.onnx', model / 'vocab.json', model / 'config.json', model / 'VERSION',
                    source / 'inference/HubertFA/tools/decoder.py', python / 'onnxruntime/__init__.py', python / 'pypinyin/__init__.py']
        if not all(p.is_file() for p in required):
            return None
        return {'model': str(model), 'python': str(python), 'source': str(source)}

    def separation_config(self):
        root = self.studio / 'music'
        files = [root / 'deps/pymss/__init__.py', root / 'deps/pymss_core/__init__.py',
                 self.music_models / 'vocal/vocal_extraction/Kim_MelBandRoformer.ckpt',
                 self.music_models / 'vocal/vocal_extraction/Kim_MelBandRoformer.yaml',
                 self.music_models / 'reverb_echo_control/dereverb_deecho/dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt',
                 self.music_models / 'reverb_echo_control/dereverb_deecho/dereverb-echo_mel_band_roformer_sdr_13.4843_v2.yaml', self.python]
        if not self.gpu_installed() or not all(p.is_file() for p in files):
            return None
        return {'deps': str(root / 'deps'), 'models': str(self.music_models)}

    def transcription_config(self):
        for name in ('FireRedASR2-AED-FP16', 'FireRedASR2-AED'):
            model = self.models / name
            required = [model / x for x in ('model.pth.tar', 'dict.txt', 'cmvn.ark', 'train_bpe1000.model')]
            if self.python.is_file() and self.gpu_installed() and all(x.is_file() for x in required):
                return {'model': str(model)}
        return None

    def ying_config(self):
        root = Path(os.environ.get('AI_LYRICS_YING_ROOT', self.studio / 'ying')).resolve()
        python = root / '.venv/Scripts/python.exe'
        required = [python, root/'model.safetensors', root/'config.json', root/'local_runtime.py',
                    root/'src/YingMusicSinger/infer/YingMusicSinger.py']
        return {'root':str(root),'python':str(python)} if all(path.is_file() for path in required) else None

    def svc_config(self):
        root = self.component_home / 'svc/pretrained_models'
        wrapper = self.studio / 'components/svc/soulx_svc_model.py'
        required=[root/'SoulX-Singer/model-svc.pt',root/'whisper-base/config.json',root/'whisper-base/model.safetensors',wrapper,self.python,
                  root/'whisper-base/preprocessor_config.json',
                  self.core/'soulxsinger/models/soulxsinger.py',self.models/'SoulX-Singer-Preprocess/rmvpe/rmvpe.pt']
        if not self.gpu_installed() or not all(x.is_file() for x in required): return None
        return {'svc_root':str(root),'svc_wrapper':str(wrapper),'core':str(self.core),'models':str(self.models)}

    def status(self):
        files = {
            'singer': self.models / 'SoulX-Singer/model.pt',
            'pitch': self.models / 'SoulX-Singer-Preprocess/rmvpe/rmvpe.pt',
            'notes': self.models / 'SoulX-Singer-Preprocess/rosvot/rosvot/model.pt',
            'note_pitch': self.models / 'SoulX-Singer-Preprocess/rosvot/rmvpe/model.pt',
            'note_boundaries': self.models / 'SoulX-Singer-Preprocess/rosvot/rwbd/model.pt',
            'aligner': self.models / 'Qwen3-ForcedAligner-0.6B/model.safetensors',
            'python': self.python,
            'source': self.core / 'cli/inference.py',
            'worker_dependencies': ROOT / 'runtime/worker-deps/matplotlib/__init__.py',
        }
        checks = {name: path.is_file() for name, path in files.items()}
        checks['gpu_packages'] = self.gpu_installed()
        # A checkpoint alone is insufficient: missing tokenizer/config/RWBD files
        # must not make a newly downloaded environment appear usable.
        catalog = json.loads(Path(__file__).with_name('model_catalog.json').read_text(encoding='utf-8'))
        checks['model_files'] = all((self.models / item['path']).is_file()
                                   for item in catalog['files'] if item['component'] in ('singer', 'aligner'))
        return {'ready': all(checks.values()), 'checks': checks, 'model_root': str(self.models),
                'python': str(self.python), 'source_root': str(self.core), 'source_revision': UPSTREAM_COMMIT, 'singing_aligner': bool(self.hfa_config()),
                'limits': '每句 0.12–20 秒；支持中英歌词；多个原唱字可映射到一个英文词；请导入干声。'}

    def require(self):
        status = self.status()
        if not status['ready']:
            raise ValueError('模型环境不完整：' + ', '.join(k for k, v in status['checks'].items() if not v))
        return self
