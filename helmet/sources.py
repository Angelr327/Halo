"""Frame sources. Both return (frame, t) where t is the capture time in seconds.

LiveCamera   - USB webcam. Background thread keeps ONLY the newest frame, so a slow
               detector never processes stale frames (OpenCV otherwise queues them).
PiCamera     - Raspberry Pi camera module (Picamera2), same newest-frame behaviour.
Both can be mounted upside down (rotate_180): the Pi camera flips in hardware (libcamera
transform, no CPU), a USB camera with cv2.rotate. Frames come out the right way up.
open_camera  - picks one according to cfg.CAMERA_SOURCE.
VideoSource  - recorded footage. t is VIDEO time, so TTC math is right even if the
               laptop is slower than real time. realtime=True skips frames to keep pace.
"""
import sys
import threading
import time

import cv2

from . import config as cfg


class LiveCamera:
    is_live = True

    def __init__(self, index=None, rotate_180=None):
        self.index = cfg.CAMERA_INDEX if index is None else index
        self.rotate_180 = cfg.CAMERA_ROTATE_180 if rotate_180 is None else rotate_180
        self._sw_rotate = self.rotate_180          # PiCamera turns this off: it rotates in hardware
        self._cond = threading.Condition()
        self._frame, self._t, self._seq = None, 0.0, 0
        self._last_read_seq = 0
        self.last_ok = time.monotonic()
        self.ended = False
        self._running = True
        self.cap = None
        self._open()
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self):
        backend = (cv2.CAP_DSHOW if sys.platform.startswith("win")
                   else cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.index, backend)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.CAPTURE_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, cfg.CAPTURE_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cfg.DISABLE_AUTOFOCUS:           # silently ignored where unsupported
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            cap.set(cv2.CAP_PROP_FOCUS, 0)  # focus at infinity
        self.cap = cap

    def _grab(self):
        return self.cap.read() if self.cap is not None else (False, None)

    def _reopen(self):
        try:
            self.cap.release()
        except Exception:
            pass
        time.sleep(1.0)
        self._open()

    def _loop(self):
        fails = 0
        while self._running:
            try:
                ok, frame = self._grab()
            except Exception:
                ok, frame = False, None
            if ok and frame is not None:
                if self._sw_rotate:
                    frame = cv2.rotate(frame, cv2.ROTATE_180)
                fails = 0
                with self._cond:
                    self._frame, self._t = frame, time.monotonic()
                    self._seq += 1
                    self.last_ok = self._t
                    self._cond.notify_all()
            else:
                fails += 1
                time.sleep(0.02)
                if fails == 50:                 # ~1 s of failures: try to reopen (camera unplugged)
                    try:
                        self._reopen()
                    except Exception:
                        pass  # absent camera must not kill its reconnect worker
                    fails = 0

    def read(self, timeout=0.1):
        """Newest frame not yet returned, or (None, None) if none arrived within timeout."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != self._last_read_seq, timeout):
                return None, None
            self._last_read_seq = self._seq
            return self._frame, self._t

    def stale_for(self):
        return time.monotonic() - self.last_ok

    def toggle_pause(self):
        pass

    def release(self):
        self._running = False
        time.sleep(0.05)
        if self.cap is not None:
            self.cap.release()


class VideoSource:
    is_live = False

    def __init__(self, path, realtime=True, loop=False):
        self.path, self.realtime, self.loop = path, realtime, loop
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.idx = -1
        self.t0_wall = None
        self.paused = False
        self.pause_started = None
        self.ended = False
        self.loop_offset = 0.0      # keeps video time monotonic across loops

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.pause_started = time.monotonic()
        elif self.t0_wall is not None and self.pause_started is not None:
            self.t0_wall += time.monotonic() - self.pause_started

    def read(self, timeout=0.1):
        if self.paused or self.ended:
            time.sleep(0.03)
            return None, None
        now = time.monotonic()
        if self.t0_wall is None:
            self.t0_wall = now
        if self.realtime:
            target_idx = int((now - self.t0_wall) * self.fps)
            if target_idx <= self.idx:              # ahead of schedule: wait for the next frame time
                time.sleep(max(0.0, (self.idx + 1) / self.fps - (now - self.t0_wall)))
                target_idx = self.idx + 1
            while self.idx < target_idx - 1:        # behind schedule: skip cheaply
                if not self.cap.grab():
                    return self._end()
                self.idx += 1
        ok, frame = self.cap.read()
        if not ok:
            return self._end()
        self.idx += 1
        return frame, self.loop_offset + self.idx / self.fps

    def _end(self):
        if self.loop:
            self.loop_offset += (self.idx + 1) / self.fps + 1.0   # 1 s gap so tracks go stale
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.idx = -1
            self.t0_wall = time.monotonic()
            return None, None
        self.ended = True
        return None, None

    def stale_for(self):
        return 0.0          # recorded footage never triggers the camera-fault path

    def release(self):
        self.cap.release()


class PiCamera(LiveCamera):
    """Camera Module 3 (the Wide version's ~120 deg view suits blind spots) via Picamera2.
    Install with apt (python3-picamera2) and create the venv with --system-site-packages."""

    def _open(self):
        from picamera2 import Picamera2
        self.cam = Picamera2(self.index)
        extra = {}
        if self.rotate_180:
            try:
                from libcamera import Transform
                extra["transform"] = Transform(hflip=1, vflip=1)   # 180 deg, done by the camera: free
                self._sw_rotate = False
            except Exception:
                self._sw_rotate = True                            # fall back to cv2.rotate
        conf = self.cam.create_video_configuration(
            main={"size": (cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT), "format": "RGB888"},  # RGB888 = BGR bytes = OpenCV order
            buffer_count=2,
            controls={"FrameRate": float(cfg.CAPTURE_FPS)}, **extra)
        self.cam.configure(conf)
        self.cam.start()
        if cfg.DISABLE_AUTOFOCUS:
            try:
                from libcamera import controls
                self.cam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": 0.0})  # infinity
            except Exception:
                pass                                   # fixed-focus modules have no AF controls

    def _grab(self):
        try:
            return True, self.cam.capture_array("main")
        except Exception:
            return False, None

    def _reopen(self):
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:
            pass
        time.sleep(1.0)
        self._open()

    def release(self):
        self._running = False
        time.sleep(0.05)
        try:
            self.cam.stop()
        except Exception:
            pass
        finally:
            self.cam.close()  # stop alone keeps the camera acquired


def _pi_camera_present():
    try:
        from picamera2 import Picamera2
        return len(Picamera2.global_camera_info()) > 0
    except Exception:
        return False


def open_camera(index=None, rotate_180=None):
    """The rear camera by default; pass index/rotate_180 for another (e.g. the front camera)."""
    src = cfg.CAMERA_SOURCE
    if src == "picamera2" or (src == "auto" and _pi_camera_present()):
        return PiCamera(index, rotate_180)
    return LiveCamera(index, rotate_180)


class RecoveringCamera:
    """Lazy camera startup for dual mode: a missing CSI port cannot stop its peer.

    read() is called from that camera's dedicated worker, so driver startup and
    reconnect delays never block the other camera or the display.
    """
    is_live = True
    ended = False

    def __init__(self, index=None, rotate_180=None):
        self.index, self.rotate_180 = index, rotate_180
        self.source = None
        self.last_ok = time.monotonic()
        self.retry_at = 0.0
        self.closed = False

    def read(self, timeout=0.1):
        if self.closed:
            return None, None
        if self.source is None and time.monotonic() >= self.retry_at:
            try:
                opened = open_camera(self.index, self.rotate_180)
                if self.closed:
                    opened.release()
                    return None, None
                self.source = opened
            except Exception:
                self.retry_at = time.monotonic() + 1.0
        if self.source is None:
            time.sleep(min(timeout, 0.1))
            return None, None
        frame, t = self.source.read(timeout)
        if frame is not None:
            self.last_ok = time.monotonic()
        return frame, t

    def stale_for(self):
        return time.monotonic() - self.last_ok

    def toggle_pause(self):
        pass

    def release(self):
        self.closed = True
        if self.source:
            self.source.release()


class CameraPreview:
    """Optional front capture for the web view; only open it while someone watches.

    Collision mode shares its existing capture instead. This worker never runs
    detection and a missing/busy camera cannot block the rear safety loop.
    """
    def __init__(self, index, rotate_180, wanted, publish):
        self.index, self.rotate_180 = index, rotate_180
        self.wanted, self.publish = wanted, publish
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        source = None
        try:
            while not self._stop.is_set():
                if not self.wanted():
                    if source is not None:
                        source.release()
                        source = None
                        self.publish(None)
                    self._stop.wait(0.1)
                    continue
                if source is None:
                    source = RecoveringCamera(self.index, self.rotate_180)
                frame, _ = source.read(timeout=0.1)
                if frame is not None:
                    self.publish(frame)
        finally:
            if source is not None:
                source.release()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
