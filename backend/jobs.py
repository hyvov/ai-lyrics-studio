"""One isolated GPU process at a time, persistent job status, stale result guards."""
from __future__ import annotations
import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from backend.runtime import ROOT, Runtime
from backend.singing import composition_fingerprint, fingerprint, validate_render
from backend.store import Conflict

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'stale'}

class RenderJobs:
    def __init__(self, store, assets, root, runtime=None):
        self.store, self.assets, self.root = store, assets, root
        self.runtime = runtime or Runtime.discover()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.jobs = {}
        for file in self.root.glob('*/job.json'):
            value = json.loads(file.read_text(encoding='utf-8'))
            if value['status'] not in TERMINAL:
                value.update(status='interrupted', error='服务已重启，请重新生成。')
            self.jobs[value['id']] = value
            self._persist(value)

    def _persist(self, job):
        path = self.root / job['id'] / 'job.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)

    def _set(self, job, **changes):
        with self.lock:
            job.update(changes)
            self._persist(job)

    def submit(self, project_id, revision, segment_ids):
        with self.lock:
            if self.active:
                raise Conflict('已有生成任务正在运行，请等待完成或取消。')
            p = self.store.get(project_id)
            if type(revision) is not int or revision != p['revision']:
                raise Conflict('工程已更新，请刷新后再生成。')
            selected = validate_render(p, segment_ids)
            self.runtime.require()
            job_id = uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            job = dict(id=job_id, project_id=project_id, segment_ids=segment_ids, status='running', stage='starting',
                       error=None, warnings=[], created_at=time.time())
            self.jobs[job_id] = job
            self.active = job_id
            self._persist(job)
            config = {'core': str(self.runtime.core), 'models': str(self.runtime.models), 'job_dir': str(directory.resolve()),
                      'source': str((self.assets / p['assets']['source']['id']).resolve()), 'segments': selected,
                      'existing_takes': []}
            if p['assets'].get('accompaniment'):
                config['accompaniment'] = str((self.assets / p['assets']['accompaniment']['id']).resolve())
            for line in p['lines']:
                take = p.get('renders', {}).get(line['id'])
                if line['id'] not in segment_ids and take and take['fingerprint'] == fingerprint(p, line):
                    config['existing_takes'].append({'start': line['start'], 'end': line['end'], 'path': str((self.assets / take['asset_id']).resolve())})
            (directory / 'input.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            threading.Thread(target=self._run, args=(job, copy.deepcopy(p)), daemon=True).start()
            return copy.deepcopy(job)

    def _run(self, job, snapshot):
        directory = self.root / job['id']
        error = None
        try:
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with self.lock:
                if job['status'] == 'cancelled':
                    return
                process = subprocess.Popen([str(self.runtime.python), '-u', str(ROOT / 'backend/model_worker.py'), '--config', str((directory / 'input.json').resolve())],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                           cwd=ROOT, env=env, creationflags=flags)
                self.process = process
            with (directory / 'worker.log').open('w', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line); log.flush()
                    if line.startswith('STUDIO_EVENT '):
                        value = json.loads(line[len('STUDIO_EVENT '):])
                        if value['stage'] == 'failed':
                            error = value.get('error')
                        if value['stage'] == 'alignment_warning':
                            with self.lock:
                                job['warnings'].append(value['message'])
                        if job['status'] != 'cancelled':
                            self._set(job, stage=value['stage'], position=value.get('position'), total=value.get('total'))
            code = process.wait()
            with self.lock:
                if job['status'] == 'cancelled':
                    return
                if code:
                    raise RuntimeError(error or '模型进程异常退出，请检查任务日志或显存占用。')
                self._publish(job, snapshot, directory)
        except Exception as exc:
            if job['status'] != 'cancelled':
                message = str(exc)
                if 'out of memory' in message.lower():
                    message = '显存不足，请释放其他应用占用或缩短片段后重试。'
                self._set(job, status='failed', error=message)
        finally:
            with self.lock:
                self.active = None
                self.process = None

    def _publish(self, job, snapshot, directory):
        outputs = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
        created = []
        with self.store.lock:
            current = self.store.get(snapshot['id'])
            expected = {x['id']: x for x in snapshot['lines'] if x['id'] in job['segment_ids']}
            current_lines = {x['id']: x for x in current['lines']}
            accepted = 0
            try:
                for output in outputs:
                    line = expected[output['segment_id']]
                    if line['id'] not in current_lines or fingerprint(current, current_lines[line['id']]) != fingerprint(snapshot, line):
                        continue
                    src = Path(output['path']).resolve()
                    if src.parent != directory.resolve() or not src.is_file() or src.stat().st_size < 44:
                        raise ValueError('生成产物路径或文件异常。')
                    asset_id = uuid4().hex + '.wav'
                    shutil.copyfile(src, self.assets / asset_id); created.append(asset_id)
                    take = {'asset_id': asset_id, 'fingerprint': fingerprint(snapshot, line), 'job_id': job['id'], 'warnings': list(job['warnings'])}
                    current.setdefault('renders', {})[line['id']] = take
                    current.setdefault('render_history', []).append({'segment_id': line['id'], **take})
                    accepted += 1
                if not accepted:
                    self._set(job, status='stale', error='歌词或素材已修改，旧结果未覆盖当前工程。')
                    return
                if composition_fingerprint(current) == composition_fingerprint(snapshot):
                    composite = {'fingerprint': composition_fingerprint(current)}
                    for name in ('rendered', 'mixed'):
                        src = directory / (name + '.wav')
                        if src.is_file():
                            asset_id = uuid4().hex + '.wav'
                            shutil.copyfile(src, self.assets / asset_id); created.append(asset_id)
                            composite[name] = asset_id
                    current['composite'] = composite
                current['revision'] += 1
                self.store._write(current)
            except Exception:
                for asset_id in created:
                    (self.assets / asset_id).unlink(missing_ok=True)
                raise
            if accepted < len(outputs):
                job['warnings'].append('部分片段在生成期间发生修改，其旧结果未覆盖当前编辑。')
            self._set(job, status='succeeded', stage='complete', accepted=accepted)

    def get(self, job_id):
        with self.lock:
            if not re.fullmatch(r'[a-f0-9]{32}', job_id) or job_id not in self.jobs:
                raise FileNotFoundError()
            return copy.deepcopy(self.jobs[job_id])

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise FileNotFoundError()
            if job['status'] not in TERMINAL:
                self._set(job, status='cancelled', stage='cancelled')
                if self.active == job_id and self.process and self.process.poll() is None:
                    self.process.terminate()
            return copy.deepcopy(job)

    def shutdown(self):
        with self.lock:
            if self.active:
                self.cancel(self.active)
