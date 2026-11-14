"""Create an independent personal Windows release from the validated runtime snapshot.

Never includes projects, user media, big model weights or the development Git trees.
Each input file and package version is recorded for an auditable rebuild.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.2.2'
SKIP_NAMES = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.soulx-verified'}
SKIP_PACKAGES = {'torch', 'torchaudio', 'torio'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_tree(source, target, predicate=lambda path: True):
    for base, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES and not Path(base, d).is_symlink() and predicate(Path(base, d))]
        for name in files:
            src = Path(base, name)
            if name in SKIP_NAMES or src.suffix in ('.pyc', '.pyo') or src.is_symlink() or not predicate(src):
                continue
            dst = target / src.relative_to(source)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def stage(target):
    if target.exists():
        raise FileExistsError('为避免混入旧文件，请为构建指定一个新的空目录：' + str(target))
    target.mkdir(parents=True)
    for name in ('backend', 'design'):
        copy_tree(ROOT / name, target / name)
    runtime = ROOT / 'runtime/independent'
    python = runtime / 'python'
    def small_python(path):
        relative = path.relative_to(python).parts
        if len(relative) >= 3 and relative[:2] == ('Lib', 'site-packages'):
            package = relative[2]
            if package in SKIP_PACKAGES or package.startswith(('torch-', 'torchaudio-')):
                return False
        return path.name not in ('ffplay.exe', 'ffprobe.exe')
    print('Staging standalone Python and small dependencies...', flush=True)
    copy_tree(python, target / 'runtime/independent/python', small_python)
    # Keep the application's Python isolation hook under version control.
    shutil.copy2(ROOT / 'desktop/sitecustomize.py', target / 'runtime/independent/python/Lib/site-packages/sitecustomize.py')
    for name in ('SoulX-Singer', 'Vocal2Midi', 'worker-deps'):
        copy_tree(ROOT / 'runtime' / name, target / 'runtime' / name,
                  lambda p: not p.name in ('assets', 'example', 'ffmpeg.exe'))
    copy_tree(runtime / 'components/hfa', target / 'runtime/independent/components/hfa')
    wrapper = target / 'runtime/independent/components/svc/soulx_svc_model.py'
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime / 'components/svc/soulx_svc_model.py', wrapper)
    copy_tree(runtime / 'music/deps', target / 'runtime/independent/music/deps')
    copy_tree(runtime / 'music/models', target / 'runtime/independent/music/models',
              lambda p: p.is_dir() or p.suffix in ('.yaml', '.yml', '.json', '.md', '.txt'))
    support = target / 'support'; support.mkdir()
    bootstrap = ROOT / 'build/downloads/MicrosoftEdgeWebview2Setup.exe'
    if digest(bootstrap) != 'cf2f2445fef0c5c14e372045f84998771eeb8bb01d59be4afd00692fa160082e':
        raise ValueError('WebView2 引导程序与已验证版本不符。')
    shutil.copy2(bootstrap, support / bootstrap.name)
    notices = target / 'third-party'; notices.mkdir()
    shutil.copy2(ROOT / 'docs/model-distribution.md', notices / 'model-distribution.md')
    shutil.copy2(ROOT / 'docs/installer-notice.txt', notices / 'NOTICE.txt')
    packages = sorted([{'name': d.metadata.get('Name'), 'version': d.version}
                       for d in importlib.metadata.distributions(path=[str(python / 'Lib/site-packages')])
                       if (d.metadata.get('Name') or '').lower() not in ('torch', 'torchaudio')], key=lambda x:x['name'] or '')
    (notices / 'python-packages.json').write_text(json.dumps(packages, indent=2), encoding='utf-8')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    (target / 'release.json').write_text(json.dumps({'name':'词声 Studio','version':VERSION,
        'git_base':revision,'runtime':'Python 3.12 / PyTorch 2.8.0 CUDA 12.8',
        'distribution':'independent-personal-build'}, ensure_ascii=False, indent=2), encoding='utf-8')


def inventory(target):
    files = {}
    for path in sorted(target.rglob('*')):
        if path.is_file() and path.name != 'files.sha256.json':
            files[path.relative_to(target).as_posix()] = {'size':path.stat().st_size, 'sha256':digest(path)}
    (target / 'files.sha256.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Inventory: {len(files)} files, {sum(x["size"] for x in files.values())/1024**3:.2f} GiB', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, default=ROOT / 'build/release')
    parser.add_argument('--compile-only', action='store_true')
    args = parser.parse_args(); target = args.stage.resolve()
    if not args.compile_only:
        stage(target)
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'build/build-deps'), PYTHONUTF8='1')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
                    '--name', '词声 Studio', '--icon', str(ROOT / 'desktop/app.ico'), '--distpath', str(ROOT / 'build/launcher'),
                    '--workpath', str(ROOT / 'build/freeze'), '--specpath', str(ROOT / 'build'),
                    '--exclude-module', 'torch', '--exclude-module', 'matplotlib', '--exclude-module', 'numpy',
                    str(ROOT / 'desktop/launcher.py')], env=env, cwd=ROOT, check=True)
    shutil.copy2(ROOT / 'build/launcher/词声 Studio.exe', target / '词声 Studio.exe')
    inventory(target)
    compiler = ROOT / 'build/inno/ISCC.exe'
    subprocess.run([str(compiler), '/Qp', '/DPayload='+str(target), '/DOutput='+str(ROOT / 'dist'),
                    str(ROOT / 'desktop/installer.iss')], cwd=ROOT, check=True)
    output = ROOT / f'dist/CiSheng-Studio-{VERSION}-setup.exe'
    output.with_suffix('.exe.sha256').write_text(digest(output)+'  '+output.name+'\n', encoding='utf-8')
    print('Installer: '+str(output), flush=True)


if __name__ == '__main__': main()
