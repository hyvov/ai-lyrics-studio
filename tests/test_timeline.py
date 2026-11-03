import copy,json,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.jobs import RenderJobs
from backend.store import Store
from backend.singing import fingerprint
from backend.timeline import alignment_fingerprint,cached_alignment,timings_to_slots,validate_timings

class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'projects')
        self.p=self.store.create({'name':'timeline','duration':4.,'lines':[{'id':'line1','original':'你好','text':'你早','start':0.,'end':4.,'mode':'melody','restore':False}]})
        self.timings=[{'text':'你','start':.2,'end':1.2},{'text':'好','start':1.5,'end':3.6}]

    def test_word_timing_cache_survives_new_lyrics_but_not_source_edits(self):
        p=copy.deepcopy(self.p);line=p['lines'][0]
        p['alignments']={'line1':{'fingerprint':alignment_fingerprint(p,line),'engine':'HubertFA','unit_timings':self.timings}}
        line['text']='你们';self.assertIsNotNone(cached_alignment(p,line))
        line['original']='你们';self.assertIsNone(cached_alignment(p,line))

    def test_invalid_word_axis_rejected(self):
        for timings in (self.timings[:1],[{**self.timings[0],'text':'错'},self.timings[1]],
                        [self.timings[0],{**self.timings[1],'start':1.}],[self.timings[0],{**self.timings[1],'end':4.1}]):
            with self.assertRaises(ValueError):validate_timings(self.p['lines'][0],timings)

    def test_manual_times_invalidate_render_fingerprint_and_drive_slots(self):
        before=fingerprint(self.p,self.p['lines'][0])
        self.p['alignments']={'line1':{'fingerprint':alignment_fingerprint(self.p,self.p['lines'][0]),'engine':'manual','unit_timings':self.timings}}
        self.assertNotEqual(before,fingerprint(self.p,self.p['lines'][0]))
        words,durations,indices=timings_to_slots(self.p['lines'][0],self.timings)
        self.assertEqual(words,['<SP>','你','<SP>','好','<SP>'])
        self.assertAlmostEqual(sum(durations),4.)
        self.assertEqual([x for x in indices if x is not None],[0,1])

    def test_api_original_correction_preserves_replacement_and_manual_roundtrip(self):
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']};pid=self.p['id']
            response=c.put(f'/api/projects/{pid}/segments/line1/timeline',headers=auth,json={'revision':1,'unit_timings':self.timings})
            self.assertEqual(response.status_code,200,response.text);p=response.json()
            self.assertEqual(p['lines'][0]['alignment_engine'],'manual')
            self.assertEqual(p['lines'][0]['unit_timings'],self.timings)
            response=c.put(f'/api/projects/{pid}/segments/line1/original',headers=auth,json={'revision':p['revision'],'text':'您好'})
            self.assertEqual(response.status_code,200,response.text);p=response.json()
            self.assertEqual(p['lines'][0]['original'],'您好');self.assertEqual(p['lines'][0]['text'],'你早')
            self.assertEqual(p['lines'][0]['unit_timings'],[])
            response=c.put(f'/api/projects/{pid}/segments/line1/timeline',headers=auth,json={'revision':1,'unit_timings':self.timings})
            self.assertEqual(response.status_code,409)

    def test_stale_alignment_result_does_not_overwrite_corrected_original(self):
        assets=self.root/'assets';assets.mkdir();jobs=RenderJobs(self.store,assets,self.root/'jobs')
        job={'id':'a'*32,'status':'running','warnings':[]};directory=self.root/'jobs'/job['id'];directory.mkdir()
        (directory/'alignment-result.json').write_text(json.dumps([{'segment_id':'line1','engine':'HubertFA','unit_timings':self.timings}]),encoding='utf-8')
        snapshot=copy.deepcopy(self.p);self.p['lines'][0]['original']='您好';self.store.update(self.p['id'],self.p)
        jobs._publish_alignment(job,snapshot,directory,final=True)
        self.assertEqual(job['status'],'stale');self.assertNotIn('alignments',self.store.get(self.p['id']))

if __name__=='__main__':unittest.main()
