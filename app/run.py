#!/usr/bin/env python3
"""기존 app/run.py 명령을 현재 SDK 기반 수신기로 연결한다."""
import os
from pathlib import Path
import sys

runner = Path(__file__).resolve().parent / 'dvs_streamer/build/GCC-arm64/Release/run.py'
if not runner.is_file():
    raise SystemExit('실행본이 없습니다. 저장소 루트에서 ./build.sh app을 실행하세요.')
os.execv(sys.executable, [sys.executable, str(runner), *sys.argv[1:]])
