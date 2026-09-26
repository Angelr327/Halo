"""Zero-hardware end-to-end test: no camera, no Arduino, no Gemini key, no monitor.
Runs the real main loop headless on a synthetic video with the web view enabled, reads the
page and a stream frame over HTTP, presses buttons, and checks that the HIGH alert fired.

YOLO is replaced by a tiny stand-in (it finds the white 'car' box in the synthetic clip), so
this runs anywhere. For real YOLO without hardware use:  python -m helmet.main --video clip.mp4
Run:  python -m tests.test_headless"""
import io
import sys
import threading
import time
import urllib.request
from contextlib import redirect_stdout

import cv2
import numpy as np

from helmet import config as cfg
from helmet import main as M
from helmet import perception

PORT = 8765
VIDEO = "/tmp/helmet_test_approach.mp4"


def make_video():
    w, h, fps = 640, 480, 12
    vw = cv2.VideoWriter(VIDEO, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    f = perception.focal_px(w)
    for i in range(int(3.7 * fps)):
        z = 40 - 10 * i / fps                       # closing from 40 m at 10 m/s, directly behind
        bw = f * 1.8 / z
        img = np.full((h, w, 3), 80, np.uint8)
        cv2.rectangle(img, (int(w / 2 - bw / 2), int(250 - bw * 0.4)), (int(w / 2 + bw / 2), int(250 + bw * 0.4)),
                      (255, 255, 255), -1)
        vw.write(img)
    vw.release()


class StandInDetector:
    model_name = "stand-in (no YOLO)"
    last_ms = 1.0

    def detect(self, frame):
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cs, _ = cv2.findContours((g > 200).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return [perception.Detection(x, y, x + w, y + h, 0.9, "car")
                for x, y, w, h in map(cv2.boundingRect, cs) if w > 3]


def get(path, timeout=3):
    return urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout)


def test_headless_zero_hardware():
    make_video()
    M.VehicleDetector = StandInDetector
    sys.argv = ["main", "--video", VIDEO, "--loop", "--headless", "--stream", str(PORT), "--no-gemini", "--port", "/dev/null-no-arduino"]
    cfg.TTS_ENABLED = False
    out = io.StringIO()
    def run():
        with redirect_stdout(out):
            M.main()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2.0)

    page = get("/").read().decode()
    assert "What's behind me?" in page and "/stream.mjpg" in page

    stream = get("/stream.mjpg")
    buf = b""
    while b"\xff\xd9" not in buf or b"\xff\xd8" not in buf:
        buf += stream.read(4096)
    jpg = buf[buf.index(b"\xff\xd8"):buf.index(b"\xff\xd9") + 2]
    frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    stream.close()
    assert frame is not None and frame.shape[1] > 640, "overlay frame not streamed"

    assert get("/key?c=t").status == 204          # haptic test button
    time.sleep(3.0)                                # let the looped clip reach the HIGH alert
    assert get("/key?c=q").status == 204          # quit button
    t.join(timeout=5)
    log = out.getvalue()
    print("\n".join("   " + line for line in log.splitlines() if not line.startswith("[status]"))[:1200])
    print(f"   streamed overlay frame: {frame.shape[1]}x{frame.shape[0]}")
    assert "Web view: http://" in log
    assert "tier=3" in log, "HIGH alert never fired"
    assert not t.is_alive(), "quit button did not stop the loop"


if __name__ == "__main__":
    test_headless_zero_hardware()
    print("ALL PASSED")
