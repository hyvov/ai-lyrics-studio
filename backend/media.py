"""Known application test media: identify by bytes, never by user file names."""
from functools import lru_cache
import hashlib
import array
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

KNOWN_TEST_AUDIO = {
    '8330b46e8444b34acfce94ba08a04b91bdbbcceae44dc40a6599a1b317745604':
        '当前音频是功能测试用的合成测试音，不包含实际演唱，无法对齐歌词。请导入真实原唱干声，或切换到可生成的演唱示例。',
}

@lru_cache(maxsize=128)
def _digest(path: str, size: int, modified_ns: int):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def source_issue(project, assets):
    asset = project.get('assets', {}).get('source')
    if not asset:
        return None
    path = assets / asset['id']
    if not path.is_file():
        return None
    stat = path.stat()
    digest = _digest(str(path), stat.st_size, stat.st_mtime_ns)
    return KNOWN_TEST_AUDIO.get(digest)


@lru_cache(maxsize=4)
def _waveform(path: str, size: int, modified_ns: int):
    """Bounded-memory 5 ms peaks from FFmpeg's 8 kHz stereo PCM stream."""
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise ValueError('无法解析波形：请安装 FFmpeg 并加入系统 PATH。')
    command=[ffmpeg,'-nostdin','-hide_banner','-v','error','-i',path,'-map','0:a:0','-t','1800',
             '-f','s16le','-acodec','pcm_s16le','-ac','2','-ar','8000','pipe:1']
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    peaks=[];pending=b''
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,creationflags=flags)
        def stop_if_running():
            try:
                if process.poll() is None:process.kill()
            except OSError:pass
        timer=threading.Timer(120,stop_if_running);timer.daemon=True;timer.start()
        try:
            while True:
                chunk=process.stdout.read(128000)
                if not chunk:break
                data=pending+chunk;usable=len(data)//160*160;pending=data[usable:]
                samples=array.array('h');samples.frombytes(data[:usable])
                if sys.byteorder!='little':samples.byteswap()
                for i in range(0,len(samples),80):
                    peaks.append(round(max(abs(x) for x in samples[i:i+80])/32768,5))
            if pending:
                data=pending[:len(pending)//2*2];samples=array.array('h');samples.frombytes(data)
                if sys.byteorder!='little':samples.byteswap()
                if samples:peaks.append(round(max(abs(x) for x in samples)/32768,5))
            code=process.wait()
        finally:
            timer.cancel();process.stdout.close()
            if process.poll() is None:process.kill();process.wait()
        if code or not peaks:
            errors.seek(0);detail=errors.read(500).decode('utf-8','replace')
            raise ValueError('音频波形解析失败：'+(detail.strip() or '音频没有有效采样。'))
    return peaks


def waveform(path: Path):
    stat=path.stat()
    return _waveform(str(path),stat.st_size,stat.st_mtime_ns)
