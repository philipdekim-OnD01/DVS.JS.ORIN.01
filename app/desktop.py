#!/usr/bin/env python3
"""메모리의 최신 이벤트 영상을 Jetson 데스크톱 창에 표시한다. 센서 제어 없음."""
import argparse
from pathlib import Path
import struct
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk
from viewer import HEIGHT, WIDTH, grayscale_image, read_snapshot


class DesktopViewer:
    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.stamp = None
        self.snapshot = None
        self.pixels = bytes(WIDTH * HEIGHT)
        self.previous = None
        self.rate = 0.0
        self.paused = tk.BooleanVar(value=False)
        self.flip = tk.BooleanVar(value=False)
        self.drawn = None
        self.photo = None
        self.timer = None
        root.title('NRV DVS · Jetson')
        root.geometry('1000x850')
        root.minsize(540, 460)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<F11>', lambda _: self.fullscreen())
        root.bind('<Escape>', lambda _: root.attributes('-fullscreen', False))
        root.bind('q', lambda _: self.close())

        toolbar = ttk.Frame(root, padding=10)
        toolbar.pack(fill='x')
        self.status = ttk.Label(toolbar, text='수신 대기')
        self.status.pack(side='left')
        ttk.Button(toolbar, text='창 닫기', command=self.close).pack(side='right', padx=4)
        ttk.Button(toolbar, text='전체 화면 · F11', command=self.fullscreen).pack(side='right', padx=4)
        ttk.Checkbutton(toolbar, text='상하 반전', variable=self.flip).pack(side='right', padx=4)
        ttk.Checkbutton(toolbar, text='화면 일시정지', variable=self.paused).pack(side='right', padx=4)
        self.canvas = tk.Canvas(root, background='#808080', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor='center')
        self.metrics = ttk.Label(root, padding=8, text='')
        self.metrics.pack(fill='x')
        ttk.Label(root, padding=8,
                  text='960 × 720 · CID 0 · 흰색 ON / 검정 OFF / 회색 배경\n창을 닫거나 화면을 멈춰도 센서 수신은 계속됩니다.').pack(fill='x')
        self.tick()

    def fullscreen(self):
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))

    def close(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.root.destroy()

    def tick(self):
        try:
            stat = (self.directory / 'snapshot.bin').stat()
            stamp = (stat.st_ino, stat.st_mtime_ns)
            if stamp != self.stamp:
                snapshot, pixels = read_snapshot(self.directory)
                if len(pixels) != WIDTH * HEIGHT:
                    raise ValueError('영상 크기 불일치')
                # 같은 실행 내 새 snapshot 간 차이로 처리량을 계산한다.
                if self.previous and self.previous['session_pid'] == snapshot['session_pid']:
                    elapsed = snapshot['seconds'] - self.previous['seconds']
                    if elapsed > 0:
                        self.rate = max(0, snapshot['events'] - self.previous['events']) / elapsed
                else:
                    self.rate = 0.0
                self.previous = snapshot
                self.snapshot, self.pixels, self.stamp = snapshot, pixels, stamp
            state = self.snapshot
            age = time.monotonic() - state['monotonic']
            live = state['state'] == 'live' and 0 <= age < 1 and state['last_good_age'] + age < 1
            paused = self.paused.get()
            text = '수신 중 · 화면 일시정지' if live and paused else '실시간 수신 중' if live else '수신 대기 / 정지'
            self.status.configure(text=text)
            self.metrics.configure(text=(
                f"해석한 이벤트 {self.rate / 1e6:.2f} M/s · 해석한 버퍼 {state['buffers']:,} · "
                f"건너뛴 버퍼 {state['skipped']:,} · "
                f"형식 오류 {state['unknown'] + state['invalid']:,}"))
            if not live:
                self.rate = 0.0
                self.draw(None)
            elif not paused:
                self.draw((state['session_pid'], state['snapshot_id']))
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            self.status.configure(text='수신 대기 · python3 app/run.py start --no-web으로 수신을 시작하세요')
            self.metrics.configure(text='새 수신 화면을 기다리고 있습니다.')
            self.draw(None)
        self.timer = self.root.after(25, self.tick)

    def draw(self, frame):
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = min(width / WIDTH, height / HEIGHT)
        size = (max(1, round(WIDTH * scale)), max(1, round(HEIGHT * scale)))
        key = (frame, size, self.flip.get())
        if key == self.drawn:
            return
        image = grayscale_image(self.pixels if frame else bytes(WIDTH * HEIGHT))
        if self.flip.get():
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image = image.resize(size, Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(image, master=self.root)
        self.canvas.itemconfigure(self.image_item, image=self.photo)
        self.canvas.coords(self.image_item, width / 2, height / 2)
        self.drawn = key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('/dev/shm/nrv-dvs-live'))
    parser.add_argument('--fullscreen', action='store_true')
    args = parser.parse_args()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise SystemExit('데스크톱에 연결할 수 없습니다. Jetson 모니터 또는 VNC 데스크톱의 터미널에서 실행하세요.\n' + str(error)) from None
    DesktopViewer(root, args.directory)
    if args.fullscreen:
        root.attributes('-fullscreen', True)
    root.mainloop()


if __name__ == '__main__':
    main()
