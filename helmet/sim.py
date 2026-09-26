"""Traffic simulator: scripted cars through the REAL pipeline, no camera or YOLO needed.

    python -m helmet.main --sim            # then open http://<ip>:8080/view

Each scene moves cars around the rider in metres. They are projected to boxes with the same
pinhole model the perception code inverts, jittered like YOLO boxes, and handed to the real
tracker, TTC, alert policy, OLED HUD and Arduino. So alerts, buzzes and the web view behave
exactly as they would with a camera. Uses: building the web view without hardware, and a
guaranteed demo backup if the room's lighting defeats the camera.
"""
import math
import random
import time

import cv2
import numpy as np

from . import config as cfg
from .perception import Detection, focal_px

W, H = cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT
FPS = 15.0
CAM_HEIGHT_M = 1.5            # helmet camera height above the road
CAR_W, CAR_H = 1.8, 1.45      # rear view of a car, metres


def _approach(x0, x1, z0, speed, until=0.6):
    """Car closing at `speed` m/s from z0, drifting laterally x0 -> x1 by the time it arrives."""
    def pos(t):
        z = z0 - speed * t
        if z < until:
            return None
        return x0 + (x1 - x0) * (1 - z / z0), z
    return pos, (z0 - until) / speed


def _hold(x, z):
    return (lambda t: (x, z)), None


# (title, [car position functions], duration s)
def _scenes():
    s = []
    p, d = _approach(0.0, 0.0, 34.0, 9.0)
    s.append(("Car closing fast from directly behind", [p], d + 1.5))
    p, d = _approach(-2.6, -2.6, 32.0, 8.0)
    s.append(("Car passing on the left, normal gap", [p], d + 1.5))
    p, d = _approach(-3.0, 0.0, 32.0, 9.0)
    s.append(("Car cutting into your lane from the left", [p], d + 1.5))
    p, d = _approach(1.6, 1.6, 32.0, 10.0)
    s.append(("Close pass on the right", [p], d + 1.5))
    far, _ = _approach(0.4, 0.4, 26.0, 1.5, until=14.0)
    near, d = _approach(-2.8, -2.8, 30.0, 8.0)
    s.append(("Two cars: one slow behind, one passing left", [far, near], d + 1.5))
    p, _ = _hold(0.2, 9.0)
    s.append(("Car following at a steady 9 m: no alert", [p], 6.0))
    return s


class Scenario:
    def __init__(self, seed=7):
        self.scenes = _scenes()
        self.rng = random.Random(seed)
        self.idx, self.t_scene = 0, 0.0

    def title(self):
        return self.scenes[self.idx][0]

    def step(self, dt):
        """Advance time; returns [(x, z), ...] for cars currently in the scene."""
        self.t_scene += dt
        title, cars, dur = self.scenes[self.idx]
        if self.t_scene > dur:
            self.idx = (self.idx + 1) % len(self.scenes)
            self.t_scene = 0.0
            title, cars, dur = self.scenes[self.idx]
        return [p for p in (f(self.t_scene) for f in cars) if p is not None]

    def boxes(self, cars):
        """Rider-view boxes (x right = rider's right), far cars first, YOLO-like jitter."""
        f = focal_px(W)
        out = []
        for x, z in sorted(cars, key=lambda c: -c[1]):
            # YOLO box jitter scales with box size: ~2% of width, ~1.5% of height per edge
            w = f * CAR_W / z * (1 + self.rng.uniform(-0.02, 0.02))
            h = f * CAR_H / z
            cx = W / 2 + f * x / z + self.rng.uniform(-0.02, 0.02) * w
            y2 = H / 2 + f * CAM_HEIGHT_M / z + self.rng.uniform(-0.015, 0.015) * h
            y1 = H / 2 + f * (CAM_HEIGHT_M - CAR_H) / z + self.rng.uniform(-0.015, 0.015) * h
            x1, x2 = max(0.0, cx - w / 2), min(W - 1.0, cx + w / 2)
            y1, y2 = max(0.0, y1), min(H - 1.0, y2)
            if x2 - x1 >= 4 and y2 > y1:
                out.append(Detection(x1, y1, x2, y2, 0.9, "car"))
        return out


def render(dets, title):
    """A simple rear-camera-like picture so the camera inset shows the same scene."""
    img = np.zeros((H, W, 3), np.uint8)
    img[: H // 2] = (70, 58, 48)                               # sky
    img[H // 2:] = (62, 62, 62)                                # road
    vp = (W // 2, H // 2)
    for x_edge in (-6.0, 6.0):
        cv2.line(img, vp, (int(W / 2 + focal_px(W) * x_edge / 2.0), H), (150, 150, 150), 2)
    for d in dets:                                             # far first, so near cars cover them
        x1, y1, x2, y2 = int(d.x1), int(d.y1), int(d.x2), int(d.y2)
        cv2.rectangle(img, (x1, y1), (x2, y2), (205, 205, 210), -1)
        wh = max(1, (y2 - y1) // 3)
        cv2.rectangle(img, (x1 + (x2 - x1) // 6, y1 + wh // 4), (x2 - (x2 - x1) // 6, y1 + wh), (60, 50, 45), -1)
        lh = max(1, (y2 - y1) // 8)
        for lx in (x1 + 2, x2 - max(3, (x2 - x1) // 6)):
            cv2.rectangle(img, (lx, y1 + wh + lh), (lx + max(2, (x2 - x1) // 7), y1 + wh + 2 * lh), (40, 40, 230), -1)
    cv2.putText(img, f"SIMULATOR: {title}", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return img


class SimSource:
    """Stands in for the camera. Frames come out as the camera would give them (flipped if
    MIRROR_VIEW), so main.py's mirror step turns them back into the rider's view."""
    is_live = False

    def __init__(self, seed=7):
        self.scenario = Scenario(seed)
        self.current = []                  # rider-view detections for the latest frame
        self.paused, self.ended = False, False
        self.t, self._last = 0.0, None

    @property
    def title(self):
        return self.scenario.title()

    def read(self, timeout=0.1):
        now = time.monotonic()
        if self._last is None:
            self._last = now - 1 / FPS
        wait = self._last + 1 / FPS - now
        if wait > 0:
            time.sleep(wait)
        now = time.monotonic()
        dt, self._last = now - self._last, now
        if self.paused:
            return None, None
        self.t += dt
        self.current = self.scenario.boxes(self.scenario.step(dt))
        frame = render(self.current, self.title)
        if cfg.MIRROR_VIEW:
            frame = cv2.flip(frame, 1)
        return frame, self.t

    def toggle_pause(self):
        self.paused = not self.paused

    def stale_for(self):
        return 0.0

    def release(self):
        pass


class SimDetector:
    """Returns the scripted boxes for the frame SimSource just produced (no YOLO)."""
    model_name = "simulator (no camera, no YOLO)"
    last_ms = 0.0

    def __init__(self, source):
        self.source = source

    def detect(self, frame):
        return list(self.source.current)
