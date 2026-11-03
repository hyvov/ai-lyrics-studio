import io
import tempfile
import unittest
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import Conflict, Store, export_lrc, parse_lrc


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = TestClient(create_app(self.root), base_url='http://127.0.0.1:18733')
        self.addCleanup(self.client.close)
        token = self.client.get('/api/session').json()['token']
        self.auth = {'x-studio-token': token}

    def create(self):
        response = self.client.post('/api/projects', headers=self.auth, json={'name': '测试工程', 'duration': 10, 'lines': []})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_import_edit_restart_export_and_conflict(self):
        p = self.create()
        response = self.client.post(f"/api/projects/{p['id']}/lyrics", headers=self.auth,
                                    json={'revision': p['revision'], 'text': '[00:00.00]晚风吹过\n[00:05.00]灯火亮起'})
        self.assertEqual(response.status_code, 200, response.text)
        p = response.json()
        stale = dict(p)
        p['lines'][0]['text'] = '晨光洒落'
        response = self.client.put(f"/api/projects/{p['id']}", headers=self.auth, json=p)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.put(f"/api/projects/{p['id']}", headers=self.auth, json=stale).status_code, 409)
        # A fresh store reopens the on-disk project, including edited text and stable IDs.
        loaded = Store(self.root / 'projects').get(p['id'])
        self.assertEqual(loaded['lines'][0]['text'], '晨光洒落')
        self.assertEqual(loaded['lines'][0]['id'], p['lines'][0]['id'])
        exported = self.client.get(f"/api/projects/{p['id']}/lyrics.lrc")
        self.assertIn('[00:00.00]晨光洒落', exported.text)

    def test_invalid_boundaries_do_not_overwrite(self):
        p = self.create()
        p['lines'] = parse_lrc('[00:00.00]第一句\n[00:05.00]第二句', 10)
        p['lines'][0]['end'] = 7
        response = self.client.put(f"/api/projects/{p['id']}", headers=self.auth, json=p)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Store(self.root / 'projects').get(p['id'])['lines'], [])
        response = self.client.post('/api/projects', headers=self.auth, json={'name': {}})
        self.assertEqual(response.status_code, 400)

    def test_upload_range_and_failed_upload_cleanup(self):
        p = self.create()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
            wav.writeframes(b'\x00\x00' * 80000)
        headers = {**self.auth, 'x-file-name': 'test.wav', 'x-project-revision': '1', 'x-audio-duration': '10'}
        url = f"/api/projects/{p['id']}/assets/source"
        response = self.client.post(url, headers=headers, content=audio.getvalue())
        self.assertEqual(response.status_code, 200, response.text)
        resource = response.json()['assets']['source']['id']
        response = self.client.get('/api/assets/' + resource, headers={'Range': 'bytes=0-43'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, audio.getvalue()[:44])
        response = self.client.post(url, headers=headers, content=audio.getvalue())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(list((self.root / 'assets').glob('*'))), 1)
        self.assertEqual(self.client.get('/api/assets/secret.json').status_code, 404)

    def test_cross_origin_and_missing_token_blocked(self):
        self.assertEqual(self.client.post('/api/projects', json={'name': 'x'}).status_code, 403)
        response = self.client.post('/api/projects', headers={**self.auth, 'Origin': 'https://example.com'}, json={'name': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/session', headers={'Host': 'evil.example'}).status_code, 403)

    def test_lrc_edge_cases(self):
        with self.assertRaises(ValueError): parse_lrc('[00:01]a\n[00:01]b', 10)
        with self.assertRaises(ValueError): parse_lrc('[00:15]a', 10)
        with self.assertRaises(ValueError): parse_lrc('[00:61]a', 100)
        with self.assertRaises(ValueError): parse_lrc('没有时间戳', 10)
        lines = parse_lrc('[00:01.2][00:05.12]重复句', 10)
        self.assertEqual([x['start'] for x in lines], [1.2, 5.12])
        lines[0]['start'] = 59.999
        self.assertTrue(export_lrc({'lines': lines}).startswith('[01:00.00]'))


if __name__ == '__main__':
    unittest.main()
