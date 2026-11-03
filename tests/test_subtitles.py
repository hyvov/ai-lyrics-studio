import hashlib
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.media import KNOWN_TEST_AUDIO
from backend.store import parse_srt, parse_lyrics, export_srt

SRT = '\ufeff1\r\n00:00:01,234 --> 00:00:02,345\r\n<i>晚风</i>\r\n轻轻吹过\r\n\r\n2\r\n00:00:05,000 --> 00:00:07,001\r\n灯火亮起\r\n'

class SubtitleTests(unittest.TestCase):
    def test_explicit_end_times_gaps_multiline_and_bom(self):
        lines=parse_srt(SRT,10)
        self.assertEqual([(x['start'],x['end']) for x in lines],[(1.234,2.345),(5.,7.001)])
        self.assertEqual(lines[0]['text'],'晚风 轻轻吹过')
        self.assertNotEqual(lines[-1]['end'],10)
        self.assertEqual(len({x['id'] for x in lines}),2)

    def test_auto_detection_and_existing_lrc(self):
        self.assertEqual(len(parse_lyrics(SRT,10)),2)
        self.assertEqual(parse_lyrics(SRT,10,'SRT')[0]['start'],1.234)
        self.assertEqual(parse_lyrics('\ufeff[00:01]晚风',10,'lrc')[0]['start'],1)
        with self.assertRaises(ValueError):parse_lyrics(SRT,10,'vtt')

    def test_srt_rejects_bad_time_axis(self):
        for text in ('1\n00:00:03,000 --> 00:00:02,000\n词',
                     '1\n00:00:01,000 --> 00:00:12,000\n词',
                     '1\n00:00:01,000 --> 00:00:04,000\n词\n\n2\n00:00:03,000 --> 00:00:05,000\n词',
                     '1\n00:61:01,000 --> 01:01:05,000\n词',
                     '1\nnot a timestamp\n词',
                     '1\n00:00:01,000 --> 00:00:02,000\n<i></i>'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_srt(text,10)

    def test_style_decimal_timestamps_and_export_round_trip(self):
        lines=parse_srt('00:00:01.250 --> 00:00:03.500\n{\\an8}<font color="red">你</font><br>好 &amp; hello',10)
        self.assertEqual(lines[0]['text'],'你 好 & hello')
        lines[0]['text']='新歌词'
        decoded=parse_srt(export_srt({'lines':lines}),10)
        self.assertEqual([(x['start'],x['end'],x['text']) for x in decoded],[(1.25,3.5,'新歌词')])

    def test_export_millisecond_carry(self):
        output=export_srt({'lines':[{'start':59.9996,'end':60.25,'text':'<SP>你\n\n好'}]})
        self.assertIn('00:01:00,000 --> 00:01:00,250',output)
        self.assertIn('你\n好',output)

    def test_api_preserves_previous_lyrics_on_invalid_srt(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory)),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']}
            p=c.post('/api/projects',headers=auth,json={'name':'字幕测试','duration':10,'lines':[]}).json()
            url=f"/api/projects/{p['id']}/lyrics"
            response=c.post(url,headers=auth,json={'revision':1,'format':'srt','text':SRT})
            self.assertEqual(response.status_code,200,response.text)
            p=response.json()
            self.assertEqual(p['lines'][-1]['end'],7.001)
            exported=c.get(f"/api/projects/{p['id']}/lyrics.srt")
            self.assertEqual(exported.status_code,200)
            self.assertIn('00:00:07,001',exported.text)
            response=c.post(url,headers=auth,json={'revision':p['revision'],'format':'srt','text':'invalid'})
            self.assertEqual(response.status_code,400)
            unchanged=c.get('/api/projects/'+p['id']).json()
            self.assertEqual(unchanged['revision'],p['revision'])
            self.assertEqual(unchanged['lines'],p['lines'])

    def test_known_synthetic_fixture_is_blocked_before_model_load(self):
        data=io.BytesIO()
        with wave.open(data,'wb') as wav:
            wav.setparams((1,2,8000,0,'NONE','none'));wav.writeframes(b'\0\0'*16000)
        digest=hashlib.sha256(data.getvalue()).hexdigest()
        message='已知测试音，没有实际演唱'
        with tempfile.TemporaryDirectory() as directory, patch.dict(KNOWN_TEST_AUDIO,{digest:message}), TestClient(create_app(Path(directory)),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']}
            p=c.post('/api/projects',headers=auth,json={'name':'fixture','duration':2,'lines':[]}).json()
            headers={**auth,'x-file-name':'renamed.wav','x-project-revision':'1','x-audio-duration':'2'}
            p=c.post(f"/api/projects/{p['id']}/assets/source",headers=headers,content=data.getvalue()).json()
            self.assertEqual(p['source_issue'],message)
            line={'id':'line1','start':0.,'end':2.,'original':'你好','text':'你早','mode':'melody','restore':False}
            p['lines']=[line]
            p=c.put('/api/projects/'+p['id'],headers=auth,json=p).json()
            response=c.post(f"/api/projects/{p['id']}/renders",headers=auth,json={'revision':p['revision'],'segment_ids':['line1']})
            self.assertEqual(response.status_code,400,response.text)
            self.assertEqual(response.json()['error'],message)
            self.assertEqual(list((Path(directory)/'jobs').iterdir()),[])

    def test_filename_alone_does_not_block_audio(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory)),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']}
            p=c.post('/api/projects',headers=auth,json={'name':'normal','duration':2,'lines':[]}).json()
            headers={**auth,'x-file-name':'test-tone.wav','x-project-revision':'1','x-audio-duration':'2'}
            p=c.post(f"/api/projects/{p['id']}/assets/source",headers=headers,content=b'different bytes').json()
            self.assertIsNone(p['source_issue'])

if __name__=='__main__':unittest.main()
