import sys
import types
import unittest
from unittest.mock import patch
import numpy as np
from backend.model_worker import mask_changed_reference
from backend.singing import prefer_masked_reference


class ReferenceQualityTests(unittest.TestCase):
    def test_saved_timing_avoids_an_unnecessary_reference(self):
        line={'original':'baby baby想爱不能爱的哀愁','text':'baby baby想休不能休的哀愁','start':0.,'end':4.}
        words=['baby','baby','想','爱','不','能','爱','的','哀','愁']
        alignment={'unit_timings':[{'text':w,'start':i*.4,'end':(i+1)*.4} for i,w in enumerate(words)]}
        self.assertTrue(prefer_masked_reference(line,alignment))
        self.assertFalse(prefer_masked_reference(line,None))
    def setUp(self):
        self.prompt = {'text':'我 明 白 有 你','duration':'0.5 0.5 0.5 0.5 0.5',
                       'note_pitch':'60 60 60 60 60','note_type':'2 2 2 2 2',
                       'f0':' '.join(['220']*250)}
        self.audio = np.ones(2500,dtype=np.float32)
        fake = types.ModuleType('preprocess.tools.g2p')
        fake.g2p_transform = lambda words, language: words
        self.g2p = patch.dict(sys.modules, {'preprocess':types.ModuleType('preprocess'),
             'preprocess.tools':types.ModuleType('preprocess.tools'), 'preprocess.tools.g2p':fake})
        self.g2p.start(); self.addCleanup(self.g2p.stop)

    def test_one_changed_word_is_masked_but_other_audio_remains(self):
        prompt,audio = mask_changed_reference(self.prompt,'我明白有你','我明白是你',self.audio,1000)
        self.assertEqual(prompt['text'],'我 明 白 <SP> 你')
        self.assertEqual(prompt['note_type'],'2 2 2 1 2')
        self.assertEqual(audio[1750],0)
        self.assertEqual(audio[250],1)
        self.assertEqual(float(prompt['f0'].split()[175]),0)
        self.assertEqual(self.prompt['text'],'我 明 白 有 你')

    def test_heavy_rewrite_prefers_another_reference(self):
        self.assertIsNone(mask_changed_reference(self.prompt,'我明白有你','他不认识你',self.audio,1000))
        with self.assertRaises(ValueError):
            mask_changed_reference(self.prompt,'我明白有你','他们真的好',self.audio,1000,force=True)

    def test_short_retained_reference_falls_back(self):
        short = dict(self.prompt,duration='0.2 0.2 0.2 0.2 0.2')
        self.assertIsNone(mask_changed_reference(short,'我明白有你','我明白是你',self.audio[:1000],1000))


if __name__ == '__main__': unittest.main()
