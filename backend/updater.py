"""Check official releases and prepare verified Windows installer updates."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import urllib.request

API='https://api.github.com/repos/hyvov/ai-lyrics-studio/releases/latest'
VERSION=re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')

def version(value):
    m=VERSION.fullmatch(str(value))
    if not m:raise ValueError('版本号格式无效。')
    return tuple(map(int,m.groups()))

class Updater:
    def __init__(self,root,data,jobs,model_setup=None):
        self.root=Path(root);self.data=Path(data);self.jobs=jobs;self.lock=threading.Lock()
        self.model_setup=model_setup
        self.state={'status':'idle','message':'尚未检查更新。','progress':0}
    def update(self,**kwargs):
        with self.lock:self.state.update(kwargs)
    def snapshot(self):
        with self.lock:return dict(self.state)
    def busy(self):
        return bool(self.jobs.active or (self.model_setup and self.model_setup.snapshot()['status']=='running'))
    def start(self, download=False):
        with self.lock:
            if self.state['status'] in ('checking','downloading','installing'):return
            self.state.update(status='checking',message='正在检查 GitHub 版本…',progress=0)
        threading.Thread(target=self.run,args=(download,),daemon=True).start()
    def run(self, download=True):
        try:
            release_file=self.root/'release.json'
            if not release_file.is_file():
                self.update(status='development',message='开发版不自动安装更新，请使用打包后的客户端。');return
            current=json.loads(release_file.read_text(encoding='utf8'))['version']
            req=urllib.request.Request(API,headers={'User-Agent':'CiShengStudio/1.0','Accept':'application/vnd.github+json'})
            with urllib.request.urlopen(req,timeout=20) as r: release=json.load(r)
            latest=release['tag_name'];v=version(latest)
            if v<=version(current):self.update(status='current',message='当前已是最新版本。',current=current,latest=latest);return
            if not download:
                self.update(status='available',message='发现新版本，是否更新？',current=current,latest=latest);return
            if self.busy():
                self.update(status='deferred',message='发现新版本；当前任务结束后可再次检查更新。',current=current,latest=latest);return
            names=[prefix+'.'.join(map(str,v))+'-setup.exe' for prefix in ('FunnySinger-', 'CiSheng-Studio-')]
            asset=next((a for name in names for a in release.get('assets',[]) if a.get('name')==name and a.get('state')=='uploaded'),None)
            name=asset['name'] if asset else names[0]
            if not asset:raise ValueError('新版本没有 Windows 安装包。')
            url=asset.get('browser_download_url','');expected=asset.get('digest','')
            if not url.startswith('https://github.com/hyvov/ai-lyrics-studio/releases/download/') or not re.fullmatch(r'sha256:[0-9a-f]{64}',expected):raise ValueError('安装包来源或校验信息无效。')
            size=asset.get('size');
            if not isinstance(size,int) or size<=0 or size>4*1024**3:raise ValueError('安装包大小异常。')
            directory=self.data/'updates';directory.mkdir(parents=True,exist_ok=True)
            part=directory/(name+'.download');target=directory/name
            self.update(status='downloading',message='发现新版，正在下载 '+latest,current=current,latest=latest,progress=0)
            digest=hashlib.sha256();count=0
            req=urllib.request.Request(url,headers={'User-Agent':'CiShengStudio/1.0'})
            with urllib.request.urlopen(req,timeout=60) as src,part.open('wb') as dst:
                while True:
                    block=src.read(1024*1024)
                    if not block:break
                    count+=len(block)
                    if count>size:raise ValueError('安装包大小异常。')
                    dst.write(block);digest.update(block);self.update(progress=int(count*100/size))
            if count!=size or digest.hexdigest()!=expected[7:]:raise ValueError('更新文件完整性校验失败。')
            os.replace(part,target)
            if self.busy():
                self.update(status='ready',message='新版已下载；当前任务结束后可点击更新。',path=str(target),sha256=expected[7:]);return
            # Ask the installer to perform the normal interactive upgrade. The user
            # retains control over closing the application and completing setup.
            self.update(status='ready',message='新版已下载并校验，请点击安装更新。',path=str(target),sha256=expected[7:],progress=100)
        except Exception as exc:self.update(status='failed',message='更新检查或下载失败：'+str(exc))
    def install(self):
        state=self.snapshot();path=Path(state.get('path','')).resolve();directory=(self.data/'updates').resolve()
        if state['status']!='ready' or not path.is_file() or path.parent!=directory:raise ValueError('没有已校验的更新安装包。')
        if self.busy():raise ValueError('模型任务或模型下载运行中，请等待完成后再更新。')
        with path.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest()!=state.get('sha256'):
                raise ValueError('安装包发生变化，请重新检查并下载更新。')
        if os.name!='nt':raise ValueError('自动更新目前支持 Windows。')
        subprocess.Popen([str(path)],cwd=str(directory),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.update(status='installing',message='已打开更新安装程序，请按提示完成升级。')
