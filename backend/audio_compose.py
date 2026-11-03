"""Loudness matching for generated vocal clips before crossfading."""
def active_rms(samples, sample_rate):
    import numpy as np
    samples=np.asarray(samples,dtype=np.float32)
    if not len(samples):return 0.0
    frame=min(len(samples),max(1,round(sample_rate*.05)));hop=max(1,frame//2)
    starts=list(range(0,max(1,len(samples)-frame+1),hop)) or [0]
    if starts[-1]+frame<len(samples):starts.append(len(samples)-frame)
    levels=np.asarray([np.sqrt(np.mean(np.square(samples[i:i+frame],dtype=np.float64))) for i in starts])
    gate=max(10**(-55/20),float(np.max(levels))*.1)
    active=levels[levels>=gate]
    return float(np.sqrt(np.mean(np.square(active)))) if len(active) else 0.0


def match_vocal_level(generated,reference,sample_rate):
    import numpy as np
    generated=np.asarray(generated,dtype=np.float32)
    level=active_rms(generated,sample_rate);reference_level=active_rms(reference,sample_rate)
    if level<=1e-6 or reference_level<=1e-6:return generated
    gain=max(10**(-12/20),min(10**(12/20),reference_level/level))
    peak=float(np.max(np.abs(generated))) if len(generated) else 0.
    if peak>1e-9:gain=min(gain,.98/peak)
    return (generated*gain).astype(np.float32,copy=False)
