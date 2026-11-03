import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.jobs import RenderJobs
from backend.singing import composition_fingerprint
from backend.store import Store


class SeparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assets = self.root / 'assets'; self.assets.mkdir()
        self.store = Store(self.root / 'projects')
        self.p = self.store.create({'name':'separation', 'duration':5, 'lines':[]})
        self.p['assets'] = {'song':{'id':'song.wav','duration':5}, 'source':{'id':'old.wav','duration':5}}
        self.store._write(self.p)
        self.jobs = RenderJobs(self.store,self.assets,self.root / 'jobs')
        self.job = {'id':'a'*32,'status':'running','warnings':[]}
        self.dir = self.root / 'jobs' / self.job['id']; self.dir.mkdir()
        (self.dir / 'separation-result.json').write_text(json.dumps({'duration':5}),encoding='utf-8')
        (self.dir / 'source.wav').write_bytes(b'x'*100)
        (self.dir / 'accompaniment.wav').write_bytes(b'y'*100)

    def test_publish_both_stems_as_one_revision(self):
        self.jobs._publish_separation(self.job,copy.deepcopy(self.p),self.dir)
        result=self.store.get(self.p['id'])
        self.assertEqual(result['revision'],2)
        self.assertNotEqual(result['assets']['source']['id'],'old.wav')
        self.assertEqual((self.assets/result['assets']['source']['id']).read_bytes(),b'x'*100)
        self.assertEqual((self.assets/result['assets']['accompaniment']['id']).read_bytes(),b'y'*100)
        self.assertEqual(self.job['status'],'succeeded')

    def test_changed_audio_is_not_overwritten(self):
        snapshot=copy.deepcopy(self.p)
        self.p['assets']['source']['id']='new.wav';self.store._write(self.p)
        self.jobs._publish_separation(self.job,snapshot,self.dir)
        self.assertEqual(self.job['status'],'stale')
        self.assertEqual(self.store.get(self.p['id'])['assets']['source']['id'],'new.wav')
        self.assertEqual(list(self.assets.iterdir()),[])

    def test_missing_stem_does_not_partially_publish(self):
        (self.dir/'accompaniment.wav').unlink()
        with self.assertRaises(ValueError):
            self.jobs._publish_separation(self.job,copy.deepcopy(self.p),self.dir)
        self.assertEqual(self.store.get(self.p['id'])['assets']['source']['id'],'old.wav')
        self.assertEqual(list(self.assets.iterdir()),[])

    def test_staging_song_does_not_expire_existing_composite(self):
        before=copy.deepcopy(self.p);before['assets'].pop('song')
        self.assertEqual(composition_fingerprint(before),composition_fingerprint(self.p))


if __name__ == '__main__':
    unittest.main()
