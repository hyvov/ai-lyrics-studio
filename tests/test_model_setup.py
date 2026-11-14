import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from backend.model_setup import download_models, ModelSetup
from unittest.mock import patch

class Response(io.BytesIO):
    def __init__(self,content,status=200,headers=None):
        super().__init__(content); self.status=status; self.headers=headers or {}
    def getcode(self): return self.status

class ModelDownloadTests(unittest.TestCase):
    def test_resume_and_verify_then_skip_existing(self):
        data=b'abcdef'; item={'path':'Singer/model.pt','url':'https://example.test/file','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Singer').mkdir();(root/'Singer/model.pt.download').write_bytes(data[:3]);calls=[];states=[]
            def open_url(req,timeout):
                calls.append(req.get_header('Range'));return Response(data[3:],206,{'Content-Range':'bytes 3-5/6'})
            download_models(root,[item],lambda **x: states.append(x),open_url)
            self.assertEqual(calls,['bytes=3-']);self.assertEqual((root/'Singer/model.pt').read_bytes(),data)
            download_models(root,[item],lambda **x:None,lambda *x,**y:self.fail('valid model downloaded twice'))
            self.assertTrue(any(x.get('stage')=='verifying' for x in states))
    def test_mismatched_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'model';p.write_bytes(b'old')
            with self.assertRaisesRegex(ValueError,'已保留原文件'):
                download_models(root,[{'path':'model','url':'https://example.test','size':3,'sha256':'0'*64}],lambda **x:None)
            self.assertEqual(p.read_bytes(),b'old')
    def test_corrupt_download_is_not_installed_and_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);item={'path':'model','url':'https://example.test','size':3,'sha256':hashlib.sha256(b'yes').hexdigest()}
            with self.assertRaisesRegex(ValueError,'校验失败'):
                download_models(root,[item],lambda **x:None,lambda *x,**y:Response(b'bad'))
            self.assertFalse((root/'model').exists());self.assertEqual(len(list(root.glob('*.invalid-*'))),1)
            download_models(root,[item],lambda **x:None,lambda *x,**y:Response(b'yes'))
            self.assertEqual((root/'model').read_bytes(),b'yes')
    def test_component_selection_and_concurrent_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup=ModelSetup(tmp)
            for selection in ([], ['bogus'], ['singer','singer'], 'singer'):
                with self.assertRaises(ValueError): setup.start(selection)
            with patch('backend.model_setup.threading.Thread') as thread:
                setup.start(['aligner'])
                self.assertEqual(thread.call_args.kwargs['args'], (('aligner',),))
                with self.assertRaises(ValueError): setup.start(['singer'])

    def test_svc_model_uses_separate_root(self):
        data = b'voice'
        item = {'path':'SoulX-Singer/model-svc.pt','url':'https://example.test',
                'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'root':'svc'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); models=root/'models'; svc=root/'svc'
            download_models(models,[item],lambda **x:None,lambda *x,**y:Response(data),roots={'models':models,'svc':svc})
            self.assertEqual((svc/item['path']).read_bytes(),data)
            self.assertFalse((models/item['path']).exists())
            with self.assertRaises(ValueError): download_models(models,[item],lambda **x:None)

    def test_reject_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            download_models(Path(tmp),[{'path':'../escape','url':'https://example.test','size':1,'sha256':'0'*64}],lambda **x:None)

if __name__=='__main__':unittest.main()
