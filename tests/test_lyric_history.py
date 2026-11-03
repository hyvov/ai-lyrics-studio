import copy
import json
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.jobs import RenderJobs
from backend.store import Store


class LyricHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'projects')
        self.p=self.store.create({'name':'history','duration':5,'lines':[{'id':'a','original':'你好','text':'你早','start':0.,'end':3.,'mode':'melody','restore':False}]})
        self.p['assets']['source']={'id':'source.wav','duration':5};self.store._write(self.p)

    def test_import_and_restore_preserve_both_lyric_versions(self):
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']};base=f"/api/projects/{self.p['id']}"
            invalid=c.post(base+'/lyrics',headers=auth,json={'revision':1,'format':'srt','text':'broken'})
            self.assertEqual(invalid.status_code,400);self.assertEqual(c.get(base+'/lyric-history').json()['history'],[])
            response=c.post(base+'/lyrics',headers=auth,json={'revision':1,'format':'srt','text':'1\n00:00:00,000 --> 00:00:03,000\n再见\n'})
            self.assertEqual(response.status_code,200,response.text);new=response.json()
            self.assertNotIn('lyric_history',new)
            history=c.get(base+'/lyric-history').json()['history'];self.assertEqual(history[0]['preview'],'你早')
            restored=c.post(base+'/lyric-history/'+history[0]['id']+'/restore',headers=auth,json={'revision':new['revision']})
            self.assertEqual(restored.status_code,200,restored.text);self.assertEqual(restored.json()['lines'][0]['text'],'你早')
            self.assertEqual(c.get(base+'/lyric-history').json()['history'][0]['preview'],'再见')
            self.assertEqual(c.post(base+'/lyric-history/'+history[0]['id']+'/restore',headers=auth,json={'revision':1}).status_code,409)

    def test_cannot_restore_history_to_changed_source(self):
        self.store.replace_lyrics(self.p['id'],1,[{**self.p['lines'][0],'text':'再见'}],'测试')
        p=self.store.get(self.p['id']);snapshot=p['lyric_history'][0]['id'];p['assets']['source']['id']='other.wav';self.store._write(p)
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']};base=f"/api/projects/{p['id']}"
            self.assertFalse(c.get(base+'/lyric-history').json()['history'][0]['compatible'])
            self.assertEqual(c.post(base+'/lyric-history/'+snapshot+'/restore',headers=auth,json={'revision':p['revision']}).status_code,409)

    def test_transcription_saves_replaced_lyrics(self):
        assets=self.root/'assets';assets.mkdir();jobs=RenderJobs(self.store,assets,self.root/'jobs')
        job={'id':'a'*32,'status':'running','warnings':[]};directory=self.root/'jobs'/job['id'];directory.mkdir()
        (directory/'transcription-result.json').write_text(json.dumps([{'text':'再见','start':0,'end':3}]),encoding='utf-8')
        jobs._publish_transcription(job,copy.deepcopy(self.p),directory)
        p=self.store.get(self.p['id']);self.assertEqual(p['lines'][0]['text'],'再见')
        self.assertEqual(p['lyric_history'][0]['lines'][0]['text'],'你早')


if __name__=='__main__':unittest.main()
