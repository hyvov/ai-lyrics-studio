import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.jobs import RenderJobs
from backend.store import Store
from backend.svc_options import QUALITY_STEPS, svc_options


class SvcOptionsTests(unittest.TestCase):
    def test_quality_and_pitch_options_are_validated(self):
        self.assertEqual(svc_options(), {'quality':'standard','auto_shift':False})
        self.assertEqual([QUALITY_STEPS[x] for x in ('fast','standard','fine')],[16,32,64])
        for value in ({'quality':'unknown'},{'quality':[]},{'auto_shift':1},{'cfg':2}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                svc_options(value)

    def test_options_are_passed_to_worker_and_published(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); assets=root/'assets'; assets.mkdir(); store=Store(root/'projects')
            p=store.create({'name':'svc','duration':5,'lines':[]})
            p['assets']={'source':{'id':'source.wav','duration':5},'reference':{'id':'reference.wav','duration':5}}
            store._write(p)
            for name in ('source.wav','reference.wav'): (assets/name).write_bytes(b'x'*100)
            runtime=MagicMock();runtime.svc_config.return_value={'core':'core','models':'models','svc_root':'svc','svc_wrapper':'wrapper'}
            jobs=RenderJobs(store,assets,root/'jobs',runtime=runtime)
            options={'quality':'fine','auto_shift':True}
            with patch('backend.jobs.source_issue',return_value=None),patch('backend.jobs.threading.Thread'):
                job=jobs.submit_svc(p['id'],p['revision'],'source',options)
            directory=jobs.root/job['id'];config=json.loads((directory/'input.json').read_text(encoding='utf-8'))
            self.assertEqual(config['options'],options)
            (directory/'svc.wav').write_bytes(b'y'*100)
            (directory/'svc-result.json').write_text(json.dumps({'options':options,'pitch_shift':3}),encoding='utf-8')
            jobs._publish_svc(job,copy.deepcopy(p),directory)
            saved=store.get(p['id'])['svc']
            self.assertEqual(saved['options'],options);self.assertEqual(saved['pitch_shift'],3)


if __name__ == '__main__': unittest.main()
