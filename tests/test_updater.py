import io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.updater import Updater

class Response(io.BytesIO):
    pass

class UpdateTests(unittest.TestCase):
    def test_check_only_reports_new_version_without_download(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'release.json').write_text(json.dumps({'version':'0.2.2'}))
            class Jobs:active=None
            updater=Updater(root,root/'data',Jobs())
            with patch('backend.updater.urllib.request.urlopen',return_value=Response(b'{"tag_name":"v0.2.3"}')) as fetch:
                updater.run(download=False)
            self.assertEqual(updater.snapshot()['status'],'available')
            self.assertEqual(fetch.call_count,1)
            self.assertFalse((root/'data/updates').exists())

    def test_install_rejects_changed_file_and_active_download(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);folder=root/'data/updates';folder.mkdir(parents=True)
            path=folder/'update.exe';path.write_bytes(b'changed')
            class Jobs:active=None
            class Models:
                def snapshot(self):return {'status':'running'}
            updater=Updater(root,root/'data',Jobs(),Models())
            updater.update(status='ready',path=str(path),sha256='0'*64)
            with self.assertRaisesRegex(ValueError,'模型任务或模型下载'):updater.install()
            updater.model_setup=None
            with self.assertRaisesRegex(ValueError,'发生变化'):updater.install()

    def test_new_release_download_is_hash_checked(self):
        import hashlib
        data=b'installer';digest=hashlib.sha256(data).hexdigest()
        metadata={'tag_name':'v0.2.3','assets':[{'name':'CiSheng-Studio-0.2.3-setup.exe','state':'uploaded','size':len(data),'digest':'sha256:'+digest,'browser_download_url':'https://github.com/hyvov/ai-lyrics-studio/releases/download/v0.2.3/CiSheng-Studio-0.2.3-setup.exe'}]}
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'release.json').write_text(json.dumps({'version':'0.2.2'}))
            class Jobs:active=None
            updater=Updater(root,root/'data',Jobs())
            with patch('backend.updater.urllib.request.urlopen',side_effect=[Response(json.dumps(metadata).encode()),Response(data)]):updater.run()
            state=updater.snapshot();self.assertEqual(state['status'],'ready');self.assertEqual(Path(state['path']).read_bytes(),data)
    def test_bad_hash_never_marks_ready(self):
        metadata={'tag_name':'v0.2.3','assets':[{'name':'CiSheng-Studio-0.2.3-setup.exe','state':'uploaded','size':3,'digest':'sha256:'+'0'*64,'browser_download_url':'https://github.com/hyvov/ai-lyrics-studio/releases/download/v0.2.3/CiSheng-Studio-0.2.3-setup.exe'}]}
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'release.json').write_text(json.dumps({'version':'0.2.2'}))
            class Jobs:active=None
            updater=Updater(root,root/'data',Jobs())
            with patch('backend.updater.urllib.request.urlopen',side_effect=[Response(json.dumps(metadata).encode()),Response(b'bad')]):updater.run()
            self.assertEqual(updater.snapshot()['status'],'failed');self.assertFalse(list((root/'data/updates').glob('*.exe')))
