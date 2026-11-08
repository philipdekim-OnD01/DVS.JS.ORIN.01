#!/usr/bin/env python3
"""One native decoder window, owned by the graphical-session systemd unit."""
import os
from pathlib import Path
import signal
import socket
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from native_decoder import NativeDecoder


def main():
    activate = True

    def request_activation(signum, frame):
        nonlocal activate
        activate = True

    # Install before READY so a launcher cannot send SIGUSR1 too early.
    signal.signal(signal.SIGUSR1, request_activation)
    root = tk.Tk(className='NrvDvsDecoder')
    NativeDecoder(root, Path('/dev/shm/nrv-dvs-live'))

    def present():
        nonlocal activate
        if activate:
            activate = False
            root.deiconify()
            root.lift()
            root.attributes('-topmost', True)
            root.after(200, lambda: root.attributes('-topmost', False))
            root.focus_force()
        root.after(100, present)

    root.after(0, present)
    root.update_idletasks()
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(b'READY=1')
    root.mainloop()


if __name__ == '__main__':
    main()
