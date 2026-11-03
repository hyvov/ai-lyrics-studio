"""Singing-specific acoustic alignment using shared HFA ONNX weights.

Decoder/G2P come from an independent Apache-2.0 Vocal2Midi checkout, not Studio.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from types import SimpleNamespace

def build_phoneme_sequence(original, chinese_pinyin, get_dictionary):
    """Keep word indices stable across Chinese/English pronunciations."""
    pinyin = iter(chinese_pinyin)
    phones, mapping, labels = ['SP'], [-1], []
    for index, word in enumerate(original):
        is_chinese = len(word) == 1 and '\u4e00' <= word <= '\u9fff'
        lang, label = ('zh', next(pinyin)) if is_chinese else ('en', word.casefold())
        pronunciation, words, indices = get_dictionary(lang)(label)
        if words != [label] or len(pronunciation) != len(indices) or not pronunciation or pronunciation[0] != 'SP' or pronunciation[-1] != 'SP':
            raise ValueError(f'HFA 发音词典未覆盖 {word}。')
        phones.extend(pronunciation[1:-1])
        mapping.extend(index if x >= 0 else -1 for x in indices[1:-1])
        phones.append('SP'); mapping.append(-1); labels.append(label)
    return phones, labels, mapping


class HfaAligner:
    def __init__(self, config):
        sys.path.insert(0, config['source'])
        sys.path.insert(0, config['python'])
        import onnxruntime as ort
        from inference.HubertFA.tools.decoder import AlignmentDecoder
        from inference.HubertFA.tools.g2p import DictionaryG2P
        self.root = Path(config['model'])
        if (self.root / 'VERSION').read_text(encoding='utf-8').strip() != '5':
            raise ValueError('HubertFA 模型格式不兼容。')
        self.vocab = json.loads((self.root / 'vocab.json').read_text(encoding='utf-8'))
        settings = json.loads((self.root / 'config.json').read_text(encoding='utf-8'))['mel_spec_config']
        self.sr = settings['sample_rate']
        self.decoder = AlignmentDecoder(self.vocab, self.sr, settings['hop_size'])
        self.g2p_class = DictionaryG2P
        self.dictionaries = {}
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        ort.disable_telemetry_events()
        self.session = ort.InferenceSession(str(self.root / 'model.onnx'), sess_options=options, providers=['CPUExecutionProvider'])

    def align(self, *, audio, text, language):
        import librosa
        import numpy as np
        from pypinyin import lazy_pinyin
        from backend.singing import units
        original = units(text)
        chinese = [u for u in original if len(u) == 1 and '\u4e00' <= u <= '\u9fff']
        def dictionary(lang):
            if lang not in self.dictionaries:
                name = self.vocab['dictionaries'].get(lang)
                if not name:
                    raise ValueError('HFA 没有对应语言的发音词典。')
                self.dictionaries[lang] = self.g2p_class(lang, self.root / name)
            return self.dictionaries[lang]
        phones, labels, mapping = build_phoneme_sequence(original, lazy_pinyin(''.join(chinese)), dictionary)
        waveform, sr = audio
        waveform = librosa.resample(waveform, orig_sr=sr, target_sr=self.sr).astype(np.float32)
        result = dict(zip((x.name for x in self.session.get_outputs()), self.session.run(None, {'waveform': waveform[None, :]})))
        words, confidence = self.decoder.decode(result['ph_frame_logits'], result['ph_edge_logits'], len(waveform)/self.sr, phones, labels, mapping)
        if len(words) != len(original) or not np.isfinite(confidence) or confidence < .1:
            raise ValueError('唱歌对齐模型未获得完整有效的词边界。')
        return [SimpleNamespace(items=[SimpleNamespace(text=text, start_time=float(word.start), end_time=float(word.end))
                                       for text, word in zip(original, words)], confidence=float(confidence))]
