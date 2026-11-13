import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.midi_slots import validate_midi_slots, apply_midi_note_edits
from backend.model_worker import map_target_notes, mask_changed_reference
from backend.singing import fingerprint, potential_reference_changes
from backend.store import Store, parse_lrc, parse_srt


class MidiSlotsTests(unittest.TestCase):
    def setUp(self):
        fake = types.ModuleType('preprocess.tools.g2p')
        fake.g2p_transform = lambda words, lang: ['<SP>' if w == '<SP>' else 'en_R-EH1-S-T' if w.isascii() else 'zh_de5' for w in words]
        self.modules = patch.dict(sys.modules, {'preprocess':types.ModuleType('preprocess'),
            'preprocess.tools':types.ModuleType('preprocess.tools'), 'preprocess.tools.g2p':fake})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.words = ['分','开','的','<SP>','时','候']
        self.durations = [.37,.24,.57,.94,.33,.95]
        self.pitches = [60,62,63,0,65,62]
        self.types = [2,2,2,1,2,2]
        self.f0 = np.full(170, 300., dtype=np.float32)
        self.f0[59:106] = 0
        self.control = [{'index':0,'word':'rest','slots':['R-EH1','S','T'],'gap_policy':'move'}]

    def plan(self, controls=None, mode='score'):
        return map_target_notes('分开的时候','rest的时候',self.words,self.durations,self.pitches,self.types,self.f0,mode,controls)

    def test_borrowed_note_is_masked_and_custom_slots_are_applied(self):
        words, phones, kinds, pitches, f0, durations, affected = self.plan(self.control)
        self.assertEqual(words, ['rest','rest','rest','的','时','候'])
        self.assertEqual(phones[:4], ['en_cont_R-EH1','en_cont_S','en_last_T','zh_de5'])
        self.assertEqual(pitches[3],63)
        self.assertEqual(affected,[0,1,2])
        self.assertTrue(np.all(f0[59:106] > 0))
        prompt={'text':' '.join(self.words),'duration':' '.join(map(str,self.durations)),
                'note_pitch':' '.join(map(str,self.pitches)),'note_type':' '.join(map(str,self.types)),
                'f0':' '.join(map(str,self.f0))}
        masked,audio=mask_changed_reference(prompt,'分开的时候','rest的时候',np.ones(3400,dtype=np.float32),1000,
                                           force=True,affected_source_indices=affected)
        self.assertEqual(masked['text'].split()[:3],['<SP>']*3)
        self.assertEqual(float(audio[1000]),0)
        self.assertEqual(float(audio[2500]),1)

    def test_keep_and_invalid_custom_slot_count(self):
        control=[{'index':0,'word':'rest','slots':['R-EH1','S-T'],'gap_policy':'keep'}]
        words,phones,_,_,_,_,affected=self.plan(control)
        self.assertEqual(words,['rest','rest','的','<SP>','时','候'])
        self.assertEqual(phones[:2],['en_cont_R-EH1','en_last_S-T'])
        self.assertEqual(affected,[0,1])
        with self.assertRaisesRegex(ValueError,'实际占用 2 个 MIDI 音符'):
            self.plan([{'index':0,'word':'rest','slots':['R','EH1','S-T'],'gap_policy':'keep'}])
        with self.assertRaises(ValueError):
            validate_midi_slots([{'index':0,'word':'rest','slots':['NOTAPHONE'],'gap_policy':'auto'}],'rest的时候')

    def test_multiple_english_groups_do_not_shift_indices(self):
        words=['分','开','的','<SP>','你','好','的','<SP>','啊']
        result=map_target_notes('分开的你好的啊','rest的love的啊',words,[.4,.4,.4,.5,.4,.4,.4,.5,.4],
                                [60,62,63,0,64,65,66,0,67],[2,2,2,1,2,2,2,1,2],np.full(190,300.),'melody')
        self.assertEqual(result[0],['rest','的','love','的','啊'])
        self.assertAlmostEqual(sum(result[5]),3.8)
        self.assertEqual(result[6],[0,1,2,3,4,5])

    def test_note_edits_apply_only_to_matching_source_and_keep_total(self):
        line={'original':'分开的时候','text':'rest的时候','start':0.,'end':3.4,'mode':'score'}
        source='a'*32+'.wav'; edges=np.cumsum(self.durations)
        events=[{'text':w,'type':kind,'source_duration':duration,'end':float(end),'pitch':None}
                for w,kind,duration,end in zip(self.words,self.types,self.durations,edges)]
        events[0]['end']=.39; events[3]['pitch']=64
        value={'source_id':source,'original':line['original'],'start':0.,'end':3.4,'events':events}
        target_words,_,_,target_pitch,_,target_duration,_=self.plan(self.control)
        edited,pitches=apply_midi_note_edits(value,line,source,self.words,self.durations,self.types,target_words,target_duration,target_pitch)
        self.assertAlmostEqual(edited[0],.39); self.assertAlmostEqual(sum(edited),3.4)
        self.assertEqual(pitches[3],64)
        with self.assertRaisesRegex(ValueError,'原唱或音符分析已变化'):
            apply_midi_note_edits(value,line,'b'*32+'.wav',self.words,self.durations,self.types,target_words,target_duration,target_pitch)

    def test_controls_invalidate_render_and_reference_selection_is_conservative(self):
        line={'id':'a','original':'分开的时候','text':'rest的时候','start':0.,'end':3.4,'mode':'score','restore':False}
        project={'assets':{'source':{'id':'a'*32+'.wav'}},'alignments':{}}
        original=fingerprint(project,line); line['midi_slots']=self.control
        self.assertNotEqual(original,fingerprint(project,line))
        self.assertEqual(potential_reference_changes(line), [0,1,2])

    def test_imported_lyrics_default_to_midi(self):
        self.assertEqual(parse_lrc('[00:00.00]你好',3)[0]['mode'],'score')
        self.assertEqual(parse_srt('1\n00:00:00,000 --> 00:00:02,000\n你好',3)[0]['mode'],'score')

    def test_real_note_endpoint_and_saved_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'assets').mkdir(); store=Store(root/'projects')
            line={'id':'a','original':'分开的时候','text':'rest的时候','start':0.,'end':3.4,'mode':'score','restore':False}
            project=store.create({'name':'test','duration':3.4,'lines':[line]})
            source='a'*32+'.wav'; project['assets']={'source':{'id':source,'duration':3.4}}
            job='b'*32; project['render_history']=[{'job_id':job,'segment_id':'a','original':line['original'],'start':0.,'end':3.4}]
            store._write(project); folder=root/'jobs'/job; folder.mkdir(parents=True)
            (folder/'input.json').write_text(json.dumps({'source':str(root/'assets'/source)}),encoding='utf-8')
            prompt={'text':' '.join(self.words),'duration':' '.join(map(str,self.durations)),
                    'note_pitch':' '.join(map(str,self.pitches)),'note_type':' '.join(map(str,self.types))}
            (folder/'a-metadata.json').write_text(json.dumps({'prompt':prompt}),encoding='utf-8')
            with TestClient(create_app(root),base_url='http://127.0.0.1:18733') as client:
                base=f"/api/projects/{project['id']}/segments/a"
                notes=client.get(base+'/midi-notes').json()
                self.assertTrue(notes['available']); self.assertEqual(len(notes['notes']),6)
                auth={'x-studio-token':client.get('/api/session').json()['token']}
                payload={'revision':1,'start':0.,'end':3.4,'text':'rest的时候','mode':'score','restore':False,'midi_slots':self.control}
                response=client.put(base+'/phrase',headers=auth,json=payload)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['lines'][0]['midi_slots'],self.control)


class ProcessorAdapterTests(unittest.TestCase):
    def test_continuation_tokens_work_with_unmodified_upstream_base(self):
        import torch
        upstream=types.ModuleType('soulxsinger.utils.data_processor')
        upstream.DataProcessor=type('DataProcessor',(),{})
        modules={'soulxsinger':types.ModuleType('soulxsinger'),
                 'soulxsinger.utils':types.ModuleType('soulxsinger.utils'),
                 'soulxsinger.utils.data_processor':upstream}
        with patch.dict(sys.modules,modules):
            sys.modules.pop('backend.soulx_processor',None)
            module=importlib.import_module('backend.soulx_processor')
            processor=module.StudioDataProcessor()
            processor.hop_size=480; processor.sample_rate=24000; processor.device='cpu'
            symbols=['<PAD>','<BOW>','<EOW>','<SEP>','en_R','en_EH1','en_S','en_T','zh_de5']
            processor.phone2idx={symbol:i for i,symbol in enumerate(symbols)}
            output=processor.preprocess([.37,.24,.57,.94],['en_cont_R-EH1','en_cont_S','en_last_T','zh_de5'],[60,62,63,63],[2]*4)
            ids=output['phoneme'][0].tolist()
            self.assertIn(processor.phone2idx['en_S'],ids);self.assertIn(processor.phone2idx['en_T'],ids)
            self.assertIsInstance(output['mel2note'],torch.Tensor)
            # Within the first note, R and EH are interleaved frame by frame;
            # this input is not a contiguous phoneme-duration alignment.
            frames = output['mel2note'][0].tolist()
            self.assertEqual(frames[1:7], [2, 3, 2, 3, 2, 3])
            sys.modules.pop('backend.soulx_processor',None)


if __name__ == '__main__': unittest.main()
