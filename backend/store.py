"""Local project storage and lyric operations. No model imports or inference side effects."""
from __future__ import annotations

import json
import copy
from backend.generation_params import generation_params
import math
import os
import re
import threading
import time
from pathlib import Path
from uuid import uuid4


class Conflict(Exception):
    pass


def snapshot_lyrics(project: dict, reason: str):
    if not project.get('lines'):return
    project.setdefault('lyric_history',[]).append({
        'id':uuid4().hex,'created_at':time.time(),'reason':reason,
        'source_id':project.get('assets',{}).get('source',{}).get('id'),
        'lines':copy.deepcopy(project['lines'])})


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
        if line.get('mode') not in ('melody', 'score', 'ying') or not isinstance(line.get('restore'), bool):
            raise ValueError('生成参数无效。')
        generation_params(line['mode'], line.get('generation_params'))
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

    def replace_lyrics(self, project_id: str, revision: int, lines: list, reason: str) -> dict:
        with self.lock:
            current=self.get(project_id)
            if type(revision) is not int or revision!=current['revision']:
                raise Conflict('工程已更新，请刷新后再试。')
            validate_project({**current,'lines':lines})
            snapshot_lyrics(current,reason)
            current['lines']=lines;current['revision']+=1
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


def parse_srt(text: str, duration: float) -> list:
    """Preserve explicit caption boundaries and gaps; reject corrupt time axes."""
    import html
    normalized = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        raise ValueError('SRT 字幕为空。')
    blocks = re.split(r'\n[ \t]*\n+', normalized)
    if len(blocks) > 10000:
        raise ValueError('字幕条数不能超过 10000。')
    stamp = r'\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}'
    timing = re.compile(rf'^({stamp})\s*-->\s*({stamp})(?:\s+(?:X1|X2|Y1|Y2):\d+)*\s*$')

    def seconds(value):
        h, m, rest = value.split(':')
        sec, fraction = re.split('[,.]', rest)
        if int(m) >= 60 or int(sec) >= 60:
            raise ValueError('SRT 的分钟和秒数必须小于 60。')
        return round(int(h) * 3600 + int(m) * 60 + int(sec) + int(fraction) / 10 ** len(fraction), 3)

    result, previous_end = [], 0.
    for position, block in enumerate(blocks, 1):
        rows = block.strip().split('\n')
        if rows[0].strip().isdigit():
            rows = rows[1:]
        match = timing.fullmatch(rows[0].strip()) if rows else None
        if not match:
            raise ValueError(f'第 {position} 条 SRT 时间格式无效，应为 00:00:01,000 --> 00:00:03,000。')
        a, b = seconds(match[1]), seconds(match[2])
        if a >= b or b > duration + .001 or a < previous_end - .000001:
            raise ValueError(f'第 {position} 条 SRT 时间倒置、超出音频或与前一条重叠；原有歌词未替换。')
        content = '\n'.join(rows[1:])
        content = re.sub(r'<br\s*/?>', '\n', content, flags=re.I)
        content = re.sub(r'</?(?:b|i|u|s|font|span)\b[^>]*>', '', content, flags=re.I)
        content = re.sub(r'\{\\[^}]+\}', '', content)
        content = ' '.join(line.strip() for line in html.unescape(content).splitlines() if line.strip())
        if not content or not content.replace('<SP>', '').strip():
            raise ValueError(f'第 {position} 条 SRT 字幕没有歌词文本。')
        result.append(dict(id=uuid4().hex, original=content, text=content, start=a, end=b, mode='melody', restore=False))
        previous_end = b
    return result


def parse_lyrics(text: str, duration: float, format_name=None) -> list:
    if format_name is not None and not isinstance(format_name, str):
        raise ValueError('歌词格式须为 SRT 或 LRC。')
    kind = (format_name or '').lower().lstrip('.')
    if kind in ('', 'auto'):
        kind = 'srt' if '-->' in text else 'lrc'
    if kind == 'srt':
        return parse_srt(text, duration)
    if kind == 'lrc':
        return parse_lrc(text.lstrip('\ufeff'), duration)
    raise ValueError('仅支持 SRT 字幕或 LRC 歌词。')


def export_srt(project: dict) -> str:
    def stamp(seconds):
        total = round(seconds * 1000)
        hours, total = divmod(total, 3600000)
        minutes, total = divmod(total, 60000)
        secs, millis = divmod(total, 1000)
        return f'{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}'
    blocks = []
    for i, line in enumerate(project['lines'], 1):
        text = '\n'.join(row.strip() for row in line['text'].replace('<SP>', '').splitlines() if row.strip())
        blocks.append(f"{i}\n{stamp(line['start'])} --> {stamp(line['end'])}\n{text}")
    return '\n\n'.join(blocks) + ('\n' if blocks else '')
