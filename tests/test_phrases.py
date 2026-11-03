import copy
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.phrases import merge_next, split_phrase
from backend.store import Store


class PhraseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'projects')
        self.p=self.store.create({'name':'phrases','duration':8,'lines':[
            {'id':'a','original':'你好，world friend','text':'早安，hello friend','start':0.,'end':4.,'mode':'melody','restore':False,'needs_review':True},
            {'id':'b','original':'再见','text':'再会','start':5.,'end':8.,'mode':'melody','restore':False}]})

    def test_split_keeps_original_and_changed_lyrics_and_review(self):
        p=copy.deepcopy(self.p);split_phrase(p,'a',2,2.)
        self.assertEqual([(x['original'],x['text']) for x in p['lines'][:2]],[('你好，','早安，'),('world friend','hello friend')])
        self.assertEqual(p['lines'][0]['end'],p['lines'][1]['start'])
        self.assertTrue(p['lines'][0]['needs_review']);self.assertTrue(p['lines'][1]['needs_review'])
        self.assertNotEqual(p['lines'][0]['id'],p['lines'][1]['id'])

    def test_merge_joins_english_words_and_rejects_mismatched_modes(self):
        p=copy.deepcopy(self.p);split_phrase(p,'a',2,2.);merge_next(p,p['lines'][1]['id'])
        self.assertEqual(p['lines'][1]['original'],'world friend再见')
        p['lines'][1]['mode']='score'
        with self.assertRaises(ValueError):merge_next(p,'a')

    def test_api_rejects_invalid_split_without_changing_project(self):
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']}
            url=f"/api/projects/{self.p['id']}/segments/a/structure"
            value={'revision':1,'action':'split','position':2,'time':.05}
            self.assertEqual(c.post(url,headers=auth,json=value).status_code,400)
            self.assertEqual(c.get(f"/api/projects/{self.p['id']}").json()['revision'],1)
            value['time']=2.
            result=c.post(url,headers=auth,json=value)
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(len(result.json()['lines']),3)
            self.assertEqual(c.post(url,headers=auth,json=value).status_code,409)


if __name__=='__main__':unittest.main()
