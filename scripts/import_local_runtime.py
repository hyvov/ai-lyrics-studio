"""Copy a local installation into this application's independent runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[1]

def version(path):
    value = json.loads(path.read_text(encoding='utf-8-sig'))['version']
    if not isinstance(value, str) or not value or Path(value).name != value or '/' in value or '\\' in value:
        raise ValueError('无效的运行时版本')
    return value

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def import_runtime(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise FileExistsError(f'目标已存在，不会覆盖：{target}')
    runtime_version = version(source / 'runtime/current.json')
    svc_version = version(source / 'runtime/components/svc/current.json')
    installed = source / 'runtime/versions' / runtime_version
    entries = [(installed / 'python', 'python'), (installed / 'core/pretrained_models', 'pretrained_models'),
               (source / 'runtime/components/hfa/versions/1.0.0', 'components/hfa'),
               (source / 'runtime/components/svc/versions' / svc_version, 'components/svc'),
               (source / 'backend/soulx_svc_model.py', 'components/svc/soulx_svc_model.py'),
               (source / 'data/music/deps', 'music/deps'), (source / 'data/music/models', 'music/models')]
    total = 0
    for src, _ in entries:
        if not src.exists() or not src.resolve().is_relative_to(source):
            raise ValueError(f'源组件不存在或不在安装目录内：{src}')
        for base, dirs, files in os.walk(src) if src.is_dir() else [(src.parent, [], [src.name])]:
            for name in dirs + files:
                p = Path(base) / name
                if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
                    raise ValueError(f'源组件包含链接，无法保证独立：{p}')
            total += sum((Path(base) / name).stat().st_size for name in files)
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < total + 1024**3:
        raise OSError('磁盘剩余空间不足，无法复制运行时')
    staging = target.with_name(target.name + '.staging-' + uuid.uuid4().hex[:8])
    staging.mkdir()
    hashes = {}
    try:
        for src, relative in entries:
            dst = staging / relative
            print(f'复制 {relative} ...', flush=True)
            if src.is_dir():
                shutil.copytree(src, dst)
                files = (p for p in src.rglob('*') if p.is_file())
                for original in files:
                    copied = dst / original.relative_to(src)
                    if copied.stat().st_size != original.stat().st_size:
                        raise IOError(f'复制大小不一致：{original}')
                    if original.stat().st_size >= 32 * 1024**2:
                        a, b = digest(original), digest(copied)
                        if a != b: raise IOError(f'校验失败：{original}')
                        hashes[str(copied.relative_to(staging)).replace('\\', '/')] = a
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                if digest(src) != digest(dst): raise IOError(f'校验失败：{src}')
                hashes[relative] = digest(dst)
        (staging / 'manifest.json').write_text(json.dumps({'format': 1, 'source_runtime_version': runtime_version,
            'source_svc_version': svc_version, 'large_file_sha256': hashes}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staging, target)
    except BaseException:
        print(f'导入未完成，临时文件保留在 {staging}', flush=True)
        raise
    print(f'独立运行时已建立：{target}', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SoulX Studio')
    parser.add_argument('--target', type=Path, default=ROOT / 'runtime/independent')
    args = parser.parse_args()
    import_runtime(args.source, args.target)
