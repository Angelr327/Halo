"""Record both helmet cameras with a shared host-monotonic timestamp sidecar.

No model inference is needed. The raw clips preserve upright camera orientation;
the rear mirror is applied during replay. Stop the live helmet app before recording.
"""
import argparse
import json
from pathlib import Path
import threading
import time

import cv2

from helmet import config as cfg
from helmet.collision import Calibration
from helmet.sources import open_camera


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True, help="new recording directory")
    ap.add_argument("--calibration", help="marker mode only: front alignment JSON to copy into this session")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--rear-camera", type=int, default=cfg.CAMERA_INDEX)
    ap.add_argument("--front-camera", type=int, default=cfg.FRONT_CAMERA_INDEX)
    ap.add_argument("--source", choices=["auto", "usb", "picamera2"], default=cfg.CAMERA_SOURCE)
    args = ap.parse_args()
    if args.seconds <= 0 or args.rear_camera == args.front_camera:
        ap.error("positive duration and different camera indices required")
    cal = Calibration.load(args.calibration) if args.calibration else None
    if cal is not None and cal.image_size != (cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT):
        ap.error("calibration resolution must match capture resolution")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    if cal is not None:
        cal.save(out / "calibration.json")
    cfg.CAMERA_SOURCE = args.source
    sources, workers, errors = [], [], []
    stamps = {"version": 1, "timestamp_basis": "host receipt monotonic seconds from shared start", "rear": [], "front": []}
    stop = threading.Event()
    try:
        sources.append(open_camera(args.rear_camera, cfg.CAMERA_ROTATE_180))
        sources.append(open_camera(args.front_camera, cfg.FRONT_CAMERA_ROTATE_180))
        start = time.monotonic()

        def record(side, source):
            writer = cv2.VideoWriter(str(out / f"{side}.avi"), cv2.VideoWriter_fourcc(*"MJPG"),
                                     cfg.CAPTURE_FPS, (cfg.CAPTURE_WIDTH, cfg.CAPTURE_HEIGHT))
            try:
                if not writer.isOpened():
                    raise RuntimeError(f"cannot open {side} video writer")
                while not stop.is_set() and time.monotonic() - start < args.seconds:
                    frame, t = source.read(timeout=.1)
                    if source.stale_for() > 2:
                        raise RuntimeError(f"{side} camera unavailable")
                    if frame is None or t < start:
                        continue
                    writer.write(frame)
                    stamps[side].append(t - start)
            except Exception as e:
                errors.append(str(e))
                stop.set()
            finally:
                writer.release()

        for side, source in zip(("rear", "front"), sources):
            worker = threading.Thread(target=record, args=(side, source))
            workers.append(worker)
            worker.start()
        print(f"Recording both cameras for {args.seconds:g}s to {out}")
        while any(w.is_alive() for w in workers):
            for worker in workers:
                worker.join(timeout=.1)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=2)
        for source in sources:
            source.release()
        with open(out / "timestamps.json", "w", encoding="utf-8") as f:
            json.dump(stamps, f, indent=2, allow_nan=False)
            f.write("\n")
    if errors or not all(stamps[s] for s in ("rear", "front")):
        ap.exit(1, "Recording incomplete: " + "; ".join(errors or ["no frames"]) + "\n")
    print("Saved raw clips, timestamps.json" + (", and calibration.json" if cal is not None else "")
          + ". Replay using --replay-timestamps.")


if __name__ == "__main__":
    main()
