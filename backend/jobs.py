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
from backend.media import source_issue
from backend.singing import composition_fingerprint, fingerprint, svc_fingerprint, validate_render, validate_alignment
from backend.timeline import alignment_fingerprint, cached_alignment, validate_timings
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

    def submit(self, project_id, revision, segment_ids, kind='render'):
        with self.lock:
            if self.active:
                raise Conflict('已有生成任务正在运行，请等待完成或取消。')
            p = self.store.get(project_id)
            if type(revision) is not int or revision != p['revision']:
                raise Conflict('工程已更新，请刷新后再生成。')
            issue = source_issue(p, self.assets)
            if issue:
                raise ValueError(issue)
            if kind not in ('render','align'):
                raise ValueError('无效任务类型。')
            selected = (validate_alignment if kind == 'align' else validate_render)(p, segment_ids)
            self.runtime.require()
            job_id = uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            job = dict(id=job_id, kind=kind, project_id=project_id, segment_ids=segment_ids, status='running', stage='starting',
                       error=None, warnings=[], created_at=time.time())
            self.jobs[job_id] = job
            self.active = job_id
            self._persist(job)
            config = {'core': str(self.runtime.core), 'models': str(self.runtime.models), 'job_dir': str(directory.resolve()),
                      'source': str((self.assets / p['assets']['source']['id']).resolve()), 'segments': selected,
                      'existing_takes': [], 'hfa': self.runtime.hfa_config(), 'operation':kind,
                      'saved_alignments':{x['id']:cached_alignment(p,x) for x in selected if kind == 'render' and cached_alignment(p,x)}}
            if p['assets'].get('accompaniment'):
                config['accompaniment'] = str((self.assets / p['assets']['accompaniment']['id']).resolve())
            for line in p['lines']:
                take = p.get('renders', {}).get(line['id'])
                if line['id'] not in segment_ids and take and take['fingerprint'] == fingerprint(p, line):
                    config['existing_takes'].append({'start': line['start'], 'end': line['end'], 'path': str((self.assets / take['asset_id']).resolve())})
            (directory / 'input.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            threading.Thread(target=self._run, args=(job, copy.deepcopy(p)), daemon=True).start()
            return copy.deepcopy(job)

    def submit_separation(self, project_id, revision):
        with self.lock:
            if self.active:
                raise Conflict('已有模型任务正在运行，请等待完成或取消。')
            p = self.store.get(project_id)
            if type(revision) is not int or revision != p['revision']:
                raise Conflict('工程已更新，请刷新后再试。')
            song = p.get('assets', {}).get('song')
            if not song or not (self.assets / song['id']).is_file():
                raise ValueError('请先导入需要分离的歌曲。')
            if song['duration'] > 1800:
                raise ValueError('歌曲不能超过 30 分钟。')
            if any(line['end'] > song['duration'] + .001 for line in p['lines']):
                raise ValueError('歌曲短于现有歌词，请先调整歌词时间或新建工程。')
            separation = self.runtime.separation_config()
            if not separation:
                raise ValueError('本地 SoulX Studio 分离组件或模型不完整，请先在原软件安装分离模型。')
            job_id = uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            job = dict(id=job_id, kind='separate', project_id=project_id, segment_ids=[], status='running', stage='starting',
                       error=None, warnings=[], created_at=time.time())
            self.jobs[job_id] = job; self.active = job_id; self._persist(job)
            config = {**separation, 'source': str((self.assets / song['id']).resolve()), 'job_dir': str(directory.resolve())}
            (directory / 'input.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            threading.Thread(target=self._run, args=(job, copy.deepcopy(p)), daemon=True).start()
            return copy.deepcopy(job)

    def submit_transcription(self, project_id, revision, replace=False):
        with self.lock:
            if self.active:
                raise Conflict('已有模型任务正在运行，请等待完成或取消。')
            p = self.store.get(project_id)
            if type(revision) is not int or revision != p['revision']:
                raise Conflict('工程已更新，请刷新后再试。')
            source = p.get('assets', {}).get('source')
            if not source or not (self.assets / source['id']).is_file():
                raise ValueError('请先导入或分离原唱人声。')
            if p['lines'] and not replace:
                raise ValueError('工程已有歌词；确认替换后才能自动识词。')
            issue=source_issue(p,self.assets)
            if issue: raise ValueError(issue)
            asr=self.runtime.transcription_config()
            if not asr: raise ValueError('本地 FireRedASR2 模型不完整。')
            job_id=uuid4().hex;directory=self.root/job_id;directory.mkdir()
            job=dict(id=job_id,kind='transcribe',project_id=project_id,segment_ids=[],status='running',stage='starting',error=None,warnings=[],created_at=time.time())
            self.jobs[job_id]=job;self.active=job_id;self._persist(job)
            config={**asr,'source':str((self.assets/source['id']).resolve()),'job_dir':str(directory.resolve())}
            (directory/'input.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
            threading.Thread(target=self._run,args=(job,copy.deepcopy(p)),daemon=True).start()
            return copy.deepcopy(job)

    def submit_svc(self, project_id, revision, target_kind):
        with self.lock:
            if self.active:raise Conflict('已有模型任务正在运行，请等待完成或取消。')
            p=self.store.get(project_id)
            if type(revision) is not int or revision!=p['revision']:raise Conflict('工程已更新，请刷新后再试。')
            if target_kind not in ('source','rendered'):raise ValueError('请选择原唱或改词演唱作为转换目标。')
            ref=p.get('assets',{}).get('reference');source=p.get('assets',{}).get('source')
            if not ref or not source:raise ValueError('请先导入目标人声和参考人声。')
            issue=source_issue(p,self.assets)
            if issue:raise ValueError(issue)
            if not 1 <= ref['duration'] <= 30:raise ValueError('参考人声需要 1–30 秒。')
            if p['duration']>1800:raise ValueError('音频不能超过 30 分钟。')
            composite=p.get('composite',{})
            if target_kind=='rendered' and (not composite.get('rendered') or composite.get('fingerprint')!=composition_fingerprint(p)):
                raise ValueError('改词演唱不存在或已过期。')
            target=source['id'] if target_kind=='source' else composite['rendered']
            for asset_id in (ref['id'],target):
                if not (self.assets/asset_id).is_file():raise ValueError('音频素材不存在。')
            svc=self.runtime.svc_config()
            if not svc:raise ValueError('本地 SoulX Studio 音色转换组件或模型不完整。')
            job_id=uuid4().hex;directory=self.root/job_id;directory.mkdir()
            job=dict(id=job_id,kind='svc',project_id=project_id,segment_ids=[],target_kind=target_kind,status='running',stage='starting',error=None,warnings=[],created_at=time.time())
            self.jobs[job_id]=job;self.active=job_id;self._persist(job)
            config={**svc,'reference':str((self.assets/ref['id']).resolve()),'target':str((self.assets/target).resolve()),'job_dir':str(directory.resolve())}
            if p.get('assets',{}).get('accompaniment'):
                config['accompaniment']=str((self.assets/p['assets']['accompaniment']['id']).resolve())
            (directory/'input.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
            threading.Thread(target=self._run,args=(job,copy.deepcopy(p)),daemon=True).start()
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
                worker = {'separate':'separation_worker.py','transcribe':'transcribe_worker.py','svc':'svc_worker.py'}.get(job.get('kind'),'model_worker.py')
                process = subprocess.Popen([str(self.runtime.python), '-u', str(ROOT / 'backend' / worker), '--config', str((directory / 'input.json').resolve())],
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
                if job.get('kind') == 'separate':
                    self._publish_separation(job, snapshot, directory)
                elif job.get('kind') == 'transcribe':
                    self._publish_transcription(job, snapshot, directory)
                elif job.get('kind') == 'svc':
                    self._publish_svc(job,snapshot,directory)
                elif job.get('kind') == 'align':
                    self._publish_alignment(job, snapshot, directory, final=True)
                else:
                    self._publish_alignment(job, snapshot, directory, final=False)
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

    def _publish_svc(self,job,snapshot,directory):
        target_kind=job['target_kind']
        with self.store.lock:
            current=self.store.get(snapshot['id'])
            if svc_fingerprint(current,target_kind)!=svc_fingerprint(snapshot,target_kind):
                self._set(job,status='stale',error='转换期间参考音频或目标音频已改变，旧结果未覆盖。');return
            path=directory/'svc.wav'
            if not path.is_file() or path.stat().st_size<=44:raise ValueError('音色转换结果不完整。')
            asset_id=uuid4().hex+'.wav';created=[]
            try:
                shutil.copyfile(path,self.assets/asset_id);created.append(asset_id)
                current['svc']={'asset_id':asset_id,'target_kind':target_kind,'fingerprint':svc_fingerprint(current,target_kind)}
                old_backing=snapshot.get('assets',{}).get('accompaniment')
                mix_path=directory/'svc-mixed.wav'
                if old_backing and old_backing==current.get('assets',{}).get('accompaniment') and mix_path.is_file() and mix_path.stat().st_size>44:
                    mix_id=uuid4().hex+'.wav';shutil.copyfile(mix_path,self.assets/mix_id);created.append(mix_id)
                    current['svc'].update(mixed_asset_id=mix_id,accompaniment_id=old_backing['id'])
                current['revision']+=1;self.store._write(current)
            except Exception:
                for created_id in created:(self.assets/created_id).unlink(missing_ok=True)
                raise
            self._set(job,status='succeeded',stage='complete')

    def _publish_transcription(self, job, snapshot, directory):
        from backend.store import validate_project
        drafts=json.loads((directory/'transcription-result.json').read_text(encoding='utf-8'))
        if not isinstance(drafts,list) or not drafts: raise ValueError('识别结果为空。')
        with self.store.lock:
            current=self.store.get(snapshot['id'])
            if current.get('assets',{}).get('source') != snapshot.get('assets',{}).get('source') or current['lines'] != snapshot['lines']:
                self._set(job,status='stale',error='识词期间原唱或歌词已改变，结果未覆盖当前工程。');return
            lines=[dict(id=uuid4().hex,original=x['text'],text=x['text'],start=x['start'],end=x['end'],mode='melody',restore=False,needs_review=True) for x in drafts]
            validate_project({**current,'lines':lines})
            current['lines']=lines;current['revision']+=1;self.store._write(current)
            self._set(job,status='succeeded',stage='complete',accepted=len(lines))

    def _publish_separation(self, job, snapshot, directory):
        result = json.loads((directory / 'separation-result.json').read_text(encoding='utf-8'))
        duration = float(result['duration'])
        with self.store.lock:
            current = self.store.get(snapshot['id'])
            if any(current.get('assets', {}).get(k) != snapshot.get('assets', {}).get(k) for k in ('song','source','accompaniment')):
                self._set(job, status='stale', error='分离期间音频素材已改变，结果未覆盖当前工程。'); return
            if not 0 < duration <= 1800 or any(x['end'] > duration + .001 for x in current['lines']):
                self._set(job, status='stale', error='分离结果与当前歌词时间不兼容，未覆盖当前工程。'); return
            created = []
            try:
                for kind, name in [('source','分离人声.wav'), ('accompaniment','分离伴奏.wav')]:
                    path = directory / (kind + '.wav')
                    if not path.is_file() or path.stat().st_size <= 44:
                        raise ValueError('分离结果不完整。')
                    asset_id = uuid4().hex + '.wav'
                    shutil.copyfile(path, self.assets / asset_id); created.append(asset_id)
                    current.setdefault('assets', {})[kind] = {'id':asset_id, 'name':name, 'duration':duration}
                current['duration'] = duration
                current['revision'] += 1
                self.store._write(current)
            except Exception:
                for asset_id in created:
                    (self.assets / asset_id).unlink(missing_ok=True)
                raise
            self._set(job, status='succeeded', stage='complete')

    def _publish_alignment(self, job, snapshot, directory, final):
        path = directory / 'alignment-result.json'
        if not path.is_file():
            if final:
                raise ValueError('对齐任务未产生时间轴。')
            return
        results = json.loads(path.read_text(encoding='utf-8'))
        original = {x['id']:x for x in snapshot['lines']}
        with self.store.lock:
            current = self.store.get(snapshot['id'])
            lines = {x['id']:x for x in current['lines']}
            accepted = 0
            for result in results:
                key = result['segment_id']
                if key not in original or key not in lines or alignment_fingerprint(current,lines[key]) != alignment_fingerprint(snapshot,original[key]):
                    continue
                timing = validate_timings(lines[key],result['unit_timings'])
                # A manual edit made during the task always wins over automatic output.
                now, before = cached_alignment(current,lines[key]), cached_alignment(snapshot,original[key])
                if now and now.get('engine') == 'manual' and now != before:
                    continue
                current.setdefault('alignments',{})[key] = {'fingerprint':alignment_fingerprint(current,lines[key]),
                    'engine':result['engine'],'unit_timings':timing}
                if final:
                    current.get('renders',{}).pop(key,None)
                accepted += 1
            if accepted:
                if final:
                    current.pop('composite',None)
                current['revision'] += 1
                self.store._write(current)
            if final:
                self._set(job,status='succeeded' if accepted else 'stale',stage='complete',accepted=accepted,
                          error=None if accepted else '原词或边界已修改，旧对齐结果未覆盖。')

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
