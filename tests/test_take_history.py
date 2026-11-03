import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.singing import composition_fingerprint,fingerprint
from backend.store import Store


class TakeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);assets=self.root/'assets';assets.mkdir()
        self.store=Store(self.root/'projects')
        self.p=self.store.create({'name':'takes','duration':5,'lines':[{'id':'a','original':'你好','text':'你早','start':0.,'end':3.,'mode':'melody','restore':False}]})
        self.ids=[character*32+'.wav' for character in 'abc']
        for asset_id in self.ids:(assets/asset_id).write_bytes(b'x'*100)
        self.p['assets']={'source':{'id':'source.wav','duration':5}}
        fp=fingerprint(self.p,self.p['lines'][0])
        self.p['render_history']=[{'segment_id':'a','asset_id':self.ids[0],'fingerprint':fp,'job_id':'job1','warnings':[]},
                                  {'segment_id':'a','asset_id':self.ids[1],'fingerprint':fp,'job_id':'job2','warnings':[]},
                                  {'segment_id':'a','asset_id':self.ids[2],'fingerprint':'stale','job_id':'job3','warnings':[]}]
        self.p['renders']={'a':dict(self.p['render_history'][1])}
        self.p['composite']={'fingerprint':composition_fingerprint(self.p),'rendered':'old.wav'}
        self.store._write(self.p)

    def test_history_switch_only_allows_compatible_and_invalidates_mix(self):
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']};base=f"/api/projects/{self.p['id']}/segments/a/takes"
            result=c.get(base).json()['takes']
            self.assertEqual(len(result),3);self.assertFalse(result[0]['compatible']);self.assertTrue(result[1]['current'])
            self.assertEqual(c.post(base+'/'+self.ids[2]+'/activate',headers=auth,json={'revision':1}).status_code,409)
            response=c.post(base+'/'+self.ids[0]+'/activate',headers=auth,json={'revision':1})
            self.assertEqual(response.status_code,200,response.text);p=response.json()
            self.assertEqual(p['renders']['a']['asset_id'],self.ids[0]);self.assertFalse(p['rendered_available'])
            self.assertEqual(len(p['render_history']),3)
            self.assertEqual(c.post(base+'/'+self.ids[1]+'/activate',headers=auth,json={'revision':1}).status_code,409)


if __name__=='__main__':unittest.main()
