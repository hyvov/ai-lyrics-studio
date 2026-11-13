"""Project-owned adapter for the pinned SoulX-Singer DataProcessor.

The upstream preprocessing method is derived from Soul-AILab/SoulX-Singer,
commit 81aeb3ae772c70093c3de74dc23c92d983801ae4 (Apache-2.0). English
continuation tokens are expanded here, without modifying the upstream checkout.
"""
from typing import List
import numpy as np
import torch
from soulxsinger.utils.data_processor import DataProcessor


class StudioDataProcessor(DataProcessor):
    def preprocess(
        self,
        note_duration: List[float],
        phonemes: List[str],
        note_pitch: List[int],
        note_type: List[int],
    ):
        """
        Insert <BOW> and <EOW> for each note. 
        Get aligned indices for each frame.

        Args:
            note_duration: Duration of each note in seconds
            phonemes: Phoneme sequence for each note
            note_pitch: Pitch value for each note
            note_type: Type value for each note
        
        """
        sample_rate = self.sample_rate
        hop_size = self.hop_size
        duration = sum(note_duration) * sample_rate / hop_size
        mel2note = torch.zeros(int(duration), dtype=torch.long)

        ph_locations = []   # idx at mel scale and length
        new_phonemes = []
        dur_sum = 0

        note2origin = []

        for ph_idx in range(len(phonemes)):
            dur = int(np.round(dur_sum * sample_rate / hop_size))
            dur = min(dur, len(mel2note) - 1)
            new_phonemes.append("<BOW>")
            note2origin.append(ph_idx)
            if phonemes[ph_idx][:8] == "en_cont_":
                # English word continuation across adjacent source notes: retain
                # the shared word boundary and omit <SEP> between its phone chunks.
                en_phs = ['en_' + x for x in phonemes[ph_idx][8:].split('-')]
                ph_locations.append([dur, max(1, len(en_phs))])
                new_phonemes.extend(en_phs)
                note2origin.extend([ph_idx] * len(en_phs))
            elif phonemes[ph_idx][:8] == "en_last_":
                en_phs = ['en_' + x for x in phonemes[ph_idx][8:].split('-')] + ['<SEP>']
                ph_locations.append([dur, max(1, len(en_phs))])
                new_phonemes.extend(en_phs)
                note2origin.extend([ph_idx] * len(en_phs))
            elif phonemes[ph_idx][:3] == "en_":
                en_phs = ['en_' + x for x in phonemes[ph_idx][3:].split('-')] + ['<SEP>']     # <sep> between en words in one note
                ph_locations.append([dur, max(1, len(en_phs))])
                new_phonemes.extend(en_phs)
                note2origin.extend([ph_idx] * len(en_phs))
            else:
                ph_locations.append([dur, 1])
                new_phonemes.append(phonemes[ph_idx])
                note2origin.append(ph_idx)
            new_phonemes.append("<EOW>")
            note2origin.append(ph_idx)
            dur_sum += note_duration[ph_idx]

        ph_idx = 1
        for idx, (i, j) in enumerate(ph_locations):
            next_phoneme_start = ph_locations[idx + 1][0] if idx < len(ph_locations) - 1 else len(mel2note)
            if i >= len(mel2note) or i + j > len(mel2note):
                break
            if i < len(mel2note) and mel2note[i] > 0:
                # print(f"warning: overlap of {idx}: {mel2note[i]}")
                while i < len(mel2note) and mel2note[i] > 0:
                    i += 1
            mel2note[i] = ph_idx
            k = i + 1
            while k + j < next_phoneme_start:
                mel2note[k : k + j] = torch.arange(ph_idx, ph_idx + j) + 1
                k += j
            mel2note[next_phoneme_start - 1] = ph_idx + j + 1
            ph_idx += j + 2     # <BOW> + ph repeats + <EOW>

        new_phonemes = ["<PAD>"] + new_phonemes
        new_note_pitch = [0] + [note_pitch[k] for k in note2origin]
        new_note_type = [1] + [note_type[k] for k in note2origin]

        return {
            "phoneme": torch.tensor([self.phone2idx[x] for x in new_phonemes], device=self.device).unsqueeze(0),
            "note_pitch": torch.tensor(new_note_pitch, device=self.device).unsqueeze(0),
            "note_type": torch.tensor(new_note_type, device=self.device).unsqueeze(0),
            "mel2note": mel2note.clone().detach().to(self.device).unsqueeze(0),
        }
    
