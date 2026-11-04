"""Windows desktop shell for the existing local AI Lyrics Studio workspace."""
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

URL = 'http://127.0.0.1:18733'


def workspace():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def request(path):
    with urlopen(URL + path, timeout=2) as response:
        return response.read()


def is_studio():
    try:
        page = request('/').decode('utf-8')
        session = json.loads(request('/api/session'))
        return '<title>AI Lyrics Studio</title>' in page and bool(session.get('token'))
    except (OSError, ValueError, URLError):
        return False


def python_path(root):
    venv = root / '.venv' / 'Scripts' / 'python.exe'
    if venv.is_file():
        return str(venv)
    executable = shutil.which('python.exe')
    if not executable:
        raise RuntimeError('未找到 Python。请先按项目 README 安装依赖。')
    return executable


def ensure_server(root):
    if is_studio():
        return None
    # An occupied port is never killed or reused without recognizing its service.
    import socket
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex(('127.0.0.1', 18733)) == 0:
            raise RuntimeError('18733 端口已被其他程序占用，请关闭占用该端口的程序后重试。')
    if not (root / 'backend' / 'app.py').is_file():
        raise RuntimeError('找不到项目文件。请将客户端放在项目的 dist 文件夹内。')
    process = subprocess.Popen([python_path(root), '-m', 'backend.app'], cwd=root,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if is_studio():
            return process
        if process.poll() is not None:
            raise RuntimeError('本地服务启动失败，请检查项目依赖。')
        time.sleep(.25)
    process.terminate()
    raise RuntimeError('本地服务启动超时，请检查项目依赖。')


def shutdown_owned(process):
    if process is None or process.poll() is not None:
        return
    try:
        runtime = json.loads(request('/api/runtime'))
        if runtime.get('active_job'):
            # Keep an active model task alive if the desktop window is closed.
            return
    except (OSError, ValueError, URLError):
        return
    process.terminate()


def main():
    import webview
    root = workspace()
    process = None
    try:
        process = ensure_server(root)
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.create_window('AI Lyrics Studio', URL, width=1500, height=950,
                              min_size=(960, 680), background_color='#fcf7f1', text_select=True)
        webview.start(gui='edgechromium', private_mode=False,
                      storage_path=str(root / 'data' / 'desktop-webview'))
    finally:
        shutdown_owned(process)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'AI Lyrics Studio 启动失败', 0x10)
        raise SystemExit(1)
