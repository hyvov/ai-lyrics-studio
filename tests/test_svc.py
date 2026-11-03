import copy
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.jobs import RenderJobs
from backend.singing import composition_fingerprint,svc_fingerprint
from backend.store import Store


class SvcTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.assets=self.root/'assets';self.assets.mkdir()
        self.store=Store(self.root/'projects')
        self.p=self.store.create({'name':'svc','duration':5,'lines':[]})
        self.p['assets']={'source':{'id':'source.wav','duration':5},'reference':{'id':'reference.wav','duration':5}}
        self.store._write(self.p);self.jobs=RenderJobs(self.store,self.assets,self.root/'jobs')
        self.job={'id':'c'*32,'status':'running','target_kind':'source','warnings':[]}
        self.dir=self.root/'jobs'/self.job['id'];self.dir.mkdir();(self.dir/'svc.wav').write_bytes(b'x'*100)

    def test_publish_available_and_stale_after_reference_change(self):
        self.jobs._publish_svc(self.job,copy.deepcopy(self.p),self.dir)
        saved=self.store.get(self.p['id']);self.assertEqual(self.job['status'],'succeeded')
        self.assertEqual((self.assets/saved['svc']['asset_id']).read_bytes(),b'x'*100)
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            p=c.get(f"/api/projects/{self.p['id']}").json();self.assertTrue(p['svc_available'])
            self.assertEqual(c.get(f"/api/projects/{self.p['id']}/exports/svc").status_code,200)
            saved['assets']['reference']['id']='different.wav';self.store._write(saved)
            self.assertFalse(c.get(f"/api/projects/{self.p['id']}").json()['svc_available'])
            self.assertEqual(c.get(f"/api/projects/{self.p['id']}/exports/svc").status_code,409)

    def test_changed_reference_does_not_publish_old_conversion(self):
        snapshot=copy.deepcopy(self.p);self.p['assets']['reference']['id']='new.wav';self.store._write(self.p)
        self.jobs._publish_svc(self.job,snapshot,self.dir)
        self.assertEqual(self.job['status'],'stale');self.assertNotIn('svc',self.store.get(self.p['id']))
        self.assertEqual(list(self.assets.iterdir()),[])

    def test_reference_does_not_expire_normal_composition(self):
        old=copy.deepcopy(self.p);old['assets'].pop('reference')
        self.assertEqual(composition_fingerprint(old),composition_fingerprint(self.p))
        self.assertNotEqual(svc_fingerprint(old,'source'),svc_fingerprint(self.p,'source'))

    def test_mix_expires_when_backing_changes_but_voice_remains(self):
        self.p['assets']['accompaniment']={'id':'backing.wav','duration':5};self.store._write(self.p)
        (self.dir/'svc-mixed.wav').write_bytes(b'y'*100)
        self.jobs._publish_svc(self.job,copy.deepcopy(self.p),self.dir)
        saved=self.store.get(self.p['id'])
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            p=c.get(f"/api/projects/{self.p['id']}").json()
            self.assertTrue(p['svc_mixed_available'])
            self.assertEqual(c.get(f"/api/projects/{self.p['id']}/exports/svc_mixed").status_code,200)
            saved['assets']['accompaniment']['id']='new-backing.wav';self.store._write(saved)
            p=c.get(f"/api/projects/{self.p['id']}").json()
            self.assertTrue(p['svc_available']);self.assertFalse(p['svc_mixed_available'])
            self.assertEqual(c.get(f"/api/projects/{self.p['id']}/exports/svc_mixed").status_code,409)


if __name__=='__main__':unittest.main()
