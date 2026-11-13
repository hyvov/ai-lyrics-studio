import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.runtime import ROOT, Runtime
from scripts.import_local_runtime import import_runtime

class IndependentRuntimeTests(unittest.TestCase):
    def test_default_ignores_studio_overrides(self):
        with patch.dict(os.environ, {'SOULX_STUDIO_HOME':'Z:/old', 'SOULX_PYTHON':'Z:/old/python.exe'}, clear=True):
            runtime = Runtime.discover()
            self.assertEqual(runtime.studio, (ROOT / 'runtime/independent').resolve())
            self.assertEqual(runtime.python, (runtime.studio / 'python/python.exe').resolve())

    def test_explicit_independent_override(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AI_LYRICS_RUNTIME_HOME':tmp}, clear=True):
            runtime = Runtime.discover()
            self.assertEqual(runtime.models, (Path(tmp) / 'pretrained_models').resolve())

    def test_import_copies_without_user_audio_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'studio'; target = root / 'new'
            def write(rel, value=b'x'):
                path = source / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
            write('runtime/current.json', json.dumps({'version':'v1'}).encode())
            write('runtime/components/svc/current.json', json.dumps({'version':'s1'}).encode())
            for rel in ['runtime/versions/v1/python/python.exe', 'runtime/versions/v1/core/pretrained_models/model.pt',
                        'runtime/components/hfa/versions/1.0.0/model/model.onnx',
                        'runtime/components/svc/versions/s1/pretrained_models/model.pt', 'backend/soulx_svc_model.py',
                        'data/music/deps/pymss/__init__.py', 'data/music/models/model.pt', 'data/music/private.wav']:
                write(rel)
            import_runtime(source, target)
            self.assertTrue((target/'python/python.exe').is_file())
            self.assertFalse((target/'music/private.wav').exists())
            self.assertTrue((source/'data/music/private.wav').is_file())
            with self.assertRaises(FileExistsError): import_runtime(source, target)
            self.assertTrue((target/'manifest.json').is_file())

if __name__ == '__main__': unittest.main()
