"""Incident recorder: a dashcam for close calls (feature 3, the Pi side of the incident report).

Keeps the last INCIDENT_PRE_S seconds of rear-camera frames in memory as small JPEGs (a few MB).
When an alert at INCIDENT_MIN_TIER or above fires, or the rider marks one (key i / web button),
it keeps recording INCIDENT_POST_S more, then a background thread writes:

    incidents/<id>/clip.mp4    H.264 clip, annotated with boxes, time-to-contact and measured gap
    incidents/<id>/thumb.jpg   the frame the alert fired on
    incidents/<id>/meta.json   what happened: vehicle, side, tier, TTC, measured clearance, the
                               vehicle's distance trace, a local summary, and Gemini's analysis

The phone app lists them at /api/v1/incidents and plays the clip (see streamer.py).

Gemini (optional, never blocking the helmet) gets a few annotated key frames plus the measured
facts and returns a structured report. The measurements are authoritative; Gemini adds what a
sensor can't: what the vehicle was and what it did. The API key stays on the Pi.
"""
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from . import config as cfg

try:
    from google.genai import types
except ImportError:
    types = None

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SIDE_WORD = {"LEFT": "left", "RIGHT": "right", "CENTER": "behind"}
TIER_NAME = {1: "LOW", 2: "MED", 3: "HIGH"}
TIER_BGR = {0: (200, 200, 200), 1: (0, 215, 215), 2: (0, 140, 255), 3: (0, 0, 255)}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": [
            "close_pass", "near_miss", "aggressive_overtake", "tailgating", "normal_pass", "false_alarm", "unclear"]},
        "severity": {"type": "integer", "minimum": 1, "maximum": 5},
        "vehicle": {"type": "string", "description": "e.g. 'white pickup truck', 'city bus', 'cyclist'"},
        "maneuver": {"type": "string", "description": "what it did, e.g. 'overtook without moving over'"},
        "summary": {"type": "string", "description": "at most 45 words, factual, for an incident report"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["classification", "severity", "vehicle", "maneuver", "summary", "confidence"],
}
ANALYSIS_PROMPT = """You review dashcam frames from a cyclist's rear-facing helmet camera to write an
incident report. The frames are mirrored like a rear-view mirror: image-left is the rider's LEFT.
The vehicle the helmet alerted on has a RED box. Measured facts from the helmet's sensors are
authoritative: never contradict the measured distances, side or timing. Describe only what the
frames and facts show: the vehicle, what it did, and how dangerous it was. If the frames are
unclear, say so and lower your confidence. Never blame the rider; never say something was safe."""


@dataclass
class _Frame:
    t: float
    jpeg: bytes
    size: tuple                  # (w, h) of the stored JPEG
    boxes: list                  # [(track_id, label, tier, x1, y1, x2, y2, caption)] in stored-JPEG pixels


@dataclass
class _Open:
    """An incident still collecting its post-roll."""
    id: str
    t_trigger: float
    t_end: float
    wall: float
    trigger: dict
    track_id: object
    events: list = field(default_factory=list)
    peak_tier: int = 0
    min_ttc: float = math.inf
    min_measured: float = math.inf
    ttc_at_alert: float = None                  # how much warning the rider got
    measured_zone: str = None                   # side a side sensor measured it passing on
    trace: list = field(default_factory=list)   # (t - t_trigger, dist_m, lat_m) of the trigger track


class IncidentRecorder:
    def __init__(self, client=None, directory=None):
        self.dir = directory or cfg.INCIDENT_DIR
        self.client = client
        self.frames = []                         # ring buffer of _Frame, oldest first
        self._last_add = -1e9
        self.open = None
        self._cooldown = {}                      # track_id -> pipeline time its incident closed
        self._queued = []                        # (fire, t, tracks) from OTHER vehicles while one is recording
        self._ids = set()                        # names handed out (folders appear only once written)
        self._jobs = queue.Queue()               # ("write", ...) / ("analyze", id)
        self._lock = threading.Lock()
        self.analysis_calls = 0
        self._last_analysis = -1e9
        self.latest_id = None
        self.writing = 0
        self.enabled = cfg.INCIDENT_ENABLED
        if self.enabled:
            os.makedirs(self.dir, exist_ok=True)
            threading.Thread(target=self._worker, daemon=True).start()
        self.ffmpeg = _find_ffmpeg()

    # ------------------------------------------------------------------ main-loop side (cheap)
    @property
    def recording(self):
        return self.open is not None

    @property
    def active_reference(self):
        """Small telemetry reference for associating a live HIGH alert with its eventual clip."""
        o = self.open
        return None if o is None else {"id": o.id, "track_id": o.track_id, "tier": o.peak_tier}

    def add_frame(self, frame, t, tracks=(), contacts=()):
        """Buffer one rider-view frame (throttled to INCIDENT_FPS) and update the open incident."""
        if not self.enabled or frame is None:
            return
        if self.open is not None:
            self._update_open(t, tracks, contacts)
        period = 1.0 / cfg.INCIDENT_FPS
        if t >= self._last_add + period - 1e-6:        # steady clock: 15 fps in -> 2 of every 3 frames kept
            self._last_add = max(self._last_add + period, t - period)
            h, w = frame.shape[:2]
            s = cfg.INCIDENT_FRAME_WIDTH / w
            small = cv2.resize(frame, (cfg.INCIDENT_FRAME_WIDTH, int(h * s)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, int(cfg.INCIDENT_JPEG_QUALITY)])
            if ok:
                boxes = []
                for tr in tracks:
                    if tr.matched_now:
                        d = tr.det
                        boxes.append((tr.id, tr.label, tr.shown_tier, d.x1 * s, d.y1 * s, d.x2 * s, d.y2 * s,
                                      _caption(tr)))
                self.frames.append(_Frame(t, buf.tobytes(), (small.shape[1], small.shape[0]), boxes))
        keep = cfg.INCIDENT_PRE_S + cfg.INCIDENT_MAX_S
        while self.frames and t - self.frames[0].t > keep:
            self.frames.pop(0)
        self.poll(t)

    def trigger(self, fire, t, tracks=(), force=False):
        """An alert fired. Starts an incident, or merges into the one still recording."""
        if not self.enabled or (fire.tier < cfg.INCIDENT_MIN_TIER and not force):
            return None
        event = {"t": round(t, 2), "tier": fire.tier, "zone": fire.zone, "label": fire.label, "reason": fire.reason,
                 "track_id": fire.track_id}
        tid = fire.track_id if fire.track_id != -1 else None
        if self.open is not None:
            o = self.open
            if tid is not None and o.track_id is not None and tid != o.track_id:
                # a different vehicle: its own report once this one is written (its pre-roll is still buffered)
                if not any(q[0].track_id == tid for q in self._queued):
                    self._queued.append((fire, t, list(tracks)))
                return None
            o.events.append(event)
            if fire.tier > o.peak_tier:
                o.peak_tier, o.trigger = fire.tier, event
            o.t_end = min(max(o.t_end, t + cfg.INCIDENT_POST_S), o.t_trigger + cfg.INCIDENT_MAX_S)
            return o.id
        if tid is not None and t - self._cooldown.get(tid, -1e9) < cfg.INCIDENT_COOLDOWN_S:
            return None                          # the same vehicle already has a report
        wall = time.time()
        iid = datetime.fromtimestamp(wall).strftime("%Y%m%d-%H%M%S") + f"-{SIDE_WORD.get(fire.zone, 'x')[0]}"
        while iid in self._ids or os.path.exists(os.path.join(self.dir, iid)):
            iid += "x"                           # two incidents in the same second on the same side
        self._ids.add(iid)
        self.open = _Open(iid, t, t + cfg.INCIDENT_POST_S, wall, event, tid, [event], fire.tier)
        for tr in tracks:
            if tr.id == tid and math.isfinite(tr.ttc):
                self.open.ttc_at_alert = round(tr.ttc, 1)
        print(f"[INCIDENT] recording {iid}: {fire.label} {SIDE_WORD.get(fire.zone, '')} ({fire.reason})")
        self._update_open(t, tracks, ())
        return iid

    def trigger_manual(self, t, tracks=()):
        """The rider pressed 'mark incident'."""
        return self.trigger(_ManualFire(t), t, tracks, force=True)

    def poll(self, t):
        """Close the open incident once its post-roll is done; the writing happens off-thread."""
        o = self.open
        if o is None or t < o.t_end:
            return
        self.open = None
        if o.track_id is not None:
            self._cooldown[o.track_id] = t
        frames = [f for f in self.frames if o.t_trigger - cfg.INCIDENT_PRE_S <= f.t <= o.t_end]
        meta = self._meta(o, frames)
        with self._lock:
            self.writing += 1
        self._jobs.put(("write", o, frames, meta))
        queued, self._queued = self._queued, []
        for fire, tq, tracks in queued:                # other vehicles that alerted meanwhile
            if self.open is None:
                self.trigger(fire, tq, tracks)
            else:
                self._queued.append((fire, tq, tracks))
        if self.open is not None and t >= self.open.t_end:
            self.poll(t)

    def close(self, timeout=15.0):
        """On quit: finish the open incident and wait for pending writes."""
        while self.open is not None:
            self.poll(self.open.t_end)
        deadline = time.monotonic() + timeout
        while self.writing and time.monotonic() < deadline:
            time.sleep(0.1)

    def _update_open(self, t, tracks, contacts):
        o = self.open
        for tr in tracks:
            if tr.id != o.track_id:
                continue
            if math.isfinite(tr.ttc) and tr.matched_now:
                o.min_ttc = min(o.min_ttc, tr.ttc)
            if tr.measured_clearance_m is not None:
                o.min_measured = min(o.min_measured, tr.measured_clearance_m)
            if tr.matched_now and tr.dist_m is not None and (not o.trace or o.trace[-1][0] < round(t - o.t_trigger, 2)):
                o.trace.append((round(t - o.t_trigger, 2), round(tr.dist_m, 2),
                                None if tr.lat_m is None else round(tr.lat_m, 2)))
        for c in contacts:
            if c.track_id == o.track_id or (o.track_id is None and c.zone == o.trigger["zone"]):
                if c.clearance_m < o.min_measured:
                    o.min_measured, o.measured_zone = c.clearance_m, c.zone

    # ------------------------------------------------------------------ report contents
    def _meta(self, o, frames):
        tr = o.trigger
        zone = o.measured_zone or tr["zone"]       # a side sensor saw it pass: that's the side it passed on
        side = SIDE_WORD.get(zone, "behind")
        label = tr["label"] if tr["label"] not in ("unknown", "manual") else "vehicle"
        measured = None if not math.isfinite(o.min_measured) else round(o.min_measured, 2)
        min_ttc = None if not math.isfinite(o.min_ttc) else round(o.min_ttc, 1)
        if tr["label"] == "manual":
            local = "Marked by the rider."
        elif "cutting in" in tr["reason"] and not o.measured_zone:
            local = f"{label.capitalize()} cut into your lane from the {side}"
        elif zone == "CENTER":
            local = f"{label.capitalize()} closed fast from directly behind"
        elif "close pass" in tr["reason"] or (measured is not None and measured < cfg.CLOSE_PASS_M):
            local = f"{label.capitalize()} passed close on your {side}"
        else:
            local = f"{label.capitalize()} came alongside on your {side}"
        warn = o.ttc_at_alert
        bits = [f"measured gap {measured:.2f} m" if measured is not None else None,
                f"warned {warn:.1f} s before contact" if warn is not None and warn > 0 else None]
        bits = [b for b in bits if b]
        if bits and tr["label"] != "manual":
            local += " (" + ", ".join(bits) + ")"
        return {
            "id": o.id,
            "time": datetime.fromtimestamp(o.wall).isoformat(timespec="seconds"),
            "kind": "manual" if tr["label"] == "manual" else "auto",
            "tier": o.peak_tier,
            "severity": TIER_NAME.get(o.peak_tier, "MARKED"),
            "zone": zone,
            "side": side,
            "label": tr["label"],
            "reason": tr["reason"],
            "track_id": o.track_id,
            "measured_clearance_m": measured,
            "min_ttc_s": min_ttc,
            "ttc_at_alert_s": o.ttc_at_alert,
            "trace": o.trace[-200:],
            "events": o.events,
            "local_summary": local + ".",
            "duration_s": round(frames[-1].t - frames[0].t, 1) if len(frames) > 1 else 0.0,
            "frames": len(frames),
            "clip": None,
            "thumb": None,
            "analysis": None,
            "analysis_status": "pending" if (cfg.INCIDENT_AUTO_ANALYZE and self.client) else
                               ("not_configured" if not self.client else "not_requested"),
        }

    # ------------------------------------------------------------------ background worker
    def request_analysis(self, iid):
        if not ID_RE.match(iid or "") or not os.path.exists(self._path(iid, "meta.json")):
            return False
        if self.client is None:
            return False
        self._update_meta(iid, analysis_status="pending")
        self._jobs.put(("analyze", iid))
        return True

    def _worker(self):
        while True:
            job = self._jobs.get()
            try:
                if job[0] == "write":
                    _, o, frames, meta = job
                    try:
                        self._write(o, frames, meta)
                    finally:
                        with self._lock:
                            self.writing -= 1
                    if meta["analysis_status"] == "pending":
                        self._analyze(meta["id"])
                elif job[0] == "analyze":
                    self._analyze(job[1])
            except Exception as e:                        # never let a bad job kill the worker
                print(f"[INCIDENT] {job[0]} failed: {type(e).__name__}: {e}")

    def _write(self, o, frames, meta):
        folder = os.path.join(self.dir, o.id)
        os.makedirs(folder, exist_ok=True)
        imgs = [self._annotate(f, o) for f in frames]
        if imgs:
            trig = min(range(len(frames)), key=lambda i: abs(frames[i].t - o.t_trigger))
            cv2.imwrite(os.path.join(folder, "thumb.jpg"), imgs[trig], [cv2.IMWRITE_JPEG_QUALITY, 85])
            meta["thumb"] = "thumb.jpg"
            span = frames[-1].t - frames[0].t
            fps = (len(frames) - 1) / span if len(frames) > 1 and span > 0 else cfg.INCIDENT_FPS
            fps = min(30.0, max(1.0, fps))              # the rate actually captured: plays back in real time
            if _encode_mp4(imgs, os.path.join(folder, "clip.mp4"), round(fps, 3), self.ffmpeg):
                meta["clip"] = "clip.mp4"
        _write_json(os.path.join(folder, "meta.json"), meta)
        self.latest_id = o.id
        self._prune()
        print(f"[INCIDENT] saved {folder} ({meta['duration_s']} s, {meta['local_summary']})")

    def _annotate(self, f, o):
        img = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
        if not cfg.INCIDENT_ANNOTATE:
            return img
        for tid, label, tier, x1, y1, x2, y2, cap in f.boxes:
            target = tid == o.track_id
            col = (0, 0, 255) if target else TIER_BGR.get(tier, (200, 200, 200))
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), col, 3 if target else 1)
            if cap:
                cv2.putText(img, cap, (int(x1), max(12, int(y1) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1,
                            cv2.LINE_AA)
        dt = f.t - o.t_trigger
        stamp = datetime.fromtimestamp(o.wall + dt).strftime("%H:%M:%S")
        bar = f"{stamp}  {'ALERT' if abs(dt) < 0.06 else f'{dt:+.1f}s'}  {o.trigger['label']} {SIDE_WORD.get(o.trigger['zone'], '')}"
        cv2.rectangle(img, (0, img.shape[0] - 20), (img.shape[1], img.shape[0]), (0, 0, 0), -1)
        cv2.putText(img, bar, (6, img.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        return img

    def _analyze(self, iid):
        meta = self.get(iid)
        if meta is None or self.client is None or types is None:
            return
        if self.analysis_calls >= cfg.INCIDENT_GEMINI_BUDGET:
            self._update_meta(iid, analysis_status="budget_exhausted")
            return
        wait = self._last_analysis + cfg.GEMINI_MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_analysis = time.monotonic()
        self.analysis_calls += 1
        parts = [types.Part.from_bytes(data=j, mime_type="image/jpeg") for j in self._keyframes_for(iid)]
        facts = {k: meta.get(k) for k in ("side", "label", "severity", "reason", "measured_clearance_m",
                                          "ttc_at_alert_s", "min_ttc_s", "duration_s")}
        facts["distance_trace_m"] = [d for _, d, _ in meta.get("trace", [])][::5]
        parts.append(types.Part.from_text(text=(
            f"Measured facts (authoritative): {json.dumps(facts)}\n"
            f"The frames are in time order, about {meta.get('duration_s', 0)} s in total. Write the report.")))
        try:
            resp = self.client.models.generate_content(
                model=cfg.GEMINI_MODEL, contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(system_instruction=ANALYSIS_PROMPT, max_output_tokens=600,
                                                   response_mime_type="application/json",
                                                   response_json_schema=ANALYSIS_SCHEMA))
            data = json.loads((resp.text or "").strip())
            self._update_meta(iid, analysis=data, analysis_status="done")
            print(f"[INCIDENT] {iid} analysed: {data.get('summary', '')}")
        except Exception as e:
            self._update_meta(iid, analysis_status="failed", analysis_error=f"{type(e).__name__}: {e}"[:200])

    def _keyframes_for(self, iid):
        """INCIDENT_KEYFRAMES evenly spaced frames from the saved clip (annotated), as JPEG bytes."""
        out = []
        cap = cv2.VideoCapture(self._path(iid, "clip.mp4"))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 0:
            for i in np.linspace(0, n - 1, min(cfg.INCIDENT_KEYFRAMES, n)).astype(int):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
                ok, img = cap.read()
                if ok:
                    img = cv2.resize(img, tuple(cfg.GEMINI_FRAME_SIZE), interpolation=cv2.INTER_AREA)
                    out.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes())
        cap.release()
        if not out and os.path.exists(self._path(iid, "thumb.jpg")):
            with open(self._path(iid, "thumb.jpg"), "rb") as fh:
                out.append(fh.read())
        return out

    # ------------------------------------------------------------------ storage (used by the web API)
    def _path(self, iid, name):
        return os.path.join(self.dir, iid, name)

    def get(self, iid):
        if not ID_RE.match(iid or ""):
            return None
        try:
            with open(self._path(iid, "meta.json")) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def list(self):
        if not os.path.isdir(self.dir):
            return []
        out = [self.get(d) for d in sorted(os.listdir(self.dir), reverse=True)]
        return [m for m in out if m]

    def file_path(self, iid, name):
        """Absolute path of a servable file (clip.mp4 / thumb.jpg), or None."""
        if not ID_RE.match(iid or "") or name not in ("clip.mp4", "thumb.jpg"):
            return None
        p = self._path(iid, name)
        return p if os.path.isfile(p) else None

    def _update_meta(self, iid, **fields):
        with self._lock:
            meta = self.get(iid)
            if meta is None:
                return
            meta.update(fields)
            _write_json(self._path(iid, "meta.json"), meta)

    def _prune(self):
        ids = sorted(d for d in os.listdir(self.dir) if ID_RE.match(d))
        for old in ids[:-cfg.INCIDENT_MAX_KEEP] if len(ids) > cfg.INCIDENT_MAX_KEEP else []:
            shutil.rmtree(os.path.join(self.dir, old), ignore_errors=True)


class _ManualFire:
    def __init__(self, t):
        self.tier, self.zone, self.label, self.reason, self.track_id, self.t = 0, "CENTER", "manual", "marked by rider", -1, t


def _caption(tr):
    if tr.measured_clearance_m is not None:
        return f"{tr.label} {tr.measured_clearance_m:.2f}m gap"
    if math.isfinite(tr.ttc) and tr.shown_tier >= 1:
        return f"{tr.label} TTC {tr.ttc:.1f}s"
    return tr.label


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)                        # readers never see a half-written file


def _find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _encode_mp4(imgs, path, fps, ffmpeg):
    """H.264 + faststart (plays on iPhone and in browsers) via ffmpeg; OpenCV mp4v as a fallback."""
    h, w = imgs[0].shape[:2]
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
               "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
        if shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd                 # never starve the detector
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            for img in imgs:
                p.stdin.write(np.ascontiguousarray(img).tobytes())
            _, err = p.communicate(timeout=60)
            if p.returncode == 0 and os.path.getsize(path) > 0:
                return True
            print(f"[INCIDENT] ffmpeg failed: {err.decode(errors='ignore')[-200:]}")
        except Exception as e:
            print(f"[INCIDENT] ffmpeg failed: {type(e).__name__}: {e}")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for img in imgs:
        vw.write(img)
    vw.release()
    return os.path.exists(path) and os.path.getsize(path) > 0
