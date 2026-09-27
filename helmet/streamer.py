"""Headless web view for the Pi: open http://<pi-ip>:8080 on a phone or laptop.

Shows the debug overlay as an MJPEG stream plus buttons that send the same key
commands as the desktop window. Encoding runs on its own thread, at STREAM_FPS,
and only while someone is watching, so it barely touches the detection loop.

  /            debug overlay + buttons
  /view        2.5D bird's-eye view (three.js, drawn on the phone from /state)
  /state       snapshot feed: server-sent events, one small JSON scene per frame (<= ~15/s)
  /api/v1/state  the latest snapshot as one plain JSON response (the iOS app polls this)
  /api/v1/incidents                    GET list (newest first) / POST = rider marks an incident
  /api/v1/incidents/<id>               GET one report (meta.json)
  /api/v1/incidents/<id>/clip.mp4      GET the clip (supports Range, for iOS AVPlayer)
  /api/v1/incidents/<id>/thumb.jpg     GET the frame the alert fired on
  /api/v1/incidents/<id>/analyze       POST = (re)run the Gemini report
  /static/...  files for /view (three.js is bundled: works on a hotspot with no internet)
"""
import json
import os
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
           ("i", "Mark incident"), ("r", "Record"), ("q", "Quit")]

PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind-spot helmet</title><style>
body{{margin:0;background:#111;color:#ddd;font-family:system-ui,sans-serif}}
img{{width:100%;max-width:1100px;display:block;margin:auto}}
.b{{display:flex;flex-wrap:wrap;gap:6px;padding:8px;justify-content:center}}
button{{background:#333;color:#eee;border:1px solid #555;border-radius:8px;padding:10px 12px;font-size:15px}}
button:active{{background:#0a6}}#s{{text-align:center;font-size:13px;color:#8c8}}
a.v{{display:block;text-align:center;color:#6cf;padding:8px;font-size:16px}}
</style></head><body><a class="v" href="/view{tok}">Open the 2.5D view &rarr;</a><img src="/stream.mjpg{tok}"><div id="s"></div><div class="b">{buttons}</div>
<script>function k(c){{fetch('/key?c='+encodeURIComponent(c)+'{amp}').then(r=>{{
document.getElementById('s').textContent=r.ok?'sent '+(c==' '?'space':c):'rejected';}});}}</script>
</body></html>"""


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".txt": "text/plain",
                 ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}


def _with_urls(meta):
    """Add the URLs the phone app needs (relative to the Pi's address)."""
    base = f"/api/v1/incidents/{meta['id']}"
    return dict(meta, url=base, clip_url=f"{base}/clip.mp4" if meta.get("clip") else None,
                thumb_url=f"{base}/thumb.jpg" if meta.get("thumb") else None)


class Streamer:
    def __init__(self, port):
        self.keys = queue.Queue()
        self._state, self._state_seq = None, 0
        self.incidents = None                    # IncidentRecorder, set by main.py
        self._state_cond = threading.Condition()
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
                elif url.path == "/view":
                    self._file(os.path.join(WEB_DIR, "view.html"), cache=False)
                elif url.path.startswith("/static/"):
                    name = os.path.basename(url.path)            # no ../ tricks: flat folder only
                    self._file(os.path.join(WEB_DIR, "static", name), cache=True)
                elif url.path.startswith("/api/v1/incidents"):
                    if not self._authorized(q):
                        self.send_response(403)
                        self.end_headers()
                        return
                    self._incidents_get(url.path)
                elif url.path == "/api/v1/state":
                    data = streamer._state
                    if data is None:                     # app running but no frame processed yet
                        self.send_response(503)
                        self.end_headers()
                        return
                    body = data.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path == "/state":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    streamer._serve_state(self.wfile)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                parts = url.path.strip("/").split("/")
                rec = streamer.incidents
                if not url.path.startswith("/api/v1/incidents") or rec is None or not self._authorized(q):
                    self.send_response(404 if rec is None or not url.path.startswith("/api/v1/incidents") else 403)
                    self.end_headers()
                    return
                if parts == ["api", "v1", "incidents"]:
                    streamer.keys.put(ord("i"))              # handled on the main loop, like the web button
                    self._json({"status": "marking"}, 202)
                elif len(parts) == 5 and parts[4] == "analyze":
                    ok = rec.request_analysis(parts[3])
                    self._json({"status": "queued" if ok else "unavailable"}, 202 if ok else 409)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _incidents_get(self, path):
                rec = streamer.incidents
                parts = path.strip("/").split("/")          # api v1 incidents [id [file]]
                if rec is None:
                    self._json({"incidents": [], "enabled": False})
                elif len(parts) == 3:
                    self._json({"incidents": [_with_urls(m) for m in rec.list()], "enabled": True,
                                "recording": rec.recording})
                elif len(parts) == 4:
                    meta = rec.get(parts[3])
                    if meta is None:
                        self.send_response(404)
                        self.end_headers()
                    else:
                        self._json(_with_urls(meta))
                elif len(parts) == 5 and rec.file_path(parts[3], parts[4]):
                    ctype = "video/mp4" if parts[4].endswith(".mp4") else "image/jpeg"
                    self._file_range(rec.file_path(parts[3], parts[4]), ctype)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _file_range(self, path, ctype):
                """Byte-range file responses: iOS AVPlayer won't play an mp4 without them."""
                size = os.path.getsize(path)
                start, end, code = 0, size - 1, 200
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    a, _, b = rng[6:].split(",")[0].strip().partition("-")
                    try:
                        if a == "":
                            start = max(0, size - int(b))
                        else:
                            start, end = int(a), (int(b) if b else size - 1)
                    except ValueError:
                        start, end = 0, size - 1
                    end = min(end, size - 1)
                    if start >= size or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    code = 206
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(path, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def _file(self, path, cache):
                try:
                    with open(path, "rb") as f:
                        body = f.read()
                except OSError:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400" if cache else "no-cache")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._encoder, daemon=True).start()

    def publish(self, canvas):
        """Called by the main loop with each rendered frame (no copy, no encoding here)."""
        self._latest = canvas

    def publish_state(self, snapshot):
        """Called by the main loop ~10 times a second with a snapshot dict (see snapshot.py)."""
        data = json.dumps(snapshot, separators=(",", ":"))
        with self._state_cond:
            self._state, self._state_seq = data, self._state_seq + 1
            self._state_cond.notify_all()

    def _serve_state(self, out):
        seen = -1
        try:
            out.write(b"retry: 1000\n\n")                 # browser reconnects after 1 s if the Pi restarts
            out.flush()
            while True:
                with self._state_cond:
                    self._state_cond.wait_for(lambda: self._state_seq != seen, timeout=2.0)
                    data, fresh = self._state, self._state_seq != seen
                    seen = self._state_seq
                out.write(b"data: " + data.encode() + b"\n\n" if fresh and data else b": ping\n\n")
                out.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

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
