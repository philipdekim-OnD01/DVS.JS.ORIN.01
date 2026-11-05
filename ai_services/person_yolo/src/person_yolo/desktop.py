"""Native offline YOLO application. The worker never touches Tk widgets."""
from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from .config import Config
from .postprocess import process


class PostprocessApp:
    def __init__(self, root):
        self.root = root
        self.paths = []
        self.worker = None
        self.cancel_event = threading.Event()
        self.updates = queue.Queue(maxsize=2)
        self.finished = queue.Queue()
        self.output_path = None
        self.state = tk.StringVar(value='저장된 DVS 영상 또는 프리뷰를 선택하세요.')
        self.weights = tk.StringVar(value=os.environ.get('DVS_YOLO_WEIGHTS', ''))
        self.output = tk.StringVar(value=str(Path.home()/'DVS-YOLO-results'))
        self.confidence = tk.StringVar(value='0.25')
        self.snapshot_fps = tk.StringVar(value='4')
        self.dry_run = tk.BooleanVar(value=False)
        root.title('DVS YOLO · 영상 후처리')
        root.geometry('1120x820'); root.minsize(850, 650)
        root.protocol('WM_DELETE_WINDOW', self.close)
        header = ttk.Frame(root, padding=12); header.pack(fill='x')
        ttk.Label(header, text='DVS YOLO 후처리', font=('', 18, 'bold')).pack(anchor='w')
        ttk.Label(header, text='저장 영상 → YOLO11 nano 사람 검출 → 결과 MP4 · JSONL · 요약').pack(anchor='w', pady=4)
        self.form = ttk.LabelFrame(root, text='입력 및 모델', padding=10); self.form.pack(fill='x', padx=12)
        buttons = ttk.Frame(self.form); buttons.pack(fill='x')
        self.video_button = ttk.Button(buttons, text='영상 파일 선택', command=self.choose_video); self.video_button.pack(side='left')
        self.snapshot_button = ttk.Button(buttons, text='프리뷰 파일 선택', command=self.choose_snapshots); self.snapshot_button.pack(side='left', padx=8)
        self.input_label = ttk.Label(self.form, text='입력 없음', wraplength=1030); self.input_label.pack(fill='x', pady=8)
        for title, variable, command in [('YOLO 가중치 (.pt)', self.weights, self.choose_weights),
                                         ('결과 저장 폴더', self.output, self.choose_output)]:
            row = ttk.Frame(self.form); row.pack(fill='x', pady=3)
            ttk.Label(row, text=title, width=20).pack(side='left')
            ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text='찾기', command=command).pack(side='right', padx=4)
        opts = ttk.Frame(self.form); opts.pack(fill='x', pady=8)
        ttk.Label(opts, text='검출 임계값').pack(side='left')
        ttk.Entry(opts, textvariable=self.confidence, width=7).pack(side='left', padx=8)
        ttk.Label(opts, text='스냅샷 재생 FPS').pack(side='left')
        ttk.Entry(opts, textvariable=self.snapshot_fps, width=7).pack(side='left', padx=8)
        ttk.Checkbutton(opts, text='입력만 확인 (추론 없음)', variable=self.dry_run).pack(side='left')
        controls = ttk.Frame(root, padding=12); controls.pack(fill='x')
        self.start_button = ttk.Button(controls, text='후처리 시작', command=self.start); self.start_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='취소', command=self.cancel, state='disabled'); self.stop_button.pack(side='left', padx=8)
        self.open_button = ttk.Button(controls, text='결과 폴더 열기', command=self.open_output, state='disabled'); self.open_button.pack(side='right')
        footer = ttk.Frame(root); footer.pack(side='bottom', fill='x')
        self.progress = ttk.Progressbar(footer, mode='indeterminate'); self.progress.pack(fill='x', padx=12, pady=5)
        ttk.Label(footer, textvariable=self.state, wraplength=820, padding=12).pack(fill='x')
        ttk.Label(footer, text='CPU 실행 · 영상/프리뷰 입력 · RAW2 원본 미지원 · 0검출은 사람이 없다는 뜻이 아닙니다.', padding=8).pack(fill='x')
        self.preview_image = None
        self.preview = ttk.Label(root, anchor='center', text='처리 중인 영상이 여기에 표시됩니다.')
        self.preview.pack(fill='both', expand=True, padx=12)
        self.preview.bind('<Configure>', lambda _: self.draw_preview())
        self.timer = root.after(100, self.poll)

    def set_inputs(self, paths):
        self.paths = [Path(p) for p in paths]
        self.input_label.configure(text=f'{len(paths)}개 파일 · ' + ', '.join(p.name for p in self.paths))

    def choose_video(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('영상', '*.mp4 *.avi *.mov *.mkv *.webm')])
        if path: self.set_inputs([path])

    def choose_snapshots(self):
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=[('저장된 DVS 프리뷰', '*.bin')])
        if paths: self.set_inputs(sorted(paths))

    def choose_weights(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('YOLO11n weights', '*.pt')])
        if path: self.weights.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(parent=self.root)
        if path: self.output.set(path)

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self.busy(): return
        try:
            if not self.paths: raise ValueError('입력 파일을 선택하세요.')
            config = replace(Config(), confidence=float(self.confidence.get()))
            fps = float(self.snapshot_fps.get())
            if not 0 < fps <= 240: raise ValueError('재생 FPS는 0 초과 240 이하입니다.')
            dry = self.dry_run.get()
            weights = Path(self.weights.get()) if self.weights.get() else None
            if not dry and (weights is None or not weights.is_file()): raise ValueError('공식 yolo11n.pt 파일을 선택하세요.')
            if not self.output.get().strip(): raise ValueError('저장 폴더를 선택하세요.')
            self.output_path = Path(self.output.get())/datetime.now().strftime('job-%Y%m%d-%H%M%S-%f')
            paths = list(self.paths)
        except ValueError as exc:
            messagebox.showerror('입력 확인', str(exc), parent=self.root); return
        self.cancel_event.clear()
        self.start_button.configure(state='disabled'); self.stop_button.configure(state='normal')
        self.open_button.configure(state='disabled')
        self.state.set('입력 해시 확인 및 모델 준비 중…'); self.progress.start()
        def update(value):
            try: self.updates.put_nowait(value)
            except queue.Full:
                try: self.updates.get_nowait()
                except queue.Empty: pass
                try: self.updates.put_nowait(value)
                except queue.Full: pass
        def work():
            try:
                result = process(paths, self.output_path, weights, config, fps, dry, self.cancel_event, update)
                self.finished.put(('done', result))
            except Exception as exc: self.finished.put(('error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=False); self.worker.start()

    def cancel(self):
        self.cancel_event.set(); self.state.set('현재 처리 단계를 마친 뒤 취소합니다…')

    def poll(self):
        while not self.updates.empty():
            item = self.updates.get_nowait()
            self.preview_image = Image.fromarray(item['rgb'])
            self.draw_preview()
            self.state.set(f"{item['frames']} 프레임 처리 · 누적 사람 박스 {item['boxes']}개 (고유 인원수 아님)")
        while not self.finished.empty():
            kind, value = self.finished.get_nowait()
            self.progress.stop(); self.start_button.configure(state='normal'); self.stop_button.configure(state='disabled')
            self.open_button.configure(state='normal' if self.output_path and self.output_path.exists() else 'disabled')
            if kind == 'error': self.state.set('실패: ' + value)
            else:
                name = {'completed':'완료', 'cancelled':'취소됨'}.get(value['state'], value['state'])
                self.state.set(f"{name} · {value['processed_frames']}프레임 / 추론 {value['inferred_frames']} / 사람 박스 {value['detection_count']} · {self.output_path}")
        self.timer = self.root.after(100, self.poll)

    def draw_preview(self):
        if self.preview_image is None: return
        picture = self.preview_image.copy()
        picture.thumbnail((max(1, self.preview.winfo_width()), max(1, self.preview.winfo_height())))
        self.photo = ImageTk.PhotoImage(picture, master=self.root)
        self.preview.configure(image=self.photo, text='')

    def open_output(self):
        if self.output_path and self.output_path.is_dir():
            if sys.platform == 'win32': os.startfile(self.output_path)
            else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open', str(self.output_path)])

    def close(self):
        if self.busy():
            self.cancel(); self.root.after(200, self.close); return
        self.root.after_cancel(self.timer); self.root.destroy()


def main():
    # Isolate Ultralytics configuration from the receiver or another Python app.
    cache = Path.home()/'.cache/dvs-yolo-postprocess'
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(cache))
    root = tk.Tk(); PostprocessApp(root); root.mainloop()


if __name__ == '__main__': main()
