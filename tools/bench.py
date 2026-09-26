"""Hardware bring-up tools (use these before the event to learn the hardware).

  python -m tools.bench serial [--port COM5]     type commands, feel the motors, watch the LED
  python -m tools.bench camera                    check orientation: is rider-left on the left?
  python -m tools.bench yolo [--models yolo26n.pt yolov8n.pt] [--sizes 256 320 416] [--ncnn]
  python -m tools.bench yolo --video clip.mp4     benchmark without any camera attached
"""
import argparse
import threading
import time

import cv2

from helmet import config as cfg


def serial_cmd(args):
    from helmet.outputs import HelmetLink
    link = HelmetLink(args.port)
    print(link.status)
    if link.ser is None:
        print("No Arduino connected. Check the USB cable (must be a DATA cable) and the CH340 driver.")
        return
    state = {"light": 0, "hb": True, "run": True}

    def heartbeat():
        while state["run"]:
            if state["hb"]:
                link.send(f"M{state['light']}")
            for line in list(link.rx_log):
                print(f"   <- {line}")
            link.rx_log.clear()
            link.poll()
            time.sleep(cfg.HEARTBEAT_S)
    threading.Thread(target=heartbeat, daemon=True).start()
    print("Commands: l / r / b = strong buzz left/right/both, 1 2 3 4 = pattern on both,\n"
          "          0 1 2 prefixed with m (m0 m1 m2) = light mode, f = fault buzz,\n"
          "          s = stop heartbeat (failsafe should trigger in 1.5 s), h = resume, q = quit,\n"
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
    a = ap.parse_args()
    {"serial": serial_cmd, "camera": camera_cmd, "yolo": yolo_cmd}[a.cmd](a)
