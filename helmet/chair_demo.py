"""Marker-free front chair visualization. Placement is illustrative, not measured range.

Uses the shared dual-camera inference worker; never writes collision/HUD state.
"""
import threading
import time

import cv2

from . import config as cfg
from .perception import Tracker


class ChairDemo:
    def __init__(self):
        self.lock = threading.RLock()
        self.tracker = Tracker()
        self.frame = None
        self.frame_received_at = 0.0
        self.detected_at = 0.0
        self.last_t = -1.0
        self.obstacles = []
        self.boxes = []
        self.reason = "Waiting for front camera"

    @property
    def stale_s(self):
        return cfg.CAMERA_TIMEOUT_S

    def capture(self, frame, t, *, age_s=0.0):
        with self.lock:
            self.frame, self.frame_received_at = frame, time.monotonic() - age_s
        return frame.shape[1], frame.shape[0]

    def associate(self, detections, t, frame_size):
        # The inference result must still describe the live scene when it arrives.
        if time.monotonic() - t > self.stale_s:
            return
        width, height = frame_size
        with self.lock:
            if t <= self.last_t:
                return
            self.last_t = t
            chairs = [d for d in detections if d.label == "chair"
                      and d.conf >= cfg.CONF_THRESHOLD and min(d.w, d.h) >= cfg.MIN_BOX_W_PX]
            self.tracker.update(chairs, t)
            self.obstacles, self.boxes = [], []
            for tr in self.tracker.tracks:
                if not tr.matched_now or tr.votes["chair"] < 2:
                    continue
                d = tr.det
                # Map box size to drawing depth only. No camera intrinsics, known
                # chair dimensions, speed estimate, TTC, or stopping-distance claim.
                depth = max(1.5, min(16.0, 1.5 / max(0.05, d.h / height)))
                x = max(-0.5, min(0.5, d.cx / width - 0.5)) * depth * 1.2
                self.obstacles.append({
                    "id": f"front:chair:{tr.id}", "source": "front", "label": "chair",
                    "detected_label": "chair", "display_asset": "tree", "placement": "illustrative",
                    "x": round(x, 2), "z": round(-depth, 2), "tier": 0, "live": True,
                    "ttc": None, "path": None, "on_path": False, "alongside": False,
                    "reason": "Chair demo: illustrative placement",
                })
                self.boxes.append((d.x1, d.y1, d.x2, d.y2))
            self.detected_at = time.monotonic()
            self.reason = "Chairs shown as trees" if self.obstacles else "Looking for chairs"

    def unavailable(self, t, reason):
        with self.lock:
            self.obstacles, self.boxes = [], []
            self.tracker = Tracker()
            self.detected_at = 0.0
            self.reason = "Front camera unavailable"

    def snapshot(self):
        with self.lock:
            fresh = (time.monotonic() - self.detected_at <= self.stale_s
                     and time.monotonic() - self.frame_received_at <= self.stale_s)
            return {"status": self.reason if fresh else "Front detection unavailable",
                    "available": fresh, "placement": "illustrative",
                    "obstacles": [dict(o) for o in self.obstacles] if fresh else []}

    def camera_frame(self):
        with self.lock:
            return self.frame, self.frame_received_at

    def preview(self):
        with self.lock:
            if self.frame is None:
                return None
            img, boxes = self.frame.copy(), list(self.boxes)
            state = self.snapshot()
        if state["available"]:
            for box in boxes:
                x1, y1, x2, y2 = map(int, box)
                cv2.rectangle(img, (x1, y1), (x2, y2), (102, 200, 80), 2)
                cv2.putText(img, "chair -> tree", (x1, max(20, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (102, 200, 80), 2)
        cv2.putText(img, "CHAIR DEMO - visual placement only", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        return img

    def close(self):
        pass


def enable_chair_demo(args, rear_source, detector):
    from .forward import DualLiveSource, RearDetectorAdapter
    from .sources import RecoveringCamera
    engine = ChairDemo()
    front = RecoveringCamera(args.front_camera, cfg.FRONT_CAMERA_ROTATE_180)
    source = DualLiveSource(rear_source, front, detector, engine)
    return source, RearDetectorAdapter(source, detector), engine
