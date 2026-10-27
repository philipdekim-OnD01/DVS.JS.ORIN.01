#!/usr/bin/env python3
"""nrv-dvs-kiosk.py - full-screen window that shows the DVS web page on the bedside touchscreen.

Uses WebKitGTK (Debian packages python3-gi + gir1.2-webkit2-4.1), so it needs no snap.
Retries every 3 s until the page answers (the acquisition service may start later than the
desktop), and reloads if the page stops answering.

  nrv-dvs-kiosk.py [URL] [--plain]  default http://127.0.0.1:8080; --plain = no touchscreen layout
  close from SSH:  pkill -f nrv-dvs-kiosk

Author: Karim <karim@nrv.kr>, NRV, 2026-10-15
"""
import sys
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
PLAIN = "--plain" in sys.argv[1:]            # --plain: show the page as in an office browser
URL = args[0] if args else "http://127.0.0.1:8080"

# Touchscreen layout for the 2560x720 bedside screen (added 2026-10-15, Karim <karim@nrv.kr>).
# Only the look changes; viewer.py and its buttons stay as they are. Left: live image 960x720.
# Right: recording state and two large buttons. Hidden: title, label field, view options, numbers.
KIOSK_CSS = """
html,body{height:720px!important;overflow:hidden!important;-webkit-user-select:none;user-select:none}
main{max-width:none!important;margin:0!important;padding:0!important}
header,.foot,#detail,.rec input,.metric,aside>div:last-child{display:none!important}
.layout{grid-template-columns:960px 1fr!important;gap:0!important;margin:0!important;height:720px}
canvas{width:960px!important;height:720px!important;border:0!important}
aside{display:flex!important;flex-direction:column;justify-content:center;padding:30px 70px!important;line-height:1.25!important}
.rec{display:grid!important;grid-template-columns:1fr 1fr;gap:28px 50px;padding:36px 44px!important;border-radius:28px!important;margin:0!important}
.rec>div:first-child{grid-column:1/3;font-size:30px;color:#a5b5ca}
.recstate{grid-column:1/3;font-size:64px!important}
.rec .start,.rec .stop{width:100%!important;height:250px;font-size:60px;font-weight:700;border-radius:26px!important;border-width:4px!important}
#recinfo{grid-column:1/3;font-size:20px;margin-top:0!important}
"""
WAIT_HTML = """<html><body style="background:#000;color:#aaa;font:40px sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
Starting camera&hellip;</body></html>"""


def page_answers():
    try:
        with urllib.request.urlopen(URL, timeout=2):
            return True
    except Exception:
        return False


class Kiosk:
    def __init__(self):
        self.win = Gtk.Window(title="NRV DVS")
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        if not PLAIN:
            ucm.add_style_sheet(WebKit2.UserStyleSheet(
                KIOSK_CSS, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserStyleLevel.AUTHOR, None, None))
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        s = self.view.get_settings()
        s.set_enable_developer_extras(False)
        s.set_enable_write_console_messages_to_stdout(True)
        self.view.connect("context-menu", lambda *a: True)          # no right-click / long-press menu
        self.view.connect("load-failed", self.on_failed)
        self.win.add(self.view)
        self.win.fullscreen()
        self.win.show_all()
        self.showing = False
        self.view.load_html(WAIT_HTML, None)
        GLib.timeout_add_seconds(3, self.check)

    def on_failed(self, *a):
        self.showing = False
        self.view.load_html(WAIT_HTML, None)
        return True

    def check(self):
        ok = page_answers()
        if ok and not self.showing:
            self.showing = True
            self.view.load_uri(URL)
        elif not ok and self.showing:
            self.on_failed()
        return True                                                   # keep checking


if __name__ == "__main__":
    Kiosk()
    Gtk.main()
