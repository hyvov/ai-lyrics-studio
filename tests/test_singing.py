import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from backend.singing import repair_score_pitches

from backend.jobs import RenderJobs
from backend.singing import units, fingerprint, composition_fingerprint, repair_alignment, validate_render
from backend.store import Store


class SingingTests(unittest.TestCase):
    def test_score_pitch_repair_uses_closest_voiced_note(self):
        pitches,repaired=repair_score_pitches([0,60,0,67,0],[1,2,2,3,2],[.2,.3,.1,.5,.2])
        self.assertEqual(pitches,[0,60,60,67,67]);self.assertEqual(repaired,2)
        with self.assertRaises(ValueError):repair_score_pitches([0,0],[1,2],[.2,.2])

    def test_short_phrase_is_accepted_down_to_120_milliseconds(self):
        p=copy.deepcopy(self.p);p['lines'][0]['start']=.7;p['lines'][0]['end']=.82
        self.assertEqual(len(validate_render(p,['line1'])),1)
        p['lines'][0]['end']=.819
        with self.assertRaises(ValueError):validate_render(p,['line1'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'projects')
        self.assets = self.root / 'assets'; self.assets.mkdir()
        self.p = self.store.create({'name':'test','duration':4,'lines':[{
            'id':'line1','original':'晚风','text':'晨光','start':0.,'end':4.,'mode':'melody','restore':False}]})
        self.p = self.store.attach(self.p['id'], 'source', {'id':'source.wav'}, 1)
        self.jobs = RenderJobs(self.store, self.assets, self.root / 'jobs')

    def result(self):
        job = {'id':'a'*32,'status':'running','segment_ids':['line1'],'warnings':[]}
        directory = self.root/'jobs'/job['id']; directory.mkdir()
        file = directory/'line1-generated.wav'
        with wave.open(str(file),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','none'));f.writeframes(b'\0\0'*96000)
        (directory/'rendered.wav').write_bytes(file.read_bytes())
        (directory/'result.json').write_text(json.dumps([{'segment_id':'line1','path':str(file)}]),encoding='utf-8')
        return job,directory

    def test_units_and_equal_length_requirement(self):
        self.assertEqual(units('你好，hello world!'), ['你','好','hello','world'])
        self.assertEqual(units('<SP>你 好<SP>'), ['你','好'])
        with self.assertRaises(ValueError): units('123')
        self.assertEqual(len(validate_render(self.p,['line1'])),1)
        p=copy.deepcopy(self.p);p['lines'][0]['text']='晨光明'
        with self.assertRaises(ValueError):validate_render(p,['line1'])
        with self.assertRaises(ValueError):validate_render(self.p,[{}])

    def test_segment_id_cannot_escape_job_directory(self):
        p=copy.deepcopy(self.p);p['lines'][0]['id']='../escape'
        with self.assertRaises(ValueError):validate_render(p,['../escape'])
        with self.assertRaises(ValueError):self.store.update(p['id'],p)

    def test_fingerprint_changes_on_source_and_lyrics(self):
        initial=fingerprint(self.p,self.p['lines'][0]); p=copy.deepcopy(self.p)
        p['lines'][0]['start']=0;p['lines'][0]['end']=4
        self.assertEqual(initial,fingerprint(p,p['lines'][0]))
        p['name']='renamed'
        self.assertEqual(initial,fingerprint(p,p['lines'][0]))
        p['lines'][0]['text']='夜雨'
        self.assertNotEqual(initial,fingerprint(p,p['lines'][0]))
        p=copy.deepcopy(self.p);p['assets']['source']['id']='different.wav'
        self.assertNotEqual(initial,fingerprint(p,p['lines'][0]))

    def test_short_alignment_repair_and_rejection(self):
        result,count=repair_alignment([(0,.4),(.4,.4),(.4,1.)],1.)
        self.assertEqual(count,1);self.assertGreater(result[1][1]-result[1][0],.02)
        self.assertLessEqual(result[1][1],result[2][0])
        with self.assertRaises(ValueError):repair_alignment([(0,0),(0,0),(0,0)],1)

    def test_publish_and_composite_reference(self):
        job,directory=self.result(); self.jobs._publish(job,copy.deepcopy(self.p),directory)
        current=self.store.get(self.p['id'])
        self.assertEqual(job['status'],'succeeded')
        self.assertEqual(current['renders']['line1']['fingerprint'],fingerprint(current,current['lines'][0]))
        self.assertTrue((self.assets/current['composite']['rendered']).exists())
        self.assertEqual(current['composite']['fingerprint'],composition_fingerprint(current))

    def test_stale_output_does_not_replace_new_edit(self):
        job,directory=self.result(); snapshot=copy.deepcopy(self.p)
        self.p['lines'][0]['text']='夜雨'; self.store.update(self.p['id'],self.p)
        self.jobs._publish(job,snapshot,directory)
        current=self.store.get(self.p['id'])
        self.assertEqual(job['status'],'stale');self.assertNotIn('renders',current)
        self.assertEqual(current['lines'][0]['text'],'夜雨')
        self.assertEqual(list(self.assets.iterdir()),[])

    def test_restart_marks_running_job_interrupted(self):
        job,directory=self.result();job['project_id']=self.p['id']
        (directory/'job.json').write_text(json.dumps(job),encoding='utf-8')
        recovered=RenderJobs(self.store,self.assets,self.root/'jobs')
        self.assertEqual(recovered.get(job['id'])['status'],'interrupted')

    def test_cancellation_is_persisted(self):
        job,directory=self.result();self.jobs.jobs[job['id']]=job
        result=self.jobs.cancel(job['id'])
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(json.loads((directory/'job.json').read_text(encoding='utf-8'))['status'],'cancelled')


if __name__=='__main__':unittest.main()
