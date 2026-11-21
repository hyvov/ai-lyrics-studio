import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.gpu_setup import extract_wheel
from backend.model_setup import download_models, ModelSetup, DownloadCancelled
from backend.runtime import Runtime
from desktop.launcher import configure_installation, python_path, installed_data_home


class ReleaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows installation configuration')
    def test_selected_unicode_storage_is_read_and_used(self):
        import ctypes
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clear=False):
            os.environ.pop('CISHENG_USER_HOME', None)
            root=Path(tmp)/'program';root.mkdir();(root/'release.json').write_text('{}')
            home=Path(tmp)/'音乐数据';config=root/'storage.ini'
            self.assertTrue(ctypes.windll.kernel32.WritePrivateProfileStringW('Storage','Home',str(home),str(config)))
            self.assertEqual(installed_data_home(root),home.resolve())
            self.assertEqual(configure_installation(root),home.resolve()/'data')
            self.assertEqual(Runtime.discover().models,home.resolve()/'models')

    def test_installed_storage_is_outside_programs_and_does_not_use_developer_models(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clear=False):
            root = Path(tmp) / 'program'; root.mkdir(); (root/'release.json').write_text('{}')
            home = Path(tmp) / 'user'; os.environ['CISHENG_USER_HOME'] = str(home)
            os.environ['AI_LYRICS_MODELS_ROOT'] = 'Z:/development/models'
            data = configure_installation(root); runtime = Runtime.discover()
            self.assertEqual(data, home / 'data')
            self.assertEqual(runtime.models, home / 'models')
            self.assertEqual(runtime.component_home, home / 'components')
            self.assertFalse(data.is_relative_to(root))
            self.assertFalse(runtime.gpu_installed())

    def test_bundled_python_is_used_without_system_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); python=root/'runtime/independent/python/python.exe'
            python.parent.mkdir(parents=True); python.touch()
            with patch('desktop.launcher.shutil.which', return_value=None):
                self.assertEqual(python_path(root),str(python))

    def test_wheel_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/'bad.whl'
            with zipfile.ZipFile(archive,'w') as wheel:wheel.writestr('../escape.txt',b'bad')
            with self.assertRaises(ValueError):extract_wheel(archive,root/'site',lambda **x:None)
            self.assertFalse((root/'escape.txt').exists())

    def test_repair_preserves_bad_original_and_installs_only_verified_file(self):
        class Response(io.BytesIO):
            status=200
            headers={}
            def getcode(self):return self.status
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'model.pt').write_bytes(b'bad')
            item={'path':'model.pt','url':'https://example.invalid/model','size':4,'sha256':hashlib.sha256(b'good').hexdigest()}
            download_models(root,[item],lambda **x:None,lambda *a,**k:Response(b'good'),repair=True)
            self.assertEqual((root/'model.pt').read_bytes(),b'good')
            self.assertEqual(next(root.glob('model.pt.invalid-*')).read_bytes(),b'bad')

    def test_cancel_keeps_partial_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);part=root/'model.download';part.write_bytes(b'partial')
            setup=ModelSetup(root);setup.cancel()
            with self.assertRaises(DownloadCancelled):setup.progress(stage='downloading')
            self.assertEqual(part.read_bytes(),b'partial')


if __name__ == '__main__':unittest.main()
