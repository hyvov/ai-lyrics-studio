"""Local project storage and lyric operations. No model imports or inference side effects."""
from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path
from uuid import uuid4


class Conflict(Exception):
    pass


def validate_project(value: dict) -> dict:
    name = value.get('name', '')
    if not isinstance(name, str):
        raise ValueError('工程名称须为文本。')
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError('工程名称须为 1–120 个字符。')
    duration = value.get('duration', 30)
    if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or not 0 < duration <= 86400:
        raise ValueError('音频时长须大于零且不超过 24 小时。')
    lines = value.get('lines', [])
    if not isinstance(lines, list) or len(lines) > 10000:
        raise ValueError('歌词句数无效。')
    seen = set()
    previous_end = 0
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError('歌词片段无效。')
        line_id = line.get('id')
        if not isinstance(line_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', line_id) or line_id in seen:
            raise ValueError('歌词片段 ID 无效或重复。')
        seen.add(line_id)
        for key in ('text', 'original'):
            if not isinstance(line.get(key), str) or not line[key].replace('<SP>', '').strip() or len(line[key]) > 10000:
                raise ValueError('歌词不能为空或过长。')
        a, b = line.get('start'), line.get('end')
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (a, b)):
            raise ValueError('时间边界须为有限数字。')
        if not 0 <= a < b <= duration + 0.001 or a < previous_end - 0.001:
            raise ValueError('歌词边界超出音频时长、次序错误或存在重叠。')
        previous_end = b
        if line.get('mode') not in ('melody', 'score') or not isinstance(line.get('restore'), bool):
            raise ValueError('生成参数无效。')
    return {'name': name, 'duration': duration, 'lines': lines}


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, project_id: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', project_id):
            raise FileNotFoundError('工程不存在。')
        return self.root / f'{project_id}.json'

    def _write(self, project: dict) -> dict:
        target = self._path(project['id'])
        temporary = target.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(project, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return project

    def list(self) -> list:
        with self.lock:
            return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(self.root.glob('*.json'))]

    def get(self, project_id: str) -> dict:
        with self.lock:
            return json.loads(self._path(project_id).read_text(encoding='utf-8'))

    def create(self, value: dict) -> dict:
        with self.lock:
            project = validate_project(value)
            project.update(id=uuid4().hex, revision=1, assets={})
            return self._write(project)

    def update(self, project_id: str, value: dict) -> dict:
        with self.lock:
            current = self.get(project_id)
            if type(value.get('revision')) is not int or value.get('revision') != current['revision']:
                raise Conflict('工程已在其他页面修改，请刷新后再试。')
            changes = validate_project(value)
            # Audio resources are assigned exclusively by the upload endpoint.
            current.update(changes, revision=current['revision'] + 1)
            return self._write(current)

    def attach(self, project_id: str, kind: str, asset: dict, revision: int, duration: float | None = None) -> dict:
        with self.lock:
            current = self.get(project_id)
            if current['revision'] != revision:
                raise Conflict('工程在上传期间发生修改，请重试。')
            if duration is not None:
                validate_project({**current, 'duration': duration})
                current['duration'] = duration
            current['assets'][kind] = asset
            current['revision'] += 1
            return self._write(current)


def parse_lrc(text: str, duration: float) -> list:
    cues = []
    for raw in text.splitlines():
        stamps = re.findall(r'\[(\d{1,4}):(\d{2})(?:[.:](\d{1,3}))?\]', raw)
        content = re.sub(r'\[[^\]]*\]', '', raw).strip()
        if not content:
            continue
        for minutes, seconds, fraction in stamps:
            if int(seconds) >= 60:
                raise ValueError('LRC 秒数须小于 60。')
            start = int(minutes) * 60 + int(seconds) + (int(fraction) / 10 ** len(fraction) if fraction else 0)
            cues.append((round(start, 3), content))
    cues.sort(key=lambda item: item[0])
    if not cues:
        raise ValueError('未找到带时间戳的 LRC 歌词。')
    if len(cues) > 10000:
        raise ValueError('歌词过长。')
    result = []
    for i, (start, content) in enumerate(cues):
        end = cues[i + 1][0] if i + 1 < len(cues) else duration
        if start < 0 or start >= end or end > duration:
            raise ValueError('歌词时间重复或超出音频时长，请先导入音频并检查 LRC。')
        result.append(dict(id=uuid4().hex, original=content, text=content, start=start, end=end, mode='melody', restore=False))
    return result


def export_lrc(project: dict) -> str:
    rows = []
    for line in project['lines']:
        centiseconds = round(line['start'] * 100)
        minutes, remainder = divmod(centiseconds, 6000)
        seconds, fraction = divmod(remainder, 100)
        content = line['text'].replace('<SP>', '').replace('\r', ' ').replace('\n', ' ')
        rows.append(f'[{minutes:02d}:{seconds:02d}.{fraction:02d}]{content}')
    return '\n'.join(rows) + '\n'
