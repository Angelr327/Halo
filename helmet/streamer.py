"""Headless web view for the Pi: open http://<pi-ip>:8080 on a phone or laptop.

Shows the debug overlay as an MJPEG stream plus buttons that send the same key
commands as the desktop window. Encoding runs on its own thread, at STREAM_FPS,
and only while someone is watching, so it barely touches the detection loop.
"""
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

from . import config as cfg

BUTTONS = [("g", "What's behind me?"), ("v", "Voice question"), ("t", "Test motors"),
           ("l", "Light override"), ("p", "Profile"), ("f", "Simulate camera fault"),
           ("k", "Kill heartbeat"), ("x", "Gemini on/off"), ("a", "Describe / agent"),
           ("m", "Mirror"), ("c", "Reload config"), ("d", "Metrics panel"), (" ", "Pause video"),
           ("r", "Record"), ("q", "Quit")]

PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind-spot helmet</title><style>
body{{margin:0;background:#111;color:#ddd;font-family:system-ui,sans-serif}}
img{{width:100%;max-width:1100px;display:block;margin:auto}}
.b{{display:flex;flex-wrap:wrap;gap:6px;padding:8px;justify-content:center}}
button{{background:#333;color:#eee;border:1px solid #555;border-radius:8px;padding:10px 12px;font-size:15px}}
button:active{{background:#0a6}}#s{{text-align:center;font-size:13px;color:#8c8}}
</style></head><body><img src="/stream.mjpg{tok}"><div id="s"></div><div class="b">{buttons}</div>
<script>function k(c){{fetch('/key?c='+encodeURIComponent(c)+'{amp}').then(r=>{{
document.getElementById('s').textContent=r.ok?'sent '+(c==' '?'space':c):'rejected';}});}}</script>
</body></html>"""


class Streamer:
    def __init__(self, port):
        self.keys = queue.Queue()
        self._latest = None
        self._jpeg = None
        self._jpeg_seq = 0
        self._cond = threading.Condition()
        self.clients = 0
        self.port = port
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _authorized(self, q):
                return not cfg.STREAM_TOKEN or q.get("t", [""])[0] == cfg.STREAM_TOKEN

            def do_GET(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == "/":
                    tok = f"?t={cfg.STREAM_TOKEN}" if cfg.STREAM_TOKEN else ""
                    amp = f"&t={cfg.STREAM_TOKEN}" if cfg.STREAM_TOKEN else ""
                    buttons = "".join(f'<button onclick="k(\'{c}\')">{label}</button>' for c, label in BUTTONS)
                    body = PAGE.format(tok=tok, amp=amp, buttons=buttons).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path == "/key":
                    c = q.get("c", [""])[0][:1]
                    if not self._authorized(q) or not c:
                        self.send_response(403)
                        self.end_headers()
                        return
                    streamer.keys.put(ord(c))
                    self.send_response(204)
                    self.end_headers()
                elif url.path == "/stream.mjpg":
                    if not self._authorized(q):
                        self.send_response(403)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    streamer._serve_stream(self.wfile)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._encoder, daemon=True).start()

    def publish(self, canvas):
        """Called by the main loop with each rendered frame (no copy, no encoding here)."""
        self._latest = canvas

    def wants_frames(self):
        return self.clients > 0

    def get_key(self):
        try:
            return self.keys.get_nowait()
        except queue.Empty:
            return 255

    def _encoder(self):
        period = 1.0 / max(1, cfg.STREAM_FPS)
        while True:
            time.sleep(period)
            img = self._latest
            if img is None or self.clients == 0:
                continue
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, cfg.STREAM_JPEG_QUALITY])
            if ok:
                with self._cond:
                    self._jpeg, self._jpeg_seq = buf.tobytes(), self._jpeg_seq + 1
                    self._cond.notify_all()

    def _serve_stream(self, out):
        self.clients += 1
        seen = -1
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self._jpeg_seq != seen, timeout=2.0)
                    jpeg, seen = self._jpeg, self._jpeg_seq
                if jpeg is None:
                    continue
                out.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                          + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.clients -= 1

    def close(self):
        self.server.shutdown()
