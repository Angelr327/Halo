"""Camera-only forward warning: the rear pipeline's box-growth TTC, pointed forward.

No marker and no calibration. The same Tracker and update_metrics() the rear camera uses
turn front YOLO boxes into:
- time to contact, from how fast a box grows. A box twice as wide means half the distance,
  whatever the lens or the object's real size, so TTC needs no calibration at all;
- a rough distance, from box width and a typical width per class (+-20-30%);
- a lateral path: where the object will be, sideways, when you reach it.

The policy warns on TIME, not metres:

    BRAKE    closing, in your path now or predicted to be, contact in <= brake_ttc_s
    CAUTION  the same, contact in <= caution_ttc_s

The threshold is the marker mode's stopping distance (collision.stopping_distance: 1 s
reaction + 0.25 s delay + braking at 1.5 m/s^2 + 0.75 m margin) expressed as time: 2.2-2.4 s
of travel at 1-2 m/s. 2.8 s adds ~0.5 s for the front's detection rate (it shares YOLO with
the rear) and the 2-frame confirmation. In simulated approaches at 1-2 m/s with YOLO-like
box jitter at 5-7.5 detections/s, BRAKE fired before that stopping distance every time
(tests/test_front_mono.py), without measuring metres.

The result has the same fields as collision.CollisionPolicy's, so the OLED, web view, JSON
log and iOS feed work unchanged, plus `obstacles`: every tracked object ahead, for the 3D view.
"""
import json
import math
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass

import cv2

from . import config as cfg
from .perception import CENTER, GlobalMotion, Tracker, update_metrics

TIER_BGR = {0: (200, 200, 200), 2: (0, 160, 255), 3: (40, 40, 255)}


@dataclass(frozen=True)
class MonoSettings:
    brake_ttc_s: float = 2.8
    caution_ttc_s: float = 4.5
    confirm_frames: int = 2      # consecutive front detections before a tier is shown
    hold_s: float = 0.8          # BRAKE stays up at least this long
    stale_s: float = 0.5         # front frames older than this are skipped, not inferred
    bike_front_m: float = 0.3    # bike's leading edge ahead of the helmet camera (~0.3 walking, 0.6-0.9 riding)


def display_asset(label):
    return "tree" if label == "chair" else None       # the 3D view draws chairs as trees


class MonoFrontPolicy:
    def __init__(self, settings=None):
        self.cfg = settings or MonoSettings()
        self.reset()

    def reset(self):
        self.streaks = {}            # track id -> (frames at BRAKE level, frames at CAUTION level or above)
        self.brake_until = -math.inf
        self.result = self._empty("INITIALIZING", "WAITING FOR FRONT CAMERA", 0.0)

    def _empty(self, state, reason, t):
        return dict(state=state, reason=reason, t=t, source="front", mode="camera-only", target_id=None,
                    detected_label=None, display_asset=None, valid=False, alignment_valid=False,
                    x=None, z=None, distance_m=None, speed_mps=None, speed_error_mps=None, trend="unknown",
                    ttc_s=None, warning_distance_m=None, measurement_age_s=None, on_path=False,
                    speed_basis="camera-only estimate from box growth", obstacles=[])

    def unavailable(self, t, reason):
        self.streaks.clear()
        self.result = self._empty("BRAKE" if t < self.brake_until else "UNAVAILABLE", reason, t)
        return dict(self.result)

    def _assess(self, tr, age_s):
        """(tier this frame, seconds until the bike's front reaches it, in your path) for one track."""
        in_path = tr.zone == CENTER or tr.on_path
        ttc = None
        if tr.approaching and math.isfinite(tr.ttc):
            ttc = max(0.0, tr.ttc - age_s) * max(0.0, 1 - self.cfg.bike_front_m / tr.dist_m)
        tier = 0
        if ttc is not None and in_path:
            tier = 3 if ttc <= self.cfg.brake_ttc_s else 2 if ttc <= self.cfg.caution_ttc_s else 0
        return tier, ttc, in_path

    def update(self, tracks, t, age_s=0.0):
        n = self.cfg.confirm_frames
        streaks = {tr.id: self.streaks[tr.id] for tr in tracks if tr.id in self.streaks}  # survive a dropout
        rows = []
        for tr in tracks:
            if not tr.matched_now or tr.dist_m is None or tr.lat_m is None:
                continue
            tier, ttc, in_path = self._assess(tr, age_s)
            b, c = streaks.get(tr.id, (0, 0))
            b, c = (b + 1 if tier >= 3 else 0), (c + 1 if tier >= 2 else 0)
            streaks[tr.id] = (b, c)
            rows.append((3 if b >= n else 2 if c >= n else 0, ttc, in_path, tr))
        self.streaks = streaks
        if any(row[0] == 3 for row in rows):
            self.brake_until = t + self.cfg.hold_s
        rows.sort(key=lambda r: (-r[0], r[1] if r[1] is not None else math.inf, not r[2], r[3].dist_m))
        target = rows[0] if rows else None
        if t < self.brake_until:
            state = "BRAKE"
            reason = f"TTC {target[1]:.1f}s {target[3].label} ahead" if target and target[0] == 3 else "BRAKE HOLD"
        elif target and target[0] == 2:
            state, reason = "CAUTION", f"TTC {target[1]:.1f}s {target[3].label} closing"
        else:
            state, reason = "CLEAR", "NO COLLISION COURSE" if rows else "NO OBJECT AHEAD"
        r = self._empty(state, reason, t)
        r.update(valid=True, alignment_valid=True, measurement_age_s=age_s,
                 obstacles=[self._obstacle(*row) for row in sorted(rows, key=lambda r: r[3].dist_m)])
        if target:
            tier, ttc, in_path, tr = target
            speed = tr.dist_m / tr.ttc if tr.approaching and math.isfinite(tr.ttc) and tr.ttc > 0 else None
            receding = tr.ready and tr.growth <= 1 / cfg.MIN_GROWTH_RATIO
            r.update(target_id=f"front:{tr.id}", detected_label=tr.label, display_asset=display_asset(tr.label),
                     confidence=round(float(tr.det.conf), 3),
                     x=tr.lat_m, z=tr.dist_m, distance_m=max(0.0, tr.dist_m - self.cfg.bike_front_m),
                     speed_mps=speed, trend="closing" if speed else "receding" if receding else "stationary",
                     ttc_s=ttc, warning_distance_m=speed * self.cfg.brake_ttc_s if speed else None, on_path=in_path)
        self.result = r
        return dict(r)

    @staticmethod
    def _obstacle(tier, ttc, in_path, tr):
        return {"id": f"front:{tr.id}", "label": tr.label, "display_asset": display_asset(tr.label),
                "confidence": round(float(tr.det.conf), 3),
                "x": tr.lat_m, "z": tr.dist_m, "tier": tier, "ttc": ttc, "on_path": in_path}


class MonoFrontEngine:
    """Drop-in for forward.ForwardEngine (same methods), so DualLiveSource, DualReplaySource,
    the simulator and main.py drive either one. Only on_detections() does real work."""
    mode = "camera-only"

    def __init__(self, callback, log_path=None, settings=None, classes=None):
        self.settings = settings or MonoSettings()
        self.front_classes = dict(classes or cfg.FRONT_CLASSES)
        self.callback = callback
        self.lock = threading.RLock()
        self.frame, self.frame_received_at = None, 0.0
        self.boxes = []
        self.detection_times = deque(maxlen=120)
        self.log = open(log_path, "w", encoding="utf-8") if log_path else None
        if self.log:
            self.log.write(json.dumps({"type": "session", "mode": self.mode, "settings": asdict(self.settings),
                                       "classes": self.front_classes, "hfov_deg": cfg.HFOV_DEG,
                                       "focal_px": cfg.FOCAL_PX, "class_width_m": cfg.CLASS_WIDTH_M},
                                      allow_nan=False) + "\n")
        self.reset()

    @property
    def stale_s(self):
        return self.settings.stale_s

    def reset(self):
        with self.lock:
            self.policy = MonoFrontPolicy(self.settings)
            self.tracker, self.motion = Tracker(), GlobalMotion()
            self.last_t = None
            self.boxes = []
            self.detection_times.clear()
            self.latest = dict(self.policy.result)

    def measure(self, frame):
        return None                                      # nothing to measure without YOLO boxes

    def on_frame(self, frame, t, meas=None, age_s=0.0):
        with self.lock:
            self.frame, self.frame_received_at = frame, time.monotonic()

    def on_detections(self, dets, t, meas, frame, age_s=0.0):
        with self.lock:
            if self.last_t is not None and t <= self.last_t:
                self.reset()                             # seek or loop: never carry TTC history across it
            self.last_t = t
            h, w = frame.shape[:2]
            self.motion.update(frame, t, [(tr.det.x1, tr.det.y1, tr.det.x2, tr.det.y2)
                                          for tr in self.tracker.tracks if not tr.coasting(t)])
            shaky = self.motion.shaky(t)
            self.tracker.update(dets, t, self.motion.dx, self.motion.dy)
            vp = self.motion.vp_x(w)
            for tr in self.tracker.tracks:
                if tr.matched_now:
                    update_metrics(tr, t, w, vp, shaky, frame_h=h)
            result = self.policy.update(self.tracker.tracks, t, age_s)
            tiers = {o["id"]: (o["tier"], o["ttc"]) for o in result["obstacles"]}
            self.boxes = [(tr.det, tr.label, tr.dist_m, *tiers.get(f"front:{tr.id}", (0, None)))
                          for tr in self.tracker.tracks if tr.matched_now]
            self.detection_times.append(t)
            dt = self.detection_times
            hz = (len(dt) - 1) / (dt[-1] - dt[0]) if len(dt) > 1 and dt[-1] > dt[0] else 0.0
            result.update(front_yolo_hz=hz, range_hz=hz, shaky=shaky)
            self._publish(result)

    def unavailable(self, t, reason):
        with self.lock:
            self.tracker, self.motion = Tracker(), GlobalMotion()   # a gap breaks every track's history
            self.boxes = []
            self._publish(self.policy.unavailable(t, reason))

    def _publish(self, result):
        self.latest = dict(result)
        self.callback(dict(result))
        if self.log:
            self.log.write(json.dumps(result, allow_nan=False) + "\n")
            self.log.flush()

    def snapshot(self):
        with self.lock:
            return dict(self.latest)

    def camera_frame(self):
        with self.lock:
            return self.frame, self.frame_received_at

    def preview(self):
        with self.lock:
            frame, r, boxes = self.frame, dict(self.latest), list(self.boxes)
        if frame is None:
            return None
        img = frame.copy()
        for d, label, dist, tier, ttc in boxes:
            color = TIER_BGR.get(tier, TIER_BGR[0])
            cv2.rectangle(img, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), color, 2)
            text = f"{label} est {dist:.1f}m" + (f" {ttc:.1f}s" if ttc is not None else "") if dist else label  # no "~" in Hershey fonts
            cv2.putText(img, text, (int(d.x1), max(12, int(d.y1) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        color = (30, 30, 255) if r["state"] == "BRAKE" else (0, 220, 255)
        lines = [f"FRONT  {r['state']}  (camera-only)", r["reason"]]
        if r.get("z") is not None:
            speed = f" | closing {r['speed_mps']:.1f} m/s" if r.get("speed_mps") else ""
            contact = f" | contact {r['ttc_s']:.1f} s" if r.get("ttc_s") is not None else ""
            lines.append(f"est {r['z']:.1f} m{speed}{contact}")
        width = max(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0] for line in lines)
        box = img[:12 + 25 * len(lines), :min(img.shape[1], width + 16)]
        box[:] = (box * 0.35).astype(img.dtype)                  # dark panel behind the status lines
        for i, line in enumerate(lines):
            cv2.putText(img, line, (8, 23 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return img

    def close(self):
        with self.lock:
            if self.log:
                self.log.close()
                self.log = None
