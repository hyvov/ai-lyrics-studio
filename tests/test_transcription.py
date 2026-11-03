import copy
import json
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.jobs import RenderJobs
from backend.store import Store
from backend.transcribe_worker import result_drafts


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.assets=root/'assets';self.assets.mkdir()
        self.store=Store(root/'projects');self.jobs=RenderJobs(self.store,self.assets,root/'jobs')
        self.project=self.store.create({'name':'asr','duration':10,'lines':[]})
        self.project['assets']['source']={'id':'source.wav','duration':10};self.store._write(self.project)
        self.job={'id':'b'*32,'status':'running','warnings':[]};self.dir=root/'jobs'/self.job['id'];self.dir.mkdir()
        (self.dir/'transcription-result.json').write_text(json.dumps([{'text':'你好','start':1,'end':3}]),encoding='utf-8')

    def test_draft_publishes_as_editable_original_and_text(self):
        self.jobs._publish_transcription(self.job,copy.deepcopy(self.project),self.dir)
        saved=self.store.get(self.project['id'])
        self.assertEqual(self.job['status'],'succeeded');self.assertEqual(saved['revision'],2)
        self.assertEqual((saved['lines'][0]['original'],saved['lines'][0]['text']),('你好','你好'))
        self.assertTrue(saved['lines'][0]['needs_review'])

    def test_later_lyrics_edit_is_never_overwritten(self):
        snapshot=copy.deepcopy(self.project)
        self.project['lines']=[dict(id='new',original='你好',text='早安',start=1,end=3,mode='melody',restore=False)]
        self.store._write(self.project)
        self.jobs._publish_transcription(self.job,snapshot,self.dir)
        self.assertEqual(self.job['status'],'stale')
        self.assertEqual(self.store.get(self.project['id'])['lines'][0]['text'],'早安')

    def test_bad_result_does_not_replace_existing_project(self):
        (self.dir/'transcription-result.json').write_text(json.dumps([{'text':'你好','start':9,'end':12}]),encoding='utf-8')
        with self.assertRaises(ValueError):self.jobs._publish_transcription(self.job,copy.deepcopy(self.project),self.dir)
        self.assertEqual(self.store.get(self.project['id'])['lines'],[])

    def test_recognized_pause_splits_phrases_without_overlap(self):
        result={'timestamp':[('你',.2,.4),('好',.4,.6),('世',.6,.8),('界',.8,1.0),('再',1.6,1.8),('见',1.8,2.0)]}
        parts=result_drafts(result,3.,6.)
        self.assertEqual([x['text'] for x in parts],['你好世界','再见'])
        self.assertLessEqual(parts[0]['end'],parts[1]['start'])
        self.assertGreaterEqual(parts[0]['start'],3.)
        self.assertLessEqual(parts[1]['end'],6.)

    def test_confirm_original_clears_review_flag(self):
        self.jobs._publish_transcription(self.job,copy.deepcopy(self.project),self.dir)
        p=self.store.get(self.project['id']);line=p['lines'][0]
        with TestClient(create_app(self.store.root.parent),base_url='http://127.0.0.1:18733') as client:
            auth={'x-studio-token':client.get('/api/session').json()['token']}
            response=client.put(f"/api/projects/{p['id']}/segments/{line['id']}/original",headers=auth,json={'revision':p['revision'],'text':line['original']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertFalse(response.json()['lines'][0]['needs_review'])


if __name__ == '__main__':unittest.main()
