import unittest

try:
    import numpy as np
except ImportError:
    np=None

from backend.audio_compose import active_rms,match_vocal_level


@unittest.skipIf(np is None,'numpy is needed for audio composition')
class AudioComposeTests(unittest.TestCase):
    def test_matches_active_voice_without_counting_silence(self):
        sr=24000;time=np.arange(sr)/sr
        tone=np.sin(2*np.pi*220*time).astype(np.float32)
        reference=np.concatenate([np.zeros(sr//2,dtype=np.float32),tone*.4,np.zeros(sr//2,dtype=np.float32)])
        generated=np.concatenate([np.zeros(sr//2,dtype=np.float32),tone*.1,np.zeros(sr//2,dtype=np.float32)])
        matched=match_vocal_level(generated,reference,sr)
        self.assertAlmostEqual(active_rms(matched,sr),active_rms(reference,sr),delta=.005)
        self.assertTrue(np.array_equal(matched[:sr//2],generated[:sr//2]))

    def test_silent_reference_does_not_amplify_and_peak_is_limited(self):
        generated=np.ones(2400,dtype=np.float32)*.3
        self.assertTrue(np.array_equal(match_vocal_level(generated,np.zeros(2400),24000),generated))
        out=match_vocal_level(np.ones(2400,dtype=np.float32)*.7,np.ones(2400)*2,24000)
        self.assertLessEqual(float(np.max(out)),.981)


if __name__=='__main__':unittest.main()
