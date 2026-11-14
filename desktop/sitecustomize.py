"""Private runtime bootstrap; installed with the application's Python only."""
import os
import sys
from pathlib import Path

packages = os.environ.get('AI_LYRICS_GPU_PACKAGES')
if packages and Path(packages).is_dir():
    sys.path.insert(0, packages)
ffmpeg = Path(sys.executable).parent / 'ffmpeg/bin'
if ffmpeg.is_dir():
    os.environ['PATH'] = str(ffmpeg) + os.pathsep + os.environ.get('PATH', '')
