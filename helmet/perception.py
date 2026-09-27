"""Detection, camera-shake estimation, tracking and per-vehicle metrics.

Key ideas
- TTC from box EXPANSION: area ~ 1/Z^2, so TTC = 2*A / (dA/dt). We fit it from the
  early-half vs late-half means of a history buffer (never endpoint-to-endpoint) and
  require most frame-to-frame changes to be growth, which kills YOLO jitter.
- Expansion is almost immune to camera shake (a head turn shifts boxes, it doesn't
  scale them). Position is NOT immune, so position gets motion compensation,
  smoothing and zone hysteresis.
- Lateral offset in metres = (box_cx - straight_behind_x) / box_w * real_width.
  That ratio does not depend on distance, so a far car in the next lane is already
  "left" instead of looking centred until it's close.
- A box cut off by the frame edge stops growing on that axis, which would read as
  "slowing down". Growth is measured on the axis that isn't cut off: width when the
  top or bottom is clipped (a close person's feet leave the frame), height when a side is.
- Trajectory: a linear fit of lateral offset over time, extended to the moment of
  contact, says whether a vehicle will END UP in your lane, not just whether it is there now.
"""
import math
import os
import shutil
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import config as cfg

LEFT, CENTER, RIGHT = "LEFT", "CENTER", "RIGHT"


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    label: str

    @property
    def w(self): return max(1.0, self.x2 - self.x1)
    @property
    def h(self): return max(1.0, self.y2 - self.y1)
    @property
    def cx(self): return (self.x1 + self.x2) / 2
    @property
    def cy(self): return (self.y1 + self.y2) / 2
    @property
    def area(self): return self.w * self.h


def _iou(a, b):
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    return inter / (a.area + b.area - inter + 1e-9)


def clean_detections(dets):
    """Cross-class duplicate removal + drop the 'person' box of someone riding a bike."""
    dets = sorted(dets, key=lambda d: -d.conf)
    kept = []
    for d in dets:
        if any(_iou(d, k) > cfg.DUPLICATE_IOU for k in kept):
            continue
        kept.append(d)
    riders = [d for d in kept if d.label in ("bicycle", "motorcycle")]
    out = []
    for d in kept:
        if d.label == "person" and riders:
            overlap = max(
                max(0.0, min(d.x2, r.x2) - max(d.x1, r.x1)) * max(0.0, min(d.y2, r.y2) - max(d.y1, r.y1))
                for r in riders) / d.area
            if overlap > 0.3:
                continue
        out.append(d)
    return out


def resolve_model_path():
    """On a Pi, turn yolo26n.pt into an NCNN model once and reuse it.
    NCNN models are fixed to the image size they were exported at, so the size is in the name."""
    path = cfg.MODEL_PATH
    if not (cfg.AUTO_EXPORT_NCNN and path.endswith(".pt")):
        return path
    stem = os.path.splitext(os.path.basename(path))[0]
    target = f"{stem}_imgsz{cfg.IMGSZ}_ncnn_model"      # must end in _ncnn_model
    if os.path.isdir(target):
        return target
    from ultralytics import YOLO
    print(f"Exporting {path} to NCNN at imgsz={cfg.IMGSZ} (one time, ~1-2 min)...")
    exported = YOLO(path).export(format="ncnn", imgsz=cfg.IMGSZ)
    shutil.move(str(exported), target)
    return target


class VehicleDetector:
    """YOLO wrapper using raw result.boxes (no supervision library)."""

    def __init__(self):
        from ultralytics import YOLO   # imported here so tests run without ultralytics
        path = resolve_model_path()
        self.model_name = os.path.basename(path.rstrip("/"))
        self.model = YOLO(path, task="detect")
        self.last_ms = 0.0
        dummy = np.zeros((cfg.CAPTURE_HEIGHT, cfg.CAPTURE_WIDTH, 3), np.uint8)
        self.model.predict(dummy, imgsz=cfg.IMGSZ, device=cfg.DEVICE, verbose=False)   # warm-up

    def class_map(self):
        m = dict(cfg.VEHICLE_CLASSES)
        if cfg.DEMO_PERSON_AS_VEHICLE:
            m[0] = "person"
        return m

    def detect(self, frame, class_map=None):
        t0 = time.perf_counter()
        cmap = self.class_map() if class_map is None else class_map
        r = self.model.predict(frame, imgsz=cfg.IMGSZ, conf=cfg.CONF_THRESHOLD, classes=list(cmap),
                               device=cfg.DEVICE, verbose=False)[0]
        dets = []
        if r.boxes is not None and len(r.boxes):
            xyxy = r.boxes.xyxy.cpu().numpy()
            conf = r.boxes.conf.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
                label = cmap.get(int(k))
                if label:
                    dets.append(Detection(float(x1), float(y1), float(x2), float(y2), float(c), label))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return clean_detections(dets)


class GlobalMotion:
    """Whole-image shift between frames (phase correlation on a tiny grayscale copy).

    dx > 0 means scene content moved right. Used to (1) predict where tracks went,
    (2) flag shaky frames, (3) follow head yaw so 'straight behind' stays correct.
    """

    def __init__(self):
        self.prev, self.win = None, None
        self.dx = self.dy = 0.0
        self.response = 0.0
        self.vp_offset = 0.0
        self.shaky_until = -1.0
        self.last_t = None

    def update(self, frame, t, mask_boxes=()):
        """mask_boxes: last known vehicle boxes. A big approaching truck would otherwise
        dominate the correlation and look like the rider shaking their head."""
        h, w = frame.shape[:2]
        dt = 0.0 if self.last_t is None else max(0.0, t - self.last_t)
        self.last_t = t
        self.dx = self.dy = 0.0
        if not cfg.GLOBAL_MOTION:
            self.prev = None
            return
        s = cfg.MOTION_DOWNSCALE
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (w // s, h // s),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        if self.win is None or self.win.shape != small.shape:
            self.win = cv2.createHanningWindow((small.shape[1], small.shape[0]), cv2.CV_32F)
            self.prev = None
        if self.prev is not None and (dt > 0.5 or float(cv2.absdiff(self.prev, small).mean()) > cfg.CUT_MEAN_DIFF):
            self.prev = None                  # cut (video loop, edited clip, camera reconnect): don't read it as a head turn
            self.vp_offset = 0.0
        if self.prev is not None:
            a, b = self.prev.copy(), small.copy()
            fill_a, fill_b = float(a.mean()), float(b.mean())
            for (x1, y1, x2, y2) in mask_boxes:
                pad = 0.15 * (x2 - x1)
                xa, xb = int(max(0, (x1 - pad) / s)), int(min(small.shape[1], (x2 + pad) / s + 1))
                ya, yb = int(max(0, (y1 - pad) / s)), int(min(small.shape[0], (y2 + pad) / s + 1))
                a[ya:yb, xa:xb] = fill_a
                b[ya:yb, xa:xb] = fill_b
            (dx, dy), resp = cv2.phaseCorrelate(a, b, self.win)
            self.response = resp
            if resp >= cfg.MOTION_MIN_RESPONSE:
                self.dx, self.dy = dx * s, dy * s
        self.prev = small
        if math.hypot(self.dx, self.dy) / w > cfg.SHAKE_GATE_FRAC:
            self.shaky_until = t + cfg.SHAKE_HOLD_S
        if cfg.VP_TRACKING:
            decay = math.exp(-dt / cfg.VP_LEAK_TAU_S) if dt > 0 else 1.0
            lim = cfg.VP_MAX_OFFSET_FRAC * w
            self.vp_offset = float(np.clip(self.vp_offset * decay + self.dx, -lim, lim))
        else:
            self.vp_offset = 0.0

    def shaky(self, t):
        return t < self.shaky_until

    def vp_x(self, width):
        return width / 2 + self.vp_offset


@dataclass
class Sample:
    t: float
    area: float
    cx: float
    cy: float
    w: float
    h: float
    clip_l: bool
    clip_r: bool
    clip_v: bool = False       # top or bottom edge cut off by the frame
    lat: float = None          # raw lateral offset (m), when measurable


@dataclass
class Track:
    id: int
    det: Detection
    first_t: float
    last_t: float
    hist: deque = field(default_factory=lambda: deque(maxlen=cfg.HISTORY_LEN))
    votes: Counter = field(default_factory=Counter)
    vx: float = 0.0            # object motion relative to the scene, px/s
    vy: float = 0.0
    pending_dx: float = 0.0    # global shift accumulated while unmatched
    pending_dy: float = 0.0
    matched_now: bool = False
    # metrics (recomputed each matched frame)
    ready: bool = False
    growth: float = 1.0
    consistency: float = 0.0
    growth_pct_s: float = 0.0
    ttc: float = math.inf
    ttc_valid_t: float = -1.0
    ttc_meas_t: float = -1.0   # last time TTC came from a real measurement (not aging)
    scale_axis: str = "-"      # which box size TTC used: "area", "w" or "h"
    lat_m: float = None
    lat_v: float = None        # lateral velocity, m/s (+ = moving to your right)
    pred_lat_m: float = None   # lateral offset extrapolated to the moment of contact
    on_path: bool = False      # predicted to end up inside your corridor
    clearance_m: float = None
    pred_clearance_m: float = None
    measured_clearance_m: float = None   # from a side ultrasonic sensor (fusion.py), when beside you
    dist_m: float = None
    zone: str = None
    zone_pending: str = None
    zone_count: int = 0
    alongside: bool = False
    approaching: bool = False
    was_approaching: bool = False  # ever approached (so a parked car we pass isn't 'in the blind spot')
    # alert state (owned by risk.AlertPolicy)
    tier: int = 0              # raw tier this frame
    shown_tier: int = 0        # tier after frame confirmation (what the overlay colours)
    cand_tier: int = 0
    cand_count: int = 0
    fired_tier: int = 0
    fired_t: float = -1e9
    reason: str = ""

    @property
    def label(self):
        return self.votes.most_common(1)[0][0] if self.votes else self.det.label

    def predict(self, t):
        dt = max(0.0, t - self.last_t)
        return (self.det.cx + self.vx * dt + self.pending_dx,
                self.det.cy + self.vy * dt + self.pending_dy)

    def coasting(self, t):
        return t - self.last_t > cfg.COAST_S


class Tracker:
    def __init__(self):
        self.tracks = []
        self._next_id = 1
        self.created_times = deque(maxlen=200)

    def update(self, dets, t, gdx=0.0, gdy=0.0):
        for tr in self.tracks:
            tr.matched_now = False
            tr.pending_dx += gdx
            tr.pending_dy += gdy

        pairs = []
        for ti, tr in enumerate(self.tracks):
            px, py = tr.predict(t)
            size = max(tr.det.w, tr.det.h)
            for di, d in enumerate(dets):
                gate = max(cfg.MIN_GATE_PX, cfg.GATE_SCALE * max(size, d.w, d.h))
                dist = math.hypot(d.cx - px, d.cy - py)
                if dist > gate:
                    continue
                ratio = max(d.area, tr.det.area) / max(1.0, min(d.area, tr.det.area))
                if ratio > cfg.MAX_SIZE_RATIO:
                    continue
                cost = dist / gate + 0.2 * math.log(ratio) + (0.3 if d.label != tr.label else 0.0)
                pairs.append((cost, ti, di))
        pairs.sort()
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            self._apply(self.tracks[ti], dets[di], t)

        for di, d in enumerate(dets):
            if di not in used_d:
                tr = Track(id=self._next_id, det=d, first_t=t, last_t=t)
                self._next_id += 1
                self._apply(tr, d, t, new=True)
                self.tracks.append(tr)
                self.created_times.append(t)

        self.tracks = [tr for tr in self.tracks if t - tr.last_t <= cfg.TRACK_STALE_S]

    def _apply(self, tr, d, t, new=False):
        if not new:
            dt = t - tr.last_t
            if dt > 1e-3:
                # object's own motion = observed motion minus camera motion
                ovx = (d.cx - tr.det.cx - tr.pending_dx) / dt
                ovy = (d.cy - tr.det.cy - tr.pending_dy) / dt
                tr.vx = 0.6 * tr.vx + 0.4 * ovx
                tr.vy = 0.6 * tr.vy + 0.4 * ovy
        tr.det = d
        tr.last_t = t
        tr.pending_dx = tr.pending_dy = 0.0
        tr.votes[d.label] += 1
        tr.matched_now = True

    def vehicles_per_minute(self, t):
        return sum(1 for c in self.created_times if t - c <= 60.0)


def focal_px(width):
    if cfg.FOCAL_PX:
        return cfg.FOCAL_PX
    return (width / 2) / math.tan(math.radians(cfg.HFOV_DEG) / 2)


def corridor_half_m():
    """Half-width of your lane: the bike plus a margin on each side."""
    return cfg.RIDER_HALF_WIDTH_M + cfg.CORRIDOR_MARGIN_M


def _scale_series(hist, label):
    """Box size on an axis the frame edge didn't cut off, chosen once for the whole window
    so early and late halves are comparable. Returns (values, axis, power), value ~ 1/Z^power."""
    side = any(s.clip_l or s.clip_r for s in hist)
    vert = any(s.clip_v for s in hist)
    if side and vert:
        return None, "-", 0
    if vert:
        return [s.w for s in hist], "w", 1
    if side or label == "person":          # a walking person's width swings with their arms
        return [s.h for s in hist], "h", 1
    return [s.area for s in hist], "area", 2


def _lateral_fit(hist, t):
    """Least-squares line through recent lateral offsets -> (offset now, velocity m/s)."""
    if not hist or hist[-1].lat is None:
        return None, None
    pts = [(s.t, s.lat) for s in hist if s.lat is not None]
    if len(pts) < cfg.MIN_PATH_SAMPLES or pts[-1][0] - pts[0][0] < 0.4:
        return None, None
    n = len(pts)
    mt = sum(p[0] for p in pts) / n
    ml = sum(p[1] for p in pts) / n
    stt = sum((p[0] - mt) ** 2 for p in pts)
    v = sum((p[0] - mt) * (p[1] - ml) for p in pts) / stt
    # Far boxes are small, so their lateral offset is noisy; projected 2.5 s ahead that noise
    # looks like a lane change. Only trust a drift that stands out from the scatter.
    resid = sum((l - (ml + v * (tt - mt))) ** 2 for tt, l in pts) / (n - 2)
    if abs(v) < cfg.PATH_MIN_SIGNIFICANCE * math.sqrt(resid / stt):
        v = 0.0
    return ml + v * (t - mt), v


def update_metrics(tr, t, frame_w, vp_x, shaky, frame_h=None):
    """Append a sample for a matched track and recompute its smoothed metrics."""
    d = tr.det
    clip_l = d.x1 <= cfg.EDGE_MARGIN_PX
    clip_r = d.x2 >= frame_w - cfg.EDGE_MARGIN_PX
    clip_v = frame_h is not None and (d.y1 <= cfg.EDGE_MARGIN_PX or d.y2 >= frame_h - cfg.EDGE_MARGIN_PX)
    # One side cut off = beside you. Both sides cut off = it fills the frame: right behind you.
    tr.alongside = clip_l != clip_r
    width_m = cfg.CLASS_WIDTH_M.get(tr.label, 1.8)

    # ---- lateral offset / clearance / distance (frozen while side-clipped: box width is wrong)
    lat = None
    if not (clip_l or clip_r) and d.w >= cfg.MIN_BOX_W_PX:
        lat = (d.cx - vp_x) / d.w * width_m
        tr.lat_m = lat if tr.lat_m is None else (1 - cfg.LAT_EMA_ALPHA) * tr.lat_m + cfg.LAT_EMA_ALPHA * lat
        tr.dist_m = focal_px(frame_w) * width_m / d.w
    if tr.lat_m is not None:
        tr.clearance_m = abs(tr.lat_m) - width_m / 2 - cfg.RIDER_HALF_WIDTH_M
    tr.hist.append(Sample(t, d.area, d.cx, d.cy, d.w, d.h, clip_l, clip_r, clip_v, lat))

    # ---- growth / consistency / TTC from early-half vs late-half means
    n = len(tr.hist)
    tr.ready = n >= cfg.MIN_HISTORY_FOR_TTC and d.w >= cfg.MIN_BOX_W_PX
    vals, axis, power = _scale_series(tr.hist, tr.label) if tr.ready else (None, "-", 0)
    if vals is not None:
        hist = list(tr.hist)
        half = n // 2
        # Average log-size (geometric mean): size grows fastest near the end, and an
        # arithmetic mean would be dominated by the last few samples and skew the timing.
        ls_e = sum(math.log(max(v, 1.0)) for v in vals[:half]) / half
        ls_l = sum(math.log(max(v, 1.0)) for v in vals[n - half:]) / half
        t_e = sum(s.t for s in hist[:half]) / half
        t_l = sum(s.t for s in hist[n - half:]) / half
        la = (2 / power) * (ls_l - ls_e)                   # as a log AREA ratio, whatever the axis,
        tr.growth = math.exp(la)                           # so MIN_GROWTH_RATIO means the same thing
        ups = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
        tr.consistency = ups / (n - 1)
        tr.scale_axis = axis
        if tr.growth > 1.0 and t_l > t_e:
            k = la / (t_l - t_e)                           # d ln(A)/dt;  TTC = 2A/(dA/dt) = 2/k
            tr.growth_pct_s = (math.exp(k) - 1) * 100
            # A secant between the two half-centres estimates TTC at their MIDPOINT;
            # subtract the time since then so the number means "seconds from now".
            ttc = 2.0 / k - (t - (t_e + t_l) / 2)
            tr.ttc = min(cfg.TTC_CAP_S, max(0.0, ttc))
        else:
            tr.growth_pct_s = 0.0 if tr.growth <= 1.0 else tr.growth_pct_s
            tr.ttc = math.inf
        tr.ttc_valid_t = tr.ttc_meas_t = t
    elif tr.ready:
        # Clipped on a side AND top/bottom (very close): nothing to measure. Count the last
        # real estimate down briefly, then give up rather than let it reach 0 on its own.
        tr.scale_axis = "-"
        if math.isfinite(tr.ttc) and tr.ttc_meas_t > 0 and t - tr.ttc_meas_t <= cfg.TTC_AGE_MAX_S:
            tr.ttc = max(0.0, tr.ttc - (t - tr.ttc_valid_t))
            tr.ttc_valid_t = t
        else:
            tr.ttc = math.inf
    tr.approaching = (tr.ready and tr.growth >= cfg.MIN_GROWTH_RATIO
                      and tr.consistency >= cfg.MIN_CONSISTENCY and math.isfinite(tr.ttc))
    tr.was_approaching = tr.was_approaching or tr.approaching

    # ---- trajectory: where will it be, sideways, when it reaches you?
    lat_now, tr.lat_v = _lateral_fit(tr.hist, t)
    if lat_now is not None and tr.approaching:
        tr.pred_lat_m = lat_now + tr.lat_v * min(tr.ttc, cfg.PATH_HORIZON_S)
        tr.pred_clearance_m = abs(tr.pred_lat_m) - width_m / 2 - cfg.RIDER_HALF_WIDTH_M
        tr.on_path = abs(tr.pred_lat_m) - width_m / 2 < corridor_half_m()
    else:
        tr.pred_lat_m = tr.pred_clearance_m = None
        tr.on_path = False

    # ---- zone with hysteresis (edge clipping is unambiguous and overrides)
    if clip_l and clip_r:
        raw = tr.zone or CENTER                           # fills the frame: keep the side it came from
    elif clip_l:
        raw = LEFT
    elif clip_r:
        raw = RIGHT
    elif cfg.ZONE_METHOD == "thirds" or tr.lat_m is None:
        frac = d.cx / frame_w
        raw = LEFT if frac < 1 / 3 else RIGHT if frac > 2 / 3 else CENTER
    else:
        raw = CENTER if abs(tr.lat_m) - width_m / 2 < corridor_half_m() else (LEFT if tr.lat_m < 0 else RIGHT)

    if tr.zone is None or tr.alongside:
        tr.zone, tr.zone_pending, tr.zone_count = raw, None, 0
    elif raw == tr.zone:
        tr.zone_pending, tr.zone_count = None, 0
    elif not shaky:
        if raw == tr.zone_pending:
            tr.zone_count += 1
        else:
            tr.zone_pending, tr.zone_count = raw, 1
        if tr.zone_count >= cfg.ZONE_CONFIRM_FRAMES:
            tr.zone, tr.zone_pending, tr.zone_count = raw, None, 0
