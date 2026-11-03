"""Known application test media: identify by bytes, never by user file names."""
from functools import lru_cache
import hashlib
from pathlib import Path

KNOWN_TEST_AUDIO = {
    '8330b46e8444b34acfce94ba08a04b91bdbbcceae44dc40a6599a1b317745604':
        '当前音频是功能测试用的合成测试音，不包含实际演唱，无法对齐歌词。请导入真实原唱干声，或切换到可生成的演唱示例。',
}

@lru_cache(maxsize=128)
def _digest(path: str, size: int, modified_ns: int):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def source_issue(project, assets):
    asset = project.get('assets', {}).get('source')
    if not asset:
        return None
    path = assets / asset['id']
    if not path.is_file():
        return None
    stat = path.stat()
    digest = _digest(str(path), stat.st_size, stat.st_mtime_ns)
    return KNOWN_TEST_AUDIO.get(digest)
