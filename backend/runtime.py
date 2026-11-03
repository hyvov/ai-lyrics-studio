"""Locate shared, read-only model weights and an independent upstream checkout."""
from __future__ import annotations
import json
import os
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

    @classmethod
    def discover(cls):
        studio = Path(os.environ.get('SOULX_STUDIO_HOME', Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SoulX Studio')).resolve()
        try:
            version = json.loads((studio / 'runtime/current.json').read_text(encoding='utf-8-sig'))['version']
        except (OSError, ValueError, KeyError):
            version = ''
        # Version names must remain within the installed runtime directory.
        if not version or Path(version).name != version or '/' in version or '\\' in version:
            version = '_missing_'
        installed = studio / 'runtime/versions' / version
        return cls(studio, Path(os.environ.get('SOULX_PYTHON', installed / 'python/python.exe')).resolve(),
                   Path(os.environ.get('SOULX_SOURCE_ROOT', ROOT / 'runtime/SoulX-Singer')).resolve(),
                   Path(os.environ.get('SOULX_MODELS_ROOT', installed / 'core/pretrained_models')).resolve())

    def hfa_config(self):
        model = Path(os.environ.get('SOULX_HFA_MODEL', self.studio / 'runtime/components/hfa/versions/1.0.0/model')).resolve()
        python = Path(os.environ.get('SOULX_HFA_PYTHON', model.parent / 'python')).resolve()
        source = Path(os.environ.get('SOULX_HFA_SOURCE', ROOT / 'runtime/Vocal2Midi')).resolve()
        required = [model / 'model.onnx', model / 'vocab.json', model / 'config.json', model / 'VERSION',
                    source / 'inference/HubertFA/tools/decoder.py', python / 'onnxruntime/__init__.py', python / 'pypinyin/__init__.py']
        if not all(p.is_file() for p in required):
            return None
        return {'model': str(model), 'python': str(python), 'source': str(source)}

    def status(self):
        files = {
            'singer': self.models / 'SoulX-Singer/model.pt',
            'pitch': self.models / 'SoulX-Singer-Preprocess/rmvpe/rmvpe.pt',
            'notes': self.models / 'SoulX-Singer-Preprocess/rosvot/rosvot/model.pt',
            'note_pitch': self.models / 'SoulX-Singer-Preprocess/rosvot/rmvpe/model.pt',
            'aligner': self.models / 'Qwen3-ForcedAligner-0.6B/model.safetensors',
            'python': self.python,
            'source': self.core / 'cli/inference.py',
            'worker_dependencies': ROOT / 'runtime/worker-deps/matplotlib/__init__.py',
        }
        checks = {name: path.is_file() for name, path in files.items()}
        return {'ready': all(checks.values()), 'checks': checks, 'model_root': str(self.models),
                'python': str(self.python), 'source_root': str(self.core), 'source_revision': UPSTREAM_COMMIT, 'singing_aligner': bool(self.hfa_config()),
                'limits': '每句 1–20 秒；中英歌词；新旧发音单位数量相同；请导入干声。'}

    def require(self):
        status = self.status()
        if not status['ready']:
            raise ValueError('模型环境不完整：' + ', '.join(k for k, v in status['checks'].items() if not v))
        return self
