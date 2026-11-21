"""Windows desktop shell for the existing local FunnySinger workspace."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

PORT = 18997
URL = f'http://127.0.0.1:{PORT}'

def installed_data_home(root):
    override = os.environ.get('CISHENG_USER_HOME')
    if override:
        return Path(override).resolve()
    default = Path(os.environ['LOCALAPPDATA']) / 'CiShengStudio'
    config = root / 'storage.ini'
    if os.name == 'nt' and config.is_file():
        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetPrivateProfileStringW('Storage', 'Home', '', buffer, len(buffer), str(config))
        if buffer.value:
            candidate = Path(buffer.value)
            if not candidate.is_absolute():
                raise ValueError('数据目录配置无效，请重新运行安装程序选择绝对路径。')
            return candidate.resolve()
    return default.resolve()


def workspace():
    if getattr(sys, 'frozen', False):
        folder = Path(sys.executable).resolve().parent
        return folder if (folder / 'release.json').is_file() else folder.parent
    return Path(__file__).resolve().parent.parent


def configure_installation(root):
    """Installed programs never put projects or downloaded weights in program files."""
    if not (root / 'release.json').is_file():
        return Path(os.environ.get('AI_LYRICS_DATA', root / 'data'))
    home = installed_data_home(root)
    values = {
        'AI_LYRICS_DATA': home / 'data',
        'AI_LYRICS_RUNTIME_HOME': root / 'runtime/independent',
        'AI_LYRICS_PYTHON': root / 'runtime/independent/python/python.exe',
        'AI_LYRICS_MODELS_ROOT': home / 'models',
        'AI_LYRICS_COMPONENTS_HOME': home / 'components',
        'AI_LYRICS_SOURCE_ROOT': root / 'runtime/SoulX-Singer',
        'AI_LYRICS_HFA_SOURCE': root / 'runtime/Vocal2Midi',
        'AI_LYRICS_HFA_MODEL': root / 'runtime/independent/components/hfa/model',
        'AI_LYRICS_HFA_PYTHON': root / 'runtime/independent/components/hfa/python',
        'AI_LYRICS_GPU_PACKAGES': home / 'gpu-packages',
        'AI_LYRICS_DOWNLOAD_CACHE': home / 'downloads',
        'MPLCONFIGDIR': home / 'cache/matplotlib',
        'NUMBA_CACHE_DIR': home / 'cache/numba',
    }
    os.environ.update({k: str(v) for k, v in values.items()})
    os.environ.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    data = values['AI_LYRICS_DATA']; data.mkdir(parents=True, exist_ok=True)
    return data


def request(path):
    with urlopen(URL + path, timeout=2) as response:
        return response.read()


def is_studio():
    try:
        page = request('/').decode('utf-8')
        session = json.loads(request('/api/session'))
        return ('<title>FunnySinger</title>' in page and bool(session.get('token'))
                and session.get('installation') == str(workspace())
                and session.get('data_root') == str(Path(os.environ.get('AI_LYRICS_DATA', workspace() / 'data')).resolve()))
    except (OSError, ValueError, URLError):
        return False


def python_path(root):
    bundled = root / 'runtime/independent/python/python.exe'
    if bundled.is_file():
        return str(bundled)
    venv = root / '.venv' / 'Scripts' / 'python.exe'
    if venv.is_file():
        return str(venv)
    executable = shutil.which('python.exe')
    if not executable:
        raise RuntimeError('未找到 Python。请先按项目 README 安装依赖。')
    return executable


def ensure_server(root):
    global PORT, URL
    if is_studio():
        return None
    # An occupied port is never killed or reused without recognizing its service.
    import socket
    for port in range(18997, 19050):
        PORT = port; URL = f'http://127.0.0.1:{port}'
        if is_studio():
            return None
        with socket.socket() as probe:
            probe.settimeout(.2)
            if probe.connect_ex(('127.0.0.1', port)) != 0:
                break
    else:
        raise RuntimeError('无法分配本地服务端口，请重启客户端。')
    if not (root / 'backend' / 'app.py').is_file():
        raise RuntimeError('找不到项目文件。请将客户端放在项目的 dist 文件夹内。')
    logs = Path(os.environ.get('AI_LYRICS_DATA', root / 'data')) / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / 'server.log'
    with log.open('ab') as output:
        process = subprocess.Popen([python_path(root), '-m', 'backend.app', '--port', str(PORT)], cwd=root,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if is_studio():
            return process
        if process.poll() is not None:
            raise RuntimeError('本地服务启动失败，诊断日志：' + str(log))
        time.sleep(.25)
    process.terminate()
    raise RuntimeError('本地服务启动超时，请检查项目依赖。')


def shutdown_owned(process):
    if process is None or process.poll() is not None:
        return
    try:
        runtime = json.loads(request('/api/runtime'))
        if runtime.get('active_job') or runtime.get('active_model_setup') or runtime.get('active_update'):
            # Keep an active model task or model download alive when the window closes.
            return
    except (OSError, ValueError, URLError):
        return
    process.terminate()


def main():
    import webview
    root = workspace()
    if os.name == 'nt':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CiShengStudio.Desktop')
    data = configure_installation(root)
    process = None
    try:
        process = ensure_server(root)
        webview.settings['ALLOW_DOWNLOADS'] = True
        # Force a fresh document on launch even with a persistent WebView profile.
        webview.create_window('FunnySinger', f'{URL}/?launch={time.time_ns()}', width=1500, height=950,
                              min_size=(960, 680), background_color='#fcf7f1', text_select=True)
        webview.start(gui='edgechromium', private_mode=False,
                      icon=str(root / 'design' / 'app.ico'),
                      storage_path=str(data / 'desktop-webview'))
    finally:
        shutdown_owned(process)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'FunnySinger 启动失败', 0x10)
        raise SystemExit(1)
