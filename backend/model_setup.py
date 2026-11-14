"""Download pinned public core models into the independent runtime."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import urllib.request
from uuid import uuid4

CATALOG = Path(__file__).with_name('model_catalog.json')

class DownloadCancelled(Exception):
    pass

class ModelSetup:
    def __init__(self, root, svc_root=None, music_root=None, runtime=None):
        self.runtime = runtime
        self.cancelled = threading.Event()
        self.root = Path(root).resolve()
        self.roots = {'models': self.root}
        if svc_root is not None: self.roots['svc'] = Path(svc_root).resolve()
        if music_root is not None: self.roots['music'] = Path(music_root).resolve()
        self.lock = threading.Lock()
        self.state = {'status':'idle','stage':'waiting','message':'歌声核心模型尚未校验。','done':0,'total':0,'current':''}
    def snapshot(self):
        with self.lock: return dict(self.state)
    def update(self, **values):
        with self.lock: self.state.update(values)
    def progress(self, **values):
        if self.cancelled.is_set(): raise DownloadCancelled()
        self.update(**values)
    def cancel(self):
        self.cancelled.set()
        self.update(message='正在暂停，已下载部分会保留。')
    def available_components(self):
        return ('singer', 'aligner', 'transcription') + (('svc',) if 'svc' in self.roots else ()) + (('separation',) if 'music' in self.roots else ())
    def start(self, components=None):
        components = ['singer', 'aligner'] if components is None else components
        if not isinstance(components, list) or not components or any(x not in self.available_components() for x in components) or len(set(components)) != len(components):
            raise ValueError('请选择有效的模型组件。')
        with self.lock:
            if self.state['status'] == 'running': raise ValueError('模型准备任务正在运行。')
            self.cancelled.clear()
            self.state.update(status='running',stage='checking',message='正在校验模型文件。',done=0,total=0,current='')
        threading.Thread(target=self._run,args=(tuple(components),),daemon=True).start()
    def _run(self, components):
        try:
            if self.runtime is not None:
                from backend.gpu_setup import install_gpu
                install_gpu(self.runtime, self.progress)
            if 'singer' in components and 'aligner' not in components:
                components = (*components, 'aligner')
            catalog=json.loads(CATALOG.read_text(encoding='utf8'))
            download_models(self.root,[item for item in catalog['files'] if item['component'] in components or any(x in components for x in item.get('also_for', []))],self.progress,roots=self.roots,repair=True)
            if self.runtime is not None and 'separation' in components:
                templates = self.runtime.studio / 'music/models'
                for source in templates.rglob('*.yaml'):
                    dest = self.roots['music'] / source.relative_to(templates)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, dest)
            if self.runtime is not None:
                ready = {'singer': self.runtime.status()['ready'],
                         'separation': bool(self.runtime.separation_config()),
                         'svc': bool(self.runtime.svc_config()),
                         'transcription': bool(self.runtime.transcription_config())}
                labels = {'singer':'歌声生成', 'separation':'人声分离', 'svc':'音色转换', 'transcription':'自动识词'}
                missing = [labels[x] for x in components if x in ready and not ready[x]]
                if missing:
                    raise ValueError('模型下载完成，但组件程序或配置不完整：'+'、'.join(missing)+'。请重新运行安装包修复基础组件。')
            self.update(status='succeeded',stage='complete',message='所选组件安装完成，模型已通过 SHA-256 校验。')
        except DownloadCancelled:
            self.update(status='cancelled',stage='paused',message='已暂停。再次点击下载可接着完成。')
        except Exception as exc:
            self.update(status='failed',stage='failed',message=str(exc))

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def download_models(root,files,progress,open_url=urllib.request.urlopen,roots=None,repair=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    roots = {'models':root} if roots is None else {k:Path(v).resolve() for k,v in roots.items()}
    total=sum(item['size'] for item in files); done=0
    progress(total=total,done=0)
    for item in files:
        component_root = roots.get(item.get('root', 'models'))
        if component_root is None: raise ValueError('未配置所需的组件目录。')
        component_root.mkdir(parents=True,exist_ok=True)
        relative=Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts: raise ValueError('模型清单路径无效。')
        target=component_root/relative
        if not target.resolve().is_relative_to(component_root): raise ValueError('模型路径超出运行目录。')
        size=item['size']; expected=item['sha256'];target.parent.mkdir(parents=True,exist_ok=True)
        progress(stage='checking',current=item['path'],message='正在校验 '+item['path'])
        if target.is_file():
            if target.stat().st_size!=size or sha256(target)!=expected:
                if not repair:
                    raise ValueError('已有文件校验失败，已保留原文件：'+str(target)+'。请先备份并移走该文件后重试。')
                os.replace(target, target.with_name(target.name + '.invalid-' + uuid4().hex[:8]))
            else:
                done+=size;progress(done=done);continue
        part=target.with_name(target.name+'.download')
        offset=part.stat().st_size if part.is_file() else 0
        if offset>size: raise ValueError('未完成下载的文件大小异常，请备份后移走：'+str(part))
        if shutil.disk_usage(target.parent).free < size-offset+256*1024*1024:
            raise OSError('磁盘空间不足，无法下载 '+item['path'])
        if offset<size:
            req=urllib.request.Request(item['url'],headers={'User-Agent':'CishengStudio/1.0',**({'Range':f'bytes={offset}-'} if offset else {})})
            progress(stage='downloading',message='正在下载 '+item['path'],done=done+offset)
            with open_url(req,timeout=60) as response:
                status=getattr(response,'status',response.getcode())
                if offset and status == 206 and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                    raise ValueError('服务器返回了无效的续传范围，请重试。')
                if offset and status != 206:
                    offset=0
                with part.open('ab' if offset else 'wb') as out:
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        out.write(chunk);offset+=len(chunk)
                        if offset>size:raise ValueError('下载文件超过预期大小。')
                        progress(done=done+offset)
        progress(stage='verifying',message='正在校验 '+item['path'])
        if not part.is_file() or part.stat().st_size != size:
            raise ValueError('下载不完整，可重试续传：'+item['path'])
        if sha256(part) != expected:
            backup = part.with_name(part.name + '.invalid-' + uuid4().hex[:8])
            os.replace(part, backup)
            raise ValueError('下载文件校验失败，已保留异常文件并允许重试：'+item['path'])
        os.replace(part,target);done+=size;progress(done=done)
