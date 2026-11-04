import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from backend.jobs import RenderJobs
from backend.singing import validate_render
from backend.store import Store


class YingModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.assets=root/'assets';self.assets.mkdir();self.store=Store(root/'projects')
        self.p=self.store.create({'name':'test','duration':8,'lines':[
            {'id':'a','original':'你好','text':'你真的好','start':0.,'end':4.,'mode':'ying','restore':False},
            {'id':'b','original':'晚风','text':'晨光','start':4.,'end':8.,'mode':'score','restore':False}]})
        self.p['assets']={'source':{'id':'source.wav','duration':8}};self.store._write(self.p)
        runtime=MagicMock();runtime.core=root;runtime.models=root;runtime.hfa_config.return_value=None
        runtime.ying_config.return_value={'root':str(root),'python':'local-python'}
        self.jobs=RenderJobs(self.store,self.assets,root/'jobs',runtime)

    def test_different_length_is_accepted_only_for_ying(self):
        self.assertEqual(len(validate_render(self.p,['a'])),1)
        p=dict(self.p);p['lines']=[dict(self.p['lines'][0],mode='score')]
        with self.assertRaises(ValueError):validate_render(p,['a'])

    def test_ying_job_uses_local_model_and_mixed_jobs_are_rejected(self):
        with patch('backend.jobs.source_issue',return_value=None),patch('backend.jobs.threading.Thread'):
            with self.assertRaisesRegex(ValueError,'分批'):
                self.jobs.submit(self.p['id'],self.p['revision'],['a','b'])
            job=self.jobs.submit(self.p['id'],self.p['revision'],['a'])
        import json
        config=json.loads((self.jobs.root/job['id']/'input.json').read_text(encoding='utf-8'))
        self.assertEqual(config['ying']['python'],'local-python')
        self.assertEqual(config['segments'][0]['mode'],'ying')


if __name__=='__main__':unittest.main()
