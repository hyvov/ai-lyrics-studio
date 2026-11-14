"""Install hash-pinned Windows CUDA wheels into a private, replaceable directory."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile
from uuid import uuid4

WHEELS = [
    {'path': 'torch-2.8.0+cu128-cp312-cp312-win_amd64.whl',
     'url': 'https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp312-cp312-win_amd64.whl',
     'size': 3461384651, 'sha256': '0ad925202387f4e7314302a1b4f8860fa824357f9b1466d7992bf276370ebcff'},
    {'path': 'torchaudio-2.8.0+cu128-cp312-cp312-win_amd64.whl',
     'url': 'https://download-r2.pytorch.org/whl/cu128/torchaudio-2.8.0%2Bcu128-cp312-cp312-win_amd64.whl',
     'size': 4673203, 'sha256': 'cce3a60cd9a97f7360c8f95504ac349311fb7d6b9b826135936764f4de5f782d'},
]


def extract_wheel(archive, destination, progress):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as wheel:
        for entry in wheel.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('GPU 安装包包含不安全路径。')
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError('GPU 文件路径无效。')
            # Only Python modules and package metadata are needed, no global scripts.
            if any(part.endswith('.data') for part in path.parts):
                continue
            progress(stage='installing', message='正在安装 GPU 组件', current=entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with wheel.open(entry) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 4 * 1024 * 1024)


def probe_gpu(python, packages=None):
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    if packages is not None:
        env['AI_LYRICS_GPU_PACKAGES'] = str(packages)
    code = ('import sys; '
            + (f'sys.path.insert(0,{str(packages)!r}); ' if packages is not None else '')
            + 'import torch,torchaudio; '
            'assert torch.__version__=="2.8.0+cu128",torch.__version__; '
            'assert torch.cuda.is_available(),"未检测到可用的 NVIDIA 显卡，请安装或更新显卡驱动"; '
            'x=torch.ones(8,device="cuda"); assert x.sum().item()==8; print(torch.cuda.get_device_name(0))')
    result = subprocess.run([str(python), '-c', code], env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=120,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('GPU 环境自检失败：' + (result.stderr or result.stdout)[-1800:])
    return result.stdout.strip()


def install_gpu(runtime, progress):
    from backend.model_setup import download_models
    external = os.environ.get('AI_LYRICS_GPU_PACKAGES')
    if runtime.gpu_installed():
        progress(stage='checking', message='正在检查 GPU 运行环境', current='CUDA 12.8')
        return probe_gpu(runtime.python, external)
    if not external:
        raise ValueError('GPU 安装目录尚未配置，请使用独立安装版客户端。')
    target = Path(external).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(os.environ.get('AI_LYRICS_DOWNLOAD_CACHE', target.parent / 'downloads'))
    if shutil.disk_usage(target.parent).free < 10 * 1024**3:
        raise ValueError('首次安装 GPU 组件需要至少 10 GiB 可用空间（包含下载和解压）。')
    download_models(cache, WHEELS, progress, repair=True)
    staging = target.with_name(target.name + '.installing-' + uuid4().hex[:12])
    staging.mkdir()
    try:
        for item in WHEELS:
            extract_wheel(cache / item['path'], staging, progress)
        progress(stage='checking', message='正在验证 CUDA 运算', current='GPU 自检')
        device = probe_gpu(runtime.python, staging)
        (staging / 'installation.json').write_text(json.dumps({'wheels': WHEELS, 'device': device}, ensure_ascii=False, indent=2), encoding='utf-8')
        if target.exists():
            os.replace(target, target.with_name(target.name + '.previous-' + uuid4().hex[:12]))
        os.replace(staging, target)
        return device
    finally:
        if staging.is_dir() and staging.resolve().parent == target.parent:
            shutil.rmtree(staging)
