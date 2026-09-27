"""Hardware bring-up tools (use these before the event to learn the hardware).

  python -m tools.bench serial [--port COM5]     type commands, feel the motors, watch the LED
  python -m tools.bench camera                    check orientation: is rider-left on the left?
  python -m tools.bench yolo [--models yolo26n.pt yolov8n.pt] [--sizes 256 320 416] [--ncnn]
  python -m tools.bench yolo --video clip.mp4     benchmark without any camera attached
  python -m tools.bench check [--seconds 10]      every camera + the ultrasonic sensor(s), all at once
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

from helmet import config as cfg


def serial_cmd(args):
    from helmet.outputs import HelmetLink
    link = HelmetLink(args.port)
    print(link.status)
    if link.ser is None:
        print("No Arduino connected. Check the USB cable (must be a DATA cable) and the CH340 driver.")
        return
    state = {"light": 0, "hb": True, "run": True, "sonar": False}

    def heartbeat():
        while state["run"]:
            if state["hb"]:
                link.send(f"M{state['light']}")
            for line in list(link.rx_log):
                print(f"   <- {line}")
            link.rx_log.clear()
            link.poll()
            if state["sonar"] and link.sonar_fresh():
                print("   sonar " + "  ".join(f"{k} {'---' if v is None else f'{v * 100:3.0f}cm'}"
                                           for k, v in link.sonar.items()))
            time.sleep(cfg.HEARTBEAT_S)
    threading.Thread(target=heartbeat, daemon=True).start()
    print("Commands: l / r / b = strong buzz left/right/both, 1 2 3 4 = pattern on both,\n"
          "          0 1 2 prefixed with m (m0 m1 m2) = light mode, f = fault buzz,\n"
          "          s = stop heartbeat (failsafe should trigger in 1.5 s), h = resume, q = quit,\n"
          "          u = print ultrasonic distances on/off, z0 / z1 = mute / unmute buzzers,\n"
          "          anything else is sent raw (e.g. L2, R1, ?)")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            state["run"] = False
            link.close()
            return
        elif cmd in ("l", "r", "b"):
            link.send(f"{cmd.upper()}3")
        elif cmd in ("1", "2", "3", "4"):
            link.send(f"B{cmd}")
        elif cmd in ("m0", "m1", "m2"):
            state["light"] = int(cmd[1])
        elif cmd == "f":
            link.send("F1")
            link.send("F0")
        elif cmd == "s":
            state["hb"] = False
            print("heartbeat stopped: expect FAILSAFE + long buzz, light back to normal flash")
        elif cmd == "h":
            state["hb"] = True
        elif cmd == "u":
            state["sonar"] = not state["sonar"]
        elif cmd in ("z0", "z1"):
            link.send(cmd.upper())
        elif cmd:
            link.send(cmd)


def camera_cmd(args):
    """Orientation check. With a desktop: live window. Headless Pi: saves orientation_check.jpg."""
    from helmet.sources import open_camera
    cam = open_camera(args.index)
    print(f"Camera: {type(cam).__name__}. Wear the helmet (or hold it as if worn) and have someone stand\n"
          "behind you on your LEFT. They must appear under 'RIDER LEFT'. If not, flip MIRROR_VIEW.")
    t0, n = time.monotonic(), 0
    try:
        while True:
            frame, _ = cam.read(timeout=2.0)
            if frame is None:
                print("no frames from camera")
                return
            n += 1
            if cfg.MIRROR_VIEW:
                frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            cv2.line(frame, (w // 2, 0), (w // 2, h), (255, 255, 0), 1)
            cv2.putText(frame, "RIDER LEFT", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(frame, "RIDER RIGHT", (w - 180, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            fps = n / max(1e-3, time.monotonic() - t0)
            cv2.putText(frame, f"{w}x{h}  {fps:.0f} fps  mirror={cfg.MIRROR_VIEW}", (10, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            if cfg.HEADLESS:
                if time.monotonic() - t0 > 3.0:
                    cv2.imwrite("orientation_check.jpg", frame)
                    print(f"Saved orientation_check.jpg ({w}x{h}, {fps:.0f} fps). Copy it off the Pi or open it on the desktop.")
                    return
                continue
            cv2.imshow("camera check", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cam.release()
        if not cfg.HEADLESS:
            cv2.destroyAllWindows()


# ------------------------------------------------------------------ hardware check
# /dev/video* nodes the Pi itself creates (camera pipeline, codecs): not USB cameras
_PI_INTERNAL = ("rp1-cfe", "pispbe", "rpi-hevc", "hevc", "bcm2835", "rpivid", "unicam", "codec", "isp")


def list_usb_cameras(sysfs="/sys/class/video4linux"):
    """[(index, name)] - one capture node per physical USB camera (each exposes 2+ nodes)."""
    found, seen = [], set()
    nodes = sorted(glob.glob(os.path.join(sysfs, "video*")), key=lambda p: int(p.rsplit("video", 1)[1]))
    for node in nodes:
        try:
            with open(os.path.join(node, "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if any(k in name.lower() for k in _PI_INTERNAL):
            continue
        dev = os.path.realpath(os.path.join(node, "device"))
        if dev in seen:
            continue
        seen.add(dev)
        found.append((int(node.rsplit("video", 1)[1]), name))
    return found


def list_csi_cameras():
    """[(num, model)] for ribbon-cable Camera Modules, or (None, reason) if picamera2 is unavailable."""
    try:
        from picamera2 import Picamera2
    except Exception as e:
        return None, f"picamera2 not importable ({type(e).__name__}); venv needs --system-site-packages"
    cams = [(i, c.get("Model", "?")) for i, c in enumerate(Picamera2.global_camera_info())
            if "usb" not in str(c.get("Id", "")).lower()]       # libcamera also lists UVC webcams
    return cams, ""


def rpicam_check():
    """Fallback when Python can't import picamera2: test each Camera Module with rpicam-still.
    Returns [(label, ok, detail)]."""
    tool = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    if not tool:
        return []
    try:
        out = subprocess.run([tool, "--list-cameras"], capture_output=True, text=True, timeout=15)
    except Exception as e:
        return [("rpicam", False, f"{type(e).__name__}: {e}")]
    cams = re.findall(r"^\s*(\d+)\s*:\s*(\S+)", out.stdout + out.stderr, re.M)
    results = []
    for num, model in cams:
        fn = f"check_csi{num}.jpg"
        try:
            r = subprocess.run([tool, "-n", "--camera", num, "-t", "1000", "--width", "640", "--height", "480",
                                "-o", fn], capture_output=True, text=True, timeout=20)
            ok = r.returncode == 0 and os.path.exists(fn) and os.path.getsize(fn) > 0
            detail = f"snapshot -> {fn}" if ok else (r.stderr.strip().splitlines() or ["no output"])[-1][:80]
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append((f"CSI{num} {model}", ok, detail))
    return results


class _Grabber:
    """Reads one camera on its own thread so both cameras run at the same time."""

    def __init__(self, label, read_fn, close_fn):
        self.label, self._read, self._close = label, read_fn, close_fn
        self.frames, self.last, self.error = 0, None, ""
        self.t0 = time.monotonic()
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run:
            try:
                frame = self._read()
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                return
            if frame is None:
                time.sleep(0.01)
                continue
            self.frames += 1
            self.last = frame

    def fps(self):
        return self.frames / max(1e-3, time.monotonic() - self.t0)

    def stop(self):
        self._run = False
        time.sleep(0.1)
        try:
            self._close()
        except Exception:
            pass


def _open_csi(num, model):
    from picamera2 import Picamera2
    role, rot = ("rear", cfg.CAMERA_ROTATE_180) if num == cfg.CAMERA_INDEX else \
        ("front", cfg.FRONT_CAMERA_ROTATE_180) if num == cfg.FRONT_CAMERA_INDEX else ("?", False)
    extra = {}
    if rot:
        from libcamera import Transform
        extra["transform"] = Transform(hflip=1, vflip=1)      # same as the helmet app: snapshots show what it sees
    cam = Picamera2(num)
    cam.configure(cam.create_video_configuration(
        main={"size": (cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT), "format": "RGB888"}, buffer_count=2, **extra))
    cam.start()
    label = f"CSI{num} {role}{' (rotated 180)' if rot else ''} {model}"
    return _Grabber(label, lambda: cam.capture_array("main"), lambda: (cam.stop(), cam.close()))


def _open_usb(index, name):
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2) if os.name != "nt" else cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.CAPTURE_HEIGHT)
    if not cap.isOpened():
        raise RuntimeError(f"/dev/video{index} would not open")
    return _Grabber(f"USB{index} {name[:18]}", lambda: cap.read()[1], cap.release)


def _tile(g, h=240):
    frame = g.last if g.last is not None else np.zeros((h, h * 4 // 3, 3), np.uint8)
    img = cv2.resize(frame, (int(frame.shape[1] * h / frame.shape[0]), h))
    status = f"{g.label}  {g.fps():.0f} fps" if g.frames else f"{g.label}  NO FRAMES {g.error}"[:60]
    cv2.rectangle(img, (0, 0), (img.shape[1], 22), (0, 0, 0), -1)
    cv2.putText(img, status, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0) if g.frames else (0, 0, 255), 1)
    return img


def check_cmd(args):
    from helmet.outputs import HelmetLink
    print("== cameras")
    grabbers = []
    csi, why = list_csi_cameras()
    rpicam = []
    if csi is None:
        print(f"   Camera Modules: {why}")
        print("   testing them with rpicam-still instead (one at a time, no live FPS)...")
        rpicam = rpicam_check()
        for label, good, detail in rpicam:
            print(f"   {'PASS' if good else 'FAIL'} {label}: {detail}")
        if rpicam:
            print("   NOTE: the helmet app itself needs picamera2 in Python for these cameras:\n"
                  "         python3 -m venv --system-site-packages .venv && . .venv/bin/activate "
                  "&& pip install -r requirements.txt")
        csi = []
    usb = list_usb_cameras() if os.path.isdir("/sys/class/video4linux") else [(i, "camera") for i in range(2)]
    print(f"   live capture: {len(csi)} Camera Module(s) {[m for _, m in csi]}, {len(usb)} USB camera(s) {[n for _, n in usb]}")
    for opener, cams in ((_open_csi, csi), (_open_usb, usb)):
        for a, b in cams:
            try:
                grabbers.append(opener(a, b))
            except Exception as e:
                print(f"   {b}: failed to open: {type(e).__name__}: {e}")

    print("== ultrasonic (Arduino)")
    link = HelmetLink(args.port)
    print(f"   {link.status}")
    readings = {k: [] for k in link.sonar}
    got_any, last_t = False, link.sonar_t

    print(f"== running {args.seconds:.0f} s: wave a hand in front of the sensor, move in front of each camera")
    t_end, t_print = time.monotonic() + args.seconds, 0.0
    try:
        while time.monotonic() < t_end:
            link.tick(0)                                  # heartbeat, so the Arduino's watchdog stays quiet
            if link.sonar_t != last_t:
                last_t, got_any = link.sonar_t, True
                for k, v in link.sonar.items():
                    readings[k].append(v)
            now = time.monotonic()
            if now - t_print >= 0.5:
                t_print = now
                cams = " | ".join(f"{g.label} {g.fps():4.1f} fps" if g.frames else
                                  f"{g.label} {'NO FRAMES' if g.error or now - g.t0 > 3 else 'starting'}"
                                  for g in grabbers) or "no cameras"
                son = "  ".join(f"{k} {'---' if v is None else f'{v * 100:3.0f}cm'}" for k, v in link.sonar.items()) \
                    if link.sonar_fresh() else "no sonar data"
                print(f"   {cams} || {son}")
            if grabbers and not cfg.HEADLESS:
                cv2.imshow("hardware check (q to stop)", np.hstack([_tile(g) for g in grabbers]))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    print("== results")
    ok = True
    for g in grabbers:
        good = g.frames > 0
        ok &= good
        if good:
            fn = f"check_{g.label.split()[0].lower()}.jpg"
            cv2.imwrite(fn, g.last)
            print(f"   PASS {g.label}: {g.frames} frames, {g.fps():.1f} fps, {g.last.shape[1]}x{g.last.shape[0]} -> {fn}")
        else:
            print(f"   FAIL {g.label}: no frames {g.error}")
    if grabbers:
        cv2.imwrite("check_cameras.jpg", np.hstack([_tile(g) for g in grabbers]))
        print("   side-by-side: check_cameras.jpg")
    ok &= all(good for _, good, _ in rpicam)
    n_cams = len(grabbers) + sum(1 for _, good, _ in rpicam if good)
    if n_cams < args.expect_cameras:
        ok = False
        print(f"   FAIL expected {args.expect_cameras} cameras, {n_cams} working")
    if link.ser is None:
        ok = False
        print("   FAIL no Arduino: check the USB cable (data, not charge-only) and that nothing else has the port open")
    elif not got_any:
        ok = False
        seen = list(link.rx_log)
        print("   FAIL Arduino connected but sent no ultrasonic data. It sent: "
              + (" | ".join(seen)[:120] if seen else "nothing"))
        if any("distance" in x or "HC-SR04" in x for x in seen):
            print("        -> that's the HC-SR04 test sketch: upload firmware/helmet_arduino/helmet_arduino.ino")
        elif any(x.startswith(("READY", "LINK", "FAILSAFE", "STATUS")) for x in seen):
            print("        -> helmet firmware without ultrasonic support: upload the latest helmet_arduino.ino")
        elif seen:
            print("        -> unreadable text = a different sketch or baud rate: upload helmet_arduino.ino (57600 baud)")
        else:
            print("        -> silent: upload helmet_arduino.ino, and close the Arduino IDE's Serial Monitor")
    else:
        for k, vals in readings.items():
            hits = [v for v in vals if v is not None]
            if hits:
                print(f"   PASS sonar {k}: {len(hits)}/{len(vals)} echoes, {min(hits) * 100:.0f}-{max(hits) * 100:.0f} cm")
            elif k == "SL":
                ok = False
                print(f"   FAIL sonar SL: {len(vals)} pings, no echo. Trig=D7, Echo=D8, VCC=5V? Point it at something 0.2-2 m away")
            else:
                print(f"   --   sonar {k}: no echo (not connected?)")
    print("ALL HARDWARE OK" if ok else "SOME CHECKS FAILED (see above)")
    for g in grabbers:
        g.stop()
    link.close()
    if grabbers and not cfg.HEADLESS:
        cv2.destroyAllWindows()


def yolo_cmd(args):
    from ultralytics import YOLO
    cap = cv2.VideoCapture(args.video if args.video else args.index)
    frames = []
    while len(frames) < 30:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.resize(f, (cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT)))
    cap.release()
    if not frames:
        print("no frames")
        return
    for m in args.models:
        for size in args.sizes:
            path = m
            if args.ncnn and m.endswith(".pt"):
                cfg.MODEL_PATH, cfg.IMGSZ, cfg.AUTO_EXPORT_NCNN = m, size, True
                from helmet.perception import resolve_model_path
                path = resolve_model_path()
            model = YOLO(path, task="detect")
            model.predict(frames[0], imgsz=size, device=cfg.DEVICE, verbose=False)
            t0 = time.perf_counter()
            for f in frames:
                model.predict(f, imgsz=size, device=cfg.DEVICE, verbose=False,
                              classes=list(cfg.VEHICLE_CLASSES))
            ms = (time.perf_counter() - t0) / len(frames) * 1000
            print(f"{path:30s} imgsz={size:4d}: {ms:6.1f} ms/frame  (~{1000 / ms:4.1f} FPS detector-only)")
    print("Aim for >= 10 FPS end to end. Pick the largest imgsz that keeps you there.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serial")
    s.add_argument("--port")
    c = sub.add_parser("camera")
    c.add_argument("--index", type=int, default=cfg.CAMERA_INDEX)
    y = sub.add_parser("yolo")
    y.add_argument("--models", nargs="+", default=["yolo26n.pt", "yolov8n.pt"])
    y.add_argument("--sizes", nargs="+", type=int, default=[256, 320, 416] if cfg.IS_PI else [320, 416, 480, 640])
    y.add_argument("--ncnn", action="store_true", default=cfg.IS_PI, help="benchmark NCNN exports (default on a Pi)")
    y.add_argument("--index", type=int, default=cfg.CAMERA_INDEX)
    y.add_argument("--video")
    k = sub.add_parser("check")
    k.add_argument("--seconds", type=float, default=10.0)
    k.add_argument("--port")
    k.add_argument("--expect-cameras", type=int, default=2)
    a = ap.parse_args()
    {"serial": serial_cmd, "camera": camera_cmd, "yolo": yolo_cmd, "check": check_cmd}[a.cmd](a)
