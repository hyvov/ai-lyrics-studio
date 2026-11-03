"""Prepare our own code/dependencies; never install into the shared Studio runtime."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.runtime import ROOT, Runtime, UPSTREAM_COMMIT


def system_proxy():
    if os.name != 'nt':
        return None
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
        try:
            if not winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                return None
            value = winreg.QueryValueEx(key, 'ProxyServer')[0]
        except FileNotFoundError:
            return None
    if '=' in value:
        entries = dict(part.split('=', 1) for part in value.split(';') if '=' in part)
        value = entries.get('https', entries.get('http', ''))
    return value if '://' in value else 'http://' + value if value else None


def main():
    runtime = Runtime.discover()
    if not runtime.python.is_file():
        raise SystemExit('未找到 SoulX Studio Python，请设置 SOULX_STUDIO_HOME 或 SOULX_PYTHON。')
    proxy = system_proxy()
    git = ['git'] + (['-c', 'http.proxy=' + proxy] if proxy else [])
    if not runtime.core.exists():
        subprocess.run(git + ['clone', 'https://github.com/Soul-AILab/SoulX-Singer.git', str(runtime.core)], check=True)
        subprocess.run(['git', '-C', str(runtime.core), 'checkout', '--detach', UPSTREAM_COMMIT], check=True)
    actual = subprocess.check_output(['git', '-C', str(runtime.core), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != UPSTREAM_COMMIT:
        raise SystemExit('上游代码版本不匹配；请指定独立的 SOULX_SOURCE_ROOT 后重新配置，现有代码未被覆盖。')
    command = [sys.executable, '-m', 'pip', '--python', str(runtime.python), 'install', '--target', str(ROOT / 'runtime/worker-deps'),
               '--upgrade', '--no-deps', '-r', str(ROOT / 'requirements-worker.txt')]
    if proxy:
        command += ['--proxy', proxy]
    subprocess.run(command, check=True)
    runtime.require()
    print('模型文件共用完成；权重未复制、未修改。')


if __name__ == '__main__':
    main()
