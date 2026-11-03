import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.hfa import build_phoneme_sequence
from backend.runtime import Runtime

class HfaTests(unittest.TestCase):
    def test_chinese_english_word_indices_remain_stable(self):
        def dictionary(lang):
            def pronounce(label):
                return ['SP',lang+'/'+label+'1',lang+'/'+label+'2','SP'],[label],[-1,0,0,-1]
            return pronounce
        phones,labels,mapping=build_phoneme_sequence(['你','Baby','好'],['ni','hao'],dictionary)
        self.assertEqual(labels,['ni','baby','hao'])
        self.assertEqual(mapping,[-1,0,0,-1,1,1,-1,2,2,-1])
        self.assertEqual(phones[4:6],['en/baby1','en/baby2'])
        self.assertEqual(phones[0], 'SP');self.assertEqual(phones[-1],'SP')

    def test_missing_pronunciation_is_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            build_phoneme_sequence(['你'],['ni'],lambda lang:lambda label:(['SP'],[],[-1]))

    def test_optional_shared_component_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);model=root/'model';python=root/'python';source=root/'source'
            env={'SOULX_HFA_MODEL':str(model),'SOULX_HFA_PYTHON':str(python),'SOULX_HFA_SOURCE':str(source)}
            runtime=Runtime(root,root/'python.exe',root/'core',root/'models')
            with patch.dict(os.environ,env):
                self.assertIsNone(runtime.hfa_config())
                files=[model/'model.onnx',model/'vocab.json',model/'config.json',model/'VERSION',
                       source/'inference/HubertFA/tools/decoder.py',python/'onnxruntime/__init__.py',python/'pypinyin/__init__.py']
                for file in files:
                    file.parent.mkdir(parents=True,exist_ok=True);file.write_text('5',encoding='utf-8')
                result=runtime.hfa_config()
                self.assertEqual(Path(result['model']),model)
                self.assertEqual(Path(result['source']),source)
                self.assertEqual((model/'VERSION').read_text(),'5')

if __name__=='__main__':unittest.main()
