"""Isolated A/B experiment: sequential English token frames for target only."""
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'runtime/worker-deps'))
from backend.runtime import Runtime
from backend import model_worker

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--config',required=True);parser.add_argument('--sequential',action='store_true');args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf8'))
    if args.sequential:
        sys.path.insert(0,config['core'])
        import torch
        import backend.soulx_processor as adapter
        base=adapter.StudioDataProcessor
        class ExperimentalProcessor(base):
            def process(self,meta,wav_path=None):
                self.experimental_target = wav_path is None
                return super().process(meta,wav_path)
            def preprocess(self,note_duration,phonemes,note_pitch,note_type):
                result=super().preprocess(note_duration,phonemes,note_pitch,note_type)
                if not getattr(self,'experimental_target',False): return result
                mapping=result['mel2note'][0]; pos=1; elapsed=0.0
                for duration, phone in zip(note_duration,phonemes):
                    start=round(elapsed*self.sample_rate/self.hop_size); elapsed+=duration
                    end=min(len(mapping),round(elapsed*self.sample_rate/self.hop_size))
                    chunks = phone[8:].split('-') if phone.startswith(('en_cont_','en_last_')) else phone[3:].split('-') if phone.startswith('en_') else []
                    sep=phone.startswith(('en_last_','en_')) and not phone.startswith('en_cont_')
                    count=len(chunks)+int(sep) if chunks else 1
                    if chunks and end-start >= count+2:
                        # Fixed boundaries, simple duration allocation; SEP gets one frame.
                        mapping[start]=pos; mapping[end-1]=pos+count+1
                        usable=end-start-2; content=usable-int(sep)
                        weights=[4 if any(c.isdigit() for c in x) else 1 for x in chunks]
                        cursor=start+1
                        for i,weight in enumerate(weights):
                            nxt=start+1+round(content*sum(weights[:i+1])/sum(weights))
                            mapping[cursor:nxt]=pos+1+i;cursor=nxt
                        if sep: mapping[cursor:end-1]=pos+count
                    pos+=count+2
                return result
        adapter.StudioDataProcessor=ExperimentalProcessor
    model_worker.run(config)
if __name__=='__main__': main()
