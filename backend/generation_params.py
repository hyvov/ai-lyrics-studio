"""Validated per-phrase inference settings, compatible with older projects."""
import math

DEFAULTS = {
    'melody': {'steps':32,'cfg':3.0,'seed':-1},
    'score': {'steps':32,'cfg':3.0,'seed':-1},
    'ying': {'steps':64,'cfg':2.5,'seed':42,'t_shift':.5,'silence':.5},
}


def generation_params(mode, value=None):
    if mode not in DEFAULTS: raise ValueError('生成模式无效。')
    defaults = DEFAULTS[mode]
    if value is None: value={}
    if not isinstance(value,dict) or any(key not in defaults for key in value):
        raise ValueError('生成参数无效或不适用于当前模式。')
    result={**defaults,**value}
    specs={'steps':(4,128,True),'cfg':(0,10,False),'seed':(-1,2147483647,True),
           't_shift':(0,1,False),'silence':(0,3,False)}
    labels={'steps':'采样步数','cfg':'引导强度','seed':'随机种子','t_shift':'采样时间偏移','silence':'参考末尾静音'}
    for key,number in result.items():
        lower,upper,integer=specs[key]
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or not lower<=number<=upper or (integer and int(number)!=number):
            raise ValueError(f'{labels[key]}须在 {lower}–{upper} 范围内'+('且为整数。' if integer else '。'))
        result[key]=int(number) if integer else float(number)
    return result
