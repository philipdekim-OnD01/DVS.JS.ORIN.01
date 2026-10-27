#!/usr/bin/env python3
"""수신기(dvs_streamer)와 화면 서버(viewer.py)를 사용자 권한으로 실행한다.

이 스크립트와 같은 폴더(build/<preset>/<Config>/)의 dvs_streamer, viewer.py, settings/ 를 쓴다.
화면 서버는 이 기기(127.0.0.1)와 로컬망 주소에서만 접속된다.
"""
import argparse
import json
import os
import resource
from pathlib import Path
import signal
import subprocess
import time
import urllib.request
from viewer import lan_address, read_snapshot

BASE = Path(__file__).resolve().parent
STATE = Path('/dev/shm/nrv-dvs-live')
RT_PRIORITY = 10  # dvs_streamer SCHED_RR 우선순위 (nrv-dvs-streamer.service LimitRTPRIO 와 같게)

def process(name):
    try:
        pid = int((BASE/(name+'.pid')).read_text())
        cmd = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        required = str(BASE/('dvs_streamer' if name == 'capture' else 'viewer.py')).encode()
        return pid if required in cmd else None
    except (OSError, ValueError):
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['start', 'stop', 'status'])
    ap.add_argument('--no-web', action='store_true', help='웹 서버 없이 수신기만 시작')
    ap.add_argument('--settings', type=Path, default=BASE/'settings/Delta_10_2000FPS_RAW2.txt',
                    help='센서 setfile (dvs_streamer 가 레지스터 목록으로 넘김)')
    args = ap.parse_args()
    if args.action == 'start':
        if process('capture') or process('viewer'):
            raise SystemExit('기존 수신기 또는 뷰어가 있습니다. status로 확인하세요.')
        if subprocess.run(['fuser','/dev/video0'],capture_output=True).returncode == 0:
            raise SystemExit('/dev/video0를 사용 중인 프로그램이 있습니다.')
        STATE.mkdir(mode=0o755, exist_ok=True)
        # 녹화 시작/정지: 웹(viewer.py)이 이 FIFO 에 쓰고 dvs_streamer 가 읽음
        control = STATE/'control'
        control.unlink(missing_ok=True)
        os.mkfifo(control, 0o600)
        # dvs_streamer | viewer.py : viewer 가 화면을 받아 snapshot.bin 갱신 (+ 웹 서버)
        viewer = ['python3',str(BASE/'viewer.py'),'--directory',str(STATE),'--control',str(control)]
        viewer += ['--no-http'] if args.no_web else []
        children = {}
        try:
            with (BASE/'capture.log').open('ab', buffering=0) as log:
                capture = [str(BASE/'dvs_streamer'),str(args.settings.resolve()),'/dev/video0',str(control)]
                # 빌드·clangd 같은 다른 작업에 밀려 드라이버 버퍼 큐가 비지(starved) 않게 실시간(SCHED_RR)
                # 으로 실행. 스트림을 읽는 스레드(해석·압축·화면)가 모두 버퍼를 꺼낼 수 있어 프로세스 전체.
                if resource.getrlimit(resource.RLIMIT_RTPRIO)[0] >= RT_PRIORITY:
                    capture = ['chrt','-r',str(RT_PRIORITY)]+capture
                else:
                    print(f'경고: 실시간 우선순위 권한 없음 (RLIMIT_RTPRIO < {RT_PRIORITY}), 일반 우선순위로 실행'
                          ' - 다른 작업이 CPU 를 쓰면 starved 가 날 수 있음',flush=True)
                children['capture'] = subprocess.Popen(capture,stdin=subprocess.DEVNULL,
                                                       stdout=subprocess.PIPE,stderr=log,start_new_session=True)
            with (BASE/'viewer.log').open('ab', buffering=0) as log:
                children['viewer'] = subprocess.Popen(viewer,stdin=children['capture'].stdout,stdout=log,stderr=log,
                                                      start_new_session=True)
            children['capture'].stdout.close()  # 파이프 읽는 쪽은 viewer 만 가짐
            for name, p in children.items():
                (BASE/(name+'.pid')).write_text(str(p.pid)+'\n')
            print('수신기 준비 확인 중' if args.no_web else '수신기·화면 서버 준비 확인 중',flush=True)
            deadline = time.monotonic()+12
            while True:
                for name, child in children.items():
                    if child.poll() is not None:
                        raise RuntimeError(f'{name} 조기 종료: {child.returncode}, {name}.log 확인')
                try:
                    if args.no_web:
                        state, _ = read_snapshot(STATE)
                    else:
                        with urllib.request.urlopen('http://127.0.0.1:8080/stats.json',timeout=1) as response:
                            state = json.load(response)
                    if state.get('session_pid') == children['capture'].pid and state['state']=='live' and state['buffers']>0:
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic()>deadline:
                    raise RuntimeError('정상 버퍼 또는 화면 서버 준비를 12초 안에 확인하지 못했습니다.')
                time.sleep(0.1)
        except BaseException:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
            for child in children.values():
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print(f'종료 지연 PID {child.pid}: 강제 종료하지 않았습니다.')
            raise
        print('정상 수신 시작 완료 (웹 서버 없음)' if args.no_web else '정상 수신·화면 서버 준비 완료: '+' '.join(f'http://{h}:8080' for h in ['127.0.0.1']+lan_address()))
    elif args.action == 'stop':
        for name in ('capture','viewer'):
            pid = process(name)
            if pid:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic()+15
                while process(name) and time.monotonic()<deadline:
                    time.sleep(0.1)
                if process(name):
                    raise SystemExit(f'{name} 종료 지연: PID {pid}. 강제 종료하지 않았습니다.')
        print('수신 및 화면 종료 완료')
    print(json.dumps({name:process(name) for name in ('capture','viewer')},ensure_ascii=False))
    if (STATE/'snapshot.bin').exists():
        print(json.dumps(read_snapshot(STATE)[0]))

if __name__ == '__main__':
    main()
