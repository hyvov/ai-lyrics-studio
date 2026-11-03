import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.media import waveform


@unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg is required for waveform decoding')
class WaveformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);assets=self.root/'assets';assets.mkdir()
        self.asset='a'*32+'.wav';self.path=assets/self.asset
        # Left channel is silent; right channel carries an audible tone.
        with wave.open(str(self.path),'wb') as handle:
            handle.setnchannels(2);handle.setsampwidth(2);handle.setframerate(16000)
            handle.writeframes(b''.join(struct.pack('<hh',0,int(16000*math.sin(2*math.pi*440*i/16000))) for i in range(16000)))

    def test_stereo_peaks_are_dense_and_do_not_ignore_right_channel(self):
        peaks=waveform(self.path)
        self.assertEqual(len(peaks),200)
        self.assertGreater(max(peaks),.4)
        with TestClient(create_app(self.root),base_url='http://127.0.0.1:18733') as c:
            response=c.get('/api/assets/'+self.asset+'/waveform')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['peaks']),200)
            self.assertEqual(c.get('/api/assets/secret.wav/waveform').status_code,404)


if __name__=='__main__':unittest.main()
