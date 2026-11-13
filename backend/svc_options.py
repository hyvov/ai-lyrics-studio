"""Validated quality controls for the local voice-conversion model."""
QUALITY_STEPS = {'fast': 16, 'standard': 32, 'fine': 64}


def svc_options(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(key not in ('quality', 'auto_shift') for key in value):
        raise ValueError('音色转换参数无效。')
    quality = value.get('quality', 'standard')
    auto_shift = value.get('auto_shift', False)
    if not isinstance(quality, str) or quality not in QUALITY_STEPS or type(auto_shift) is not bool:
        raise ValueError('请选择有效的转换质量和音域匹配设置。')
    return {'quality':quality, 'auto_shift':auto_shift}
