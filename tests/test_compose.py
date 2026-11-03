import copy
import tempfile
import unittest
from pathlib import Path

from backend.jobs import RenderJobs
from backend.singing import composition_fingerprint,fingerprint
from backend.store import Store


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.assets=root/'assets';self.assets.mkdir()
        self.store=Store(root/'projects');self.jobs=RenderJobs(self.store,self.assets,root/'jobs')
        self.p=self.store.create({'name':'compose','duration':5,'lines':[{'id':'a','original':'你好','text':'你早','start':0.,'end':3.,'mode':'melody','restore':False}]})
        self.p['assets']={'source':{'id':'source.wav','duration':5},'accompaniment':{'id':'back.wav','duration':5}}
        self.p['renders']={'a':{'asset_id':'take.wav','fingerprint':fingerprint(self.p,self.p['lines'][0])}}
        self.store._write(self.p)
        self.job={'id':'d'*32,'status':'running','warnings':[]};self.dir=root/'jobs'/self.job['id'];self.dir.mkdir()
        (self.dir/'rendered.wav').write_bytes(b'x'*100);(self.dir/'mixed.wav').write_bytes(b'y'*100)

    def test_compose_publishes_both_outputs_and_preserves_take(self):
        self.jobs._publish_compose(self.job,copy.deepcopy(self.p),self.dir)
        p=self.store.get(self.p['id']);self.assertEqual(self.job['status'],'succeeded')
        self.assertEqual(p['renders'],self.p['renders']);self.assertEqual(p['composite']['fingerprint'],composition_fingerprint(p))
        self.assertEqual((self.assets/p['composite']['mixed']).read_bytes(),b'y'*100)

    def test_stale_song_or_take_does_not_publish(self):
        snapshot=copy.deepcopy(self.p);self.p['renders']['a']['asset_id']='new-take.wav';self.store._write(self.p)
        self.jobs._publish_compose(self.job,snapshot,self.dir)
        self.assertEqual(self.job['status'],'stale');self.assertNotIn('composite',self.store.get(self.p['id']))

    def test_missing_mix_does_not_partially_publish(self):
        (self.dir/'mixed.wav').unlink()
        with self.assertRaises(ValueError):self.jobs._publish_compose(self.job,copy.deepcopy(self.p),self.dir)
        self.assertEqual(list(self.assets.iterdir()),[]);self.assertNotIn('composite',self.store.get(self.p['id']))


if __name__=='__main__':unittest.main()
