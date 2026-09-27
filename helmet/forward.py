"""Forward demo integration: bounded dual-camera inference and deterministic replay.

Only the inference thread touches YOLO. The marker worker and OLED do not wait
for it. Rear detections are returned to the existing main loop via an adapter.

Two front engines share one interface (measure / on_frame / on_detections):
ForwardEngine (ArUco marker + calibration, --collision-calibration) and
front_mono.MonoFrontEngine (camera-only box-growth TTC, no calibration).
"""
import json
import math
import threading
import time
from collections import deque
from dataclasses import asdict

import cv2

from . import config as cfg
from .collision import Calibration, CollisionPolicy, MarkerRanger
from .front_mono import MonoFrontEngine
from .perception import Tracker
from .sources import RecoveringCamera, VideoSource


class ForwardEngine:
    mode = "marker"
    front_classes = {56: "chair"}

    def __init__(self, calibration, callback, log_path=None):
        self.ranger = MarkerRanger(calibration)
        self.policy = CollisionPolicy(calibration)
        self.tracker = Tracker()  # identity only; never run rear update_metrics here
        self.callback = callback
        self.lock = threading.RLock()
        self.associated_t = -math.inf
        self.chair_id = None
        self.last_detection_t = -math.inf
        self.last_input_t = None
        self.latest = dict(self.policy.result)
        self.frame = None
        self.frame_received_at = 0.0
        self.log = open(log_path, "w", encoding="utf-8") if log_path else None
        if self.log:
            self.log.write(json.dumps({"type": "session", "calibration": calibration.to_dict(),
                                       "settings": asdict(self.policy.cfg)}, allow_nan=False) + "\n")
        self.times = deque(maxlen=120)
        self.detection_times = deque(maxlen=120)

    def reset(self):
        with self.lock:
            self.policy.reset()
            self.tracker = Tracker()
            self.associated_t = self.last_detection_t = -math.inf
            self.chair_id = None
            self.last_input_t = None
            self.times.clear()
            self.detection_times.clear()

    @property
    def stale_s(self):
        return self.policy.cfg.stale_s

    def measure(self, frame):
        """Per-frame work that doesn't need YOLO: the marker pose."""
        return self.ranger.measure(frame)

    def on_frame(self, frame, t, meas, age_s=0.0):
        pose, reason = meas
        self.process(frame, t, pose, reason, age_s=age_s)

    def on_detections(self, dets, t, meas, frame, age_s=0.0):
        self.associate(dets, t, meas[0])

    def associate(self, detections, t, pose):
        with self.lock:
            if t <= self.last_detection_t:
                return
            self.last_detection_t = t
            self.detection_times.append(t)
            self.tracker.update(detections, t)
            if pose is None:
                return
            x, y = pose.center
            matches = [tr for tr in self.tracker.tracks if tr.matched_now and tr.label == "chair"
                       and tr.det.x1 <= x <= tr.det.x2 and tr.det.y1 <= y <= tr.det.y2]
            if len(matches) == 1:
                self.associated_t, self.chair_id = t, matches[0].id
            elif len(matches) > 1:
                self.associated_t = -math.inf  # ambiguity is not an ordinary YOLO dropout

    def process(self, frame, t, pose, reason, *, age_s=0.0):
        with self.lock:
            if self.last_input_t is not None and t <= self.last_input_t:
                self.reset()
            self.last_input_t = t
            self.frame = frame
            self.frame_received_at = time.monotonic()
            result = self.policy.update(pose, t, associated=0 <= t - self.associated_t <= self.policy.cfg.association_s,
                                        age_s=age_s, reason=reason)
            if pose is not None:
                self.times.append(t)
            while self.times and t - self.times[0] > 3:
                self.times.popleft()
            result["range_hz"] = ((len(self.times) - 1) / (self.times[-1] - self.times[0])
                                  if len(self.times) > 1 and self.times[-1] > self.times[0] else 0.0)
            result["chair_track_id"] = f"front:{self.chair_id}" if self.chair_id is not None else None
            dt = self.detection_times
            result["front_yolo_hz"] = (len(dt)-1)/(dt[-1]-dt[0]) if len(dt) > 1 and dt[-1] > dt[0] else 0.0
            self._publish(result)

    def unavailable(self, t, reason):
        with self.lock:
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
            frame, r = self.frame, dict(self.latest)
        if frame is None:
            return None
        img = frame.copy()
        state = r["state"]
        color = (30, 30, 255) if state == "BRAKE" else (0, 220, 255)
        lines = [f"FRONT  {state}", r["reason"], "Approach speed (stationary target)"]
        if r.get("valid"):
            lines += [f"gap {r['distance_m']:.2f} m | speed {r['speed_mps']:.2f} m/s",
                      f"warn {r['warning_distance_m']:.2f} m | age {r['measurement_age_s']*1000:.0f} ms"]
        for i, line in enumerate(lines):
            cv2.putText(img, line, (8, 23 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
            cv2.putText(img, line, (8, 23 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return img

    def close(self):
        with self.lock:
            if self.log:
                self.log.close()
                self.log = None


class RearDetectorAdapter:
    """The existing rear main loop consumes detections from the shared worker."""
    def __init__(self, source, detector):
        self.source, self.detector = source, detector
        self.model_name = detector.model_name

    @property
    def last_ms(self):
        return self.source.rear_ms

    def detect(self, frame):
        return self.source.read_detections


class DualLiveSource:
    is_live = True
    frames_are_mirrored = True  # orientation already applied alongside inference
    ended = False

    def __init__(self, rear, front, detector, engine):
        self.rear, self.front, self.detector, self.engine = rear, front, detector, engine
        self._cond = threading.Condition()
        self._inputs = {}
        self._output = None
        self._running = True
        self.read_detections = []
        self.rear_ms = 0.0
        self.last_ok = time.monotonic()
        self.threads = [threading.Thread(target=self._capture_rear, daemon=True),
                        threading.Thread(target=self._capture_front, daemon=True),
                        threading.Thread(target=self._infer, daemon=True)]
        for th in self.threads:
            th.start()

    def _submit(self, side, packet):
        with self._cond:
            self._inputs[side] = packet  # size one per camera; old work is dropped
            self._cond.notify_all()

    def _capture_rear(self):
        while self._running:
            try:
                frame, t = self.rear.read(timeout=0.1)
                if frame is not None:
                    self._submit("rear", (frame, t, None))
            except Exception as e:
                print(f"[rear capture] {e}")
                time.sleep(0.1)

    def _capture_front(self):
        while self._running:
            try:
                frame, t = self.front.read(timeout=0.1)
                if frame is None:
                    if self.front.stale_for() > self.engine.stale_s:
                        self.engine.unavailable(time.monotonic(), "FRONT CAMERA OFFLINE")
                    continue
                meas = self.engine.measure(frame)
                self._submit("front", (frame, t, meas))
                self.engine.on_frame(frame, t, meas, age_s=max(0.0, time.monotonic() - t))
            except Exception as e:
                self.engine.unavailable(time.monotonic(), f"FRONT ERROR: {type(e).__name__}")
                print(f"[front capture] {e}")
                time.sleep(0.1)

    def _infer(self):
        preferred = "rear"
        while self._running:
            with self._cond:
                self._cond.wait_for(lambda: self._inputs or not self._running, timeout=0.1)
                if not self._running:
                    break
                if not self._inputs:
                    continue
                side = preferred if preferred in self._inputs else next(iter(self._inputs))
                frame, t, meas = self._inputs.pop(side)
                preferred = "front" if side == "rear" else "rear"
            if time.monotonic() - t > self.engine.stale_s:
                continue
            try:
                if side == "front":
                    dets = self.detector.detect(frame, class_map=self.engine.front_classes)
                    self.engine.on_detections(dets, t, meas, frame, age_s=max(0.0, time.monotonic() - t))
                else:
                    image = cv2.flip(frame, 1) if cfg.MIRROR_VIEW else frame
                    dets = self.detector.detect(image)
                    ms = self.detector.last_ms
                    if time.monotonic() - t <= cfg.CAMERA_TIMEOUT_S:
                        with self._cond:
                            self._output = image, t, dets, ms
                            self.last_ok = time.monotonic()
                            self._cond.notify_all()
            except Exception as e:
                print(f"[{side} inference] {e}")
                time.sleep(0.05)

    def read(self, timeout=0.1):
        with self._cond:
            if not self._cond.wait_for(lambda: self._output is not None or not self._running, timeout):
                return None, None
            if self._output is None:
                return None, None
            frame, t, self.read_detections, self.rear_ms = self._output
            self._output = None
            return frame, t

    def stale_for(self):
        return time.monotonic() - self.last_ok

    def toggle_pause(self):
        pass

    def release(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()
        for th in self.threads:
            th.join(timeout=1)
        self.rear.release()
        self.front.release()
        self.engine.close()


class DualReplaySource:
    """Merge two clips by capture time; no independent video clocks or frame skipping.

Frame zero of each trimmed clip is the same physical instant. Replay stops at the
shorter clip. Real-time playback slows down if processing cannot keep up; decisions
always use media time, including pauses and loops, for reproducible results.
"""
    is_live = False
    frames_are_mirrored = True

    def __init__(self, rear, front, detector, engine, realtime=True, loop=False, timestamps=None):
        self.paths = rear, front
        self.detector, self.engine = detector, engine
        self.realtime, self.loop = realtime, loop
        self.paused = self.ended = False
        self.offset = 0.0
        self.last_t = 0.0
        self.wall_start = None
        self.pause_t = None
        self.rear_ms = 0.0
        self.read_detections = []
        self.timestamps = None
        if timestamps:
            with open(timestamps, encoding="utf-8") as f:
                times = json.load(f)
            if times.get("version") != 1:
                raise ValueError("unsupported replay timestamps version")
            self.timestamps = [times[side] for side in ("rear", "front")]
            for values in self.timestamps:
                if (not values or not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values)
                        or any(b <= a for a, b in zip(values, values[1:]))):
                    raise ValueError("replay timestamps must be finite, nonnegative, and strictly increasing")
        self._open()

    def _open(self):
        self.sources = []
        try:
            for path in self.paths:
                self.sources.append(VideoSource(path, realtime=False))
        except Exception:
            for source in self.sources:
                source.release()
            raise
        if self.timestamps:
            for s, ts in zip(self.sources, self.timestamps):
                if int(s.cap.get(cv2.CAP_PROP_FRAME_COUNT)) != len(ts):
                    for source in self.sources:
                        source.release()
                    raise ValueError("timestamp count does not match paired recording")
        self.pending = [self._next(i) for i in range(2)]

    def _next(self, side):
        frame, t = self.sources[side].read()
        if frame is not None and self.timestamps:
            t = self.timestamps[side][self.sources[side].idx]
        return frame, t

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.pause_t = time.monotonic()
            self.engine.reset()
            self.engine.unavailable(self.last_t, "REPLAY PAUSED")
        elif self.wall_start is not None:
            self.wall_start += time.monotonic() - self.pause_t

    def read(self, timeout=0.1):
        if self.paused or self.ended:
            time.sleep(min(timeout, 0.03))
            return None, None
        if any(frame is None for frame, _ in self.pending):
            self.engine.unavailable(self.last_t + 1.0, "REPLAY ENDED")
            if self.loop:
                self.offset = self.last_t + 1.0
                for source in self.sources:
                    source.release()
                self.engine.reset()
                self._open()
                self.wall_start = None
            else:
                self.ended = True
            return None, None
        # Process only one event per read so UI/heartbeat work is never starved.
        side = 1 if self.pending[1][1] <= self.pending[0][1] else 0
        frame, media_t = self.pending[side]
        t = self.offset + media_t
        if self.wall_start is None:
            self.wall_start = time.monotonic() - media_t
        wait = self.wall_start + media_t - time.monotonic()
        if self.realtime and wait > 0:
            time.sleep(min(wait, timeout))
            return None, None
        self.last_t = t
        self.pending[side] = self._next(side)
        if side == 1:
            meas = self.engine.measure(frame)
            self.engine.on_detections(self.detector.detect(frame, class_map=self.engine.front_classes), t, meas, frame)
            self.engine.on_frame(frame, t, meas)
            return None, None
        frame = cv2.flip(frame, 1) if cfg.MIRROR_VIEW else frame
        self.read_detections = self.detector.detect(frame)
        self.rear_ms = self.detector.last_ms
        return frame, t

    def stale_for(self):
        return 0.0

    def release(self):
        for source in self.sources:
            source.release()
        self.engine.close()


def enable_forward(args, rear_source, detector, hud):
    if args.collision_calibration:
        engine = ForwardEngine(Calibration.load(args.collision_calibration), hud.update_collision, args.collision_log)
    else:
        engine = MonoFrontEngine(hud.update_collision, args.collision_log)
    if args.sim:
        rear_source.front_engine = engine                # the simulator scripts the front camera too
        return rear_source, detector, engine
    if args.front_video:
        rear_source.release()
        try:
            source = DualReplaySource(args.video, args.front_video, detector, engine,
                                      realtime=not args.no_realtime, loop=args.loop, timestamps=args.replay_timestamps)
        except Exception:
            engine.close()
            raise
    else:
        front = RecoveringCamera(args.front_camera, cfg.FRONT_CAMERA_ROTATE_180)
        source = DualLiveSource(rear_source, front, detector, engine)
    return source, RearDetectorAdapter(source, detector), engine
