#!/usr/bin/env python3
"""Native Tk DVS monitor. Local snapshot + bounded sensor reads; no HTTP client/server."""
import argparse
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import numpy as np
from PIL import Image, ImageTk
from sensor_settings import SensorSettings, parse_addresses

W, H = 960, 720
PRESETS = {'MIPI / RAW2': [0x3901, 0x3906, 0x3907, 0x3908, 0x3915],
           '감도': [0x0157, 0x0166, 0x0167, 0x0168], '이벤트': [0x392c, 0x3282],
           '센서 ROI': list(range(0x3300, 0x330d))}


def snapshot(directory):
    with (Path(directory) / 'snapshot.bin').open('rb') as f:
        before = os.fstat(f.fileno())
        data = f.read(4 + 65536 + W * H // 4 + 1)
        after = os.fstat(f.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('스냅샷 변경 중')
    if len(data) < 4:
        raise ValueError('잘린 헤더')
    n = struct.unpack('!I', data[:4])[0]
    if not 0 < n <= 65536 or len(data) != 4 + n + W * H // 4:
        raise ValueError('잘못된 프리뷰 길이')
    meta = json.loads(data[4:4+n])
    packed = np.frombuffer(data, np.uint8, offset=4+n)
    pixels = np.empty(W * H, np.uint8)
    for j in range(4):
        pixels[j::4] = (packed >> (2*j)) & 3
    return meta, pixels.reshape(H, W)


def fresh(meta, now=None):
    try:
        age = (time.monotonic() if now is None else now) - meta['monotonic']
        return (meta.get('state') == 'live' and meta.get('has_frame', False)
                and 0 <= age < 2 and 0 <= meta['last_good_age'] + age < 2)
    except (KeyError, TypeError):
        return False


def rates(a, b):
    if not a or a.get('session_pid') != b.get('session_pid'):
        return None
    dt = b['monotonic'] - a['monotonic']
    if dt <= 0:
        return None
    delta = {k: b['stream'][k] - a['stream'][k] for k in ('bytes', 'buffers')}
    if min(delta.values()) < 0 or b['frames'] < a['frames']:
        return None
    return dict(mib_s=delta['bytes']/dt/2**20, buffers_s=delta['buffers']/dt,
                decoded_fps=(b['frames']-a['frames'])/dt,
                preview_fps=(b['snapshot_id']-a['snapshot_id'])/dt)


def render(pixels, polarity='ON + OFF', flip_x=False, flip_y=False,
           noise=0, roi=(0, 959, 0, 719), colors=('#808080', '#ffffff', '#00bfff')):
    values = pixels.copy()
    values[values == 3] = 0
    if polarity == 'ON only': values[values == 2] = 0
    if polarity == 'OFF only': values[values == 1] = 0
    x0, x1, y0, y1 = roi
    if not (0 <= x0 <= x1 < W and 0 <= y0 <= y1 < H):
        raise ValueError('ROI 범위: x 0..959, y 0..719, 시작 ≤ 끝')
    values[:y0] = 0; values[y1+1:] = 0
    values[:, :x0] = 0; values[:, x1+1:] = 0
    if noise:
        padded = np.pad(values != 0, 1)
        count = np.zeros_like(values)
        for y in range(3):
            for x in range(3):
                if (y, x) != (1, 1): count += padded[y:y+H, x:x+W]
        values[count < noise] = 0
    palette = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in colors], np.uint8)
    image = palette[values]
    if flip_x: image = image[:, ::-1]
    if flip_y: image = image[::-1]
    return Image.fromarray(np.ascontiguousarray(image))


class NativeDecoder:
    def __init__(self, root, directory):
        self.root, self.directory = root, Path(directory)
        self.meta = self.previous = self.image = self.sensor = None
        self.sensor_pid = None
        self.results = queue.Queue()
        self.busy = False
        self.paused = tk.BooleanVar(value=False)
        self.flip_x, self.flip_y = tk.BooleanVar(), tk.BooleanVar()
        self.polarity, self.fps, self.noise = tk.StringVar(value='ON + OFF'), tk.StringVar(value='30'), tk.StringVar(value='0')
        self.colors = ['#808080', '#ffffff', '#00bfff']
        self.roi = (0, 959, 0, 719)
        self.last_metrics = self.draw_count = 0
        self.status = tk.StringVar(value='수신 확인 중')
        root.title('DVS Decoder · Orin · Native')
        root.geometry('1320x850'); root.minsize(980, 680)
        root.protocol('WM_DELETE_WINDOW', root.destroy)
        root.bind('<F11>', lambda _: root.attributes('-fullscreen', not root.attributes('-fullscreen')))
        root.bind('<Escape>', lambda _: root.attributes('-fullscreen', False))
        top = ttk.Frame(root, padding=10); top.pack(fill='x')
        ttk.Label(top, textvariable=self.status, font=('', 14, 'bold')).pack(side='left')
        ttk.Checkbutton(top, text='화면 일시정지', variable=self.paused).pack(side='right')
        ttk.Button(top, text='PNG 저장', command=self.save).pack(side='right', padx=8)
        body = ttk.Panedwindow(root, orient='horizontal'); body.pack(fill='both', expand=True)
        left = ttk.Frame(body); body.add(left, weight=3)
        self.canvas = tk.Canvas(left, bg='#181d25', highlightthickness=0); self.canvas.pack(fill='both', expand=True)
        self.item = self.canvas.create_image(0, 0, anchor='center')
        self.metrics = ttk.Label(left, padding=10, wraplength=800); self.metrics.pack(fill='x')
        ttk.Label(left, text='960 × 720 · 디코딩된 합성 프리뷰 · 창을 닫아도 수신/녹화는 유지됩니다.', padding=8).pack(fill='x')
        tabs = ttk.Notebook(body); body.add(tabs, weight=1)
        display, registers, health, config = [ttk.Frame(tabs, padding=10) for _ in range(4)]
        for panel, title in zip((display, registers, health, config), ('표시', '레지스터', '수신 상태', '설정 파일')): tabs.add(panel, text=title)
        def combo(label, var, values):
            ttk.Label(display, text=label).pack(anchor='w', pady=(8, 2))
            ttk.Combobox(display, textvariable=var, values=values, state='readonly').pack(fill='x')
        combo('앱 표시 목표 FPS', self.fps, ('15', '30', '60'))
        combo('극성', self.polarity, ('ON + OFF', 'ON only', 'OFF only'))
        combo('주변 이벤트 필터 k', self.noise, ('0', '1', '2'))
        ttk.Checkbutton(display, text='좌우 반전', variable=self.flip_x).pack(anchor='w', pady=6)
        ttk.Checkbutton(display, text='상하 반전', variable=self.flip_y).pack(anchor='w')
        for i, label in enumerate(('배경색', 'ON 색', 'OFF 색')):
            ttk.Button(display, text=label, command=lambda i=i: self.color(i)).pack(fill='x', pady=4)
        self.roi_vars = [tk.StringVar(value=str(x)) for x in self.roi]
        for label, var in zip(('X 시작', 'X 끝', 'Y 시작', 'Y 끝'), self.roi_vars):
            row = ttk.Frame(display); row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, width=10).pack(side='left'); ttk.Entry(row, textvariable=var, width=10).pack(side='left')
        ttk.Button(display, text='표시 ROI 적용', command=self.apply_roi).pack(fill='x', pady=8)
        ttk.Label(display, text='표시 옵션은 이 앱에만 적용됩니다.\n센서 설정·녹화 원본은 바뀌지 않습니다.', wraplength=300).pack(anchor='w')
        self.address = tk.StringVar(value='0x3901, 0x3907, 0x3908')
        for name, addresses in PRESETS.items():
            ttk.Button(registers, text=name, command=lambda a=addresses: self.address.set(', '.join(f'0x{x:04X}' for x in a))).pack(fill='x', pady=3)
        ttk.Entry(registers, textvariable=self.address).pack(fill='x', pady=8)
        self.read_button = ttk.Button(registers, text='센서 레지스터 읽기', command=self.read_registers); self.read_button.pack(fill='x')
        self.reg_message = ttk.Label(registers, text='정상 수신 중에만 조회 가능합니다.', wraplength=310); self.reg_message.pack(fill='x', pady=8)
        self.reg_table = ttk.Treeview(registers, columns=('address', 'value', 'expected'), show='headings', height=16)
        for col, label in zip(('address', 'value', 'expected'), ('주소', '센서 실제값', '파일 값')):
            self.reg_table.heading(col, text=label); self.reg_table.column(col, width=92, stretch=True)
        self.reg_table.pack(fill='both', expand=True)
        ttk.Label(registers, text='읽기 전용 · 최대 16개 · 1초 간격\n파일 값은 앱 시작 때 읽은 파일이며 센서 readback이 아닙니다.', wraplength=310).pack(fill='x', pady=8)
        self.health = tk.Text(health, width=42, wrap='word'); self.health.pack(fill='both', expand=True)
        self.config = tk.Text(config, width=42, wrap='none'); self.config.pack(fill='both', expand=True)
        self.tick()

    def color(self, index):
        color = colorchooser.askcolor(self.colors[index], parent=self.root)[1]
        if color: self.colors[index] = color

    def apply_roi(self):
        try:
            roi = tuple(int(v.get()) for v in self.roi_vars)
            render(np.zeros((H, W), np.uint8), roi=roi)
            self.roi = roi
        except ValueError as exc: messagebox.showerror('ROI', str(exc), parent=self.root)

    def save(self):
        if self.image is None: return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.png', filetypes=[('PNG', '*.png')])
        if path: self.image.save(path)

    @staticmethod
    def text(widget, value):
        widget.configure(state='normal'); widget.delete('1.0', 'end'); widget.insert('end', value); widget.configure(state='disabled')

    def discover_sensor(self, meta):
        pid = meta['session_pid']
        if pid == self.sensor_pid: return
        self.sensor = None; self.sensor_pid = pid
        try:
            args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
            if Path(args[0]).name != 'dvs_streamer': raise ValueError('수신 프로세스 불일치')
            self.sensor = SensorSettings(args[1])
            info = self.sensor.describe()
            lines = [str(self.sensor.path), 'SHA256: ' + self.sensor.digest, '앱이 읽은 파일 값 (센서 실제값 아님)', '']
            lines += [f"{e['line']:4} {e['address_hex']} = {e['value_hex']}  {e['comment']}" for e in info['entries']]
            self.text(self.config, '\n'.join(lines))
        except (OSError, ValueError, IndexError) as exc: self.text(self.config, str(exc))

    def read_registers(self):
        try:
            meta, _ = snapshot(self.directory)
            if not fresh(meta) or not self.sensor or meta['session_pid'] != self.sensor_pid:
                raise ValueError('수신 정지/세션 변경: 레지스터 조회를 실행하지 않습니다.')
            addresses = parse_addresses(self.address.get().replace(',', ' ').split())
            if self.busy: return
            self.busy = True; self.reg_message.configure(text='읽는 중…')
            def worker():
                try: self.results.put(self.sensor.read_registers(addresses))
                except Exception as exc: self.results.put(str(exc))
            threading.Thread(target=worker, daemon=True).start()
        except (OSError, ValueError) as exc: self.reg_message.configure(text=str(exc))

    def tick(self):
        try:
            while not self.results.empty():
                result = self.results.get_nowait(); self.busy = False
                self.reg_table.delete(*self.reg_table.get_children())
                if isinstance(result, str): self.reg_message.configure(text=result)
                else:
                    for r in result['registers']:
                        expected = '—' if r['setfile_value'] is None else f"0x{r['setfile_value']:02X}"
                        self.reg_table.insert('', 'end', values=(r['address_hex'], r['value_hex'], expected))
                    self.reg_message.configure(text='센서 실제 읽기 · ' + result['read_at'])
            meta, pixels = snapshot(self.directory); self.meta = meta
            live = fresh(meta)
            self.discover_sensor(meta)
            self.read_button.configure(state='normal' if live and self.sensor and not self.busy else 'disabled')
            self.status.set('● MIPI 수신 중' if live else '● 수신 정지 — 최신 정상 센서 프레임 없음')
            if time.monotonic() - self.last_metrics >= 1:
                speed = rates(self.previous, meta)
                self.metrics.configure(text=('측정 준비 중' if speed is None else
                    f"MIPI → VI {speed['mib_s']:.2f} MiB/s · {speed['buffers_s']:.1f} buffers/s\n"
                    f"디코딩 {speed['decoded_fps']:.1f} frames/s · 프리뷰 갱신 {speed['preview_fps']:.1f}/s") +
                    f"\n마지막 정상 프레임 {meta['last_good_age']:.1f}s 전 · 녹화 {meta.get('dump', {}).get('state', '—')}")
                self.text(self.health, json.dumps(meta, ensure_ascii=False, indent=2))
                self.previous, self.last_metrics = meta, time.monotonic()
            if not live or not self.paused.get():
                self.image = render(pixels if live else np.zeros((H, W), np.uint8), self.polarity.get(),
                                    self.flip_x.get(), self.flip_y.get(), int(self.noise.get()), self.roi, self.colors)
                cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
                scale = min(cw/W, ch/H)
                self.photo = ImageTk.PhotoImage(self.image.resize((max(1, int(W*scale)), max(1, int(H*scale))), Image.Resampling.NEAREST))
                self.canvas.itemconfigure(self.item, image=self.photo); self.canvas.coords(self.item, cw/2, ch/2)
        except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
            self.status.set('수신 데이터 없음 · ' + str(exc)); self.read_button.configure(state='disabled')
        self.root.after(max(1, round(1000/int(self.fps.get()))), self.tick)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('/dev/shm/nrv-dvs-live'))
    args = parser.parse_args()
    root = tk.Tk(); NativeDecoder(root, args.directory); root.mainloop()


if __name__ == '__main__': main()
