"""Create measured front calibration; nothing is guessed from rear camera settings.

Run python -m tools.calibrate_front --help. OpenCV >= 4.9 includes ArUco.
"""
import argparse
import glob
import json
from pathlib import Path
import time

import cv2
import numpy as np

from helmet import config as cfg
from helmet.collision import Calibration, MarkerRanger
from helmet.sources import open_camera


def marker(args):
    if not 0 <= args.id < 50:
        raise ValueError("ID must be between 0 and 49")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    image = cv2.aruco.generateImageMarker(dictionary, args.id, 800)
    image = cv2.copyMakeBorder(image, 80, 80, 80, 80, cv2.BORDER_CONSTANT, value=255)
    if not cv2.imwrite(args.output, image):
        raise ValueError("could not write marker")
    print(f"Saved {args.output}. Print flat; measure the OUTER BLACK SQUARE, excluding white margin.")
    print("Default demo size is 0.20 m. A larger measured marker may be necessary for your lens/range.")


def capture(args):
    cfg.CAMERA_SOURCE = args.source
    source = open_camera(args.camera, cfg.FRONT_CAMERA_ROTATE_180)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    print("Capture starts in 3 s. For intrinsics, move/tilt a checkerboard across the frame.")
    print("For alignment, stay still in the normal helmet posture, looking straight along the approach lane.")
    count, start, last = 0, time.monotonic(), -1e9
    try:
        while count < args.count:
            frame, t = source.read(timeout=0.1)
            if source.stale_for() > 3:
                raise ValueError("camera unavailable")
            if frame is None or time.monotonic() - start < 3 or t - last < args.interval:
                continue
            path = out / f"front_{time.time_ns()}.png"
            if not cv2.imwrite(str(path), frame):
                raise ValueError(f"could not write {path}")
            print(path)
            count, last = count + 1, t
    finally:
        source.release()


def intrinsics(args):
    if args.square_m <= 0 or min(args.columns, args.rows) < 3:
        raise ValueError("positive square length and at least 3x3 inner corners required")
    paths = sorted(glob.glob(args.images))
    obj = np.zeros((args.rows * args.columns, 3), np.float32)
    obj[:, :2] = np.mgrid[0:args.columns, 0:args.rows].T.reshape(-1, 2) * args.square_m
    objects, pixels, size = [], [], None
    for path in paths:
        gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        image_size = gray.shape[1::-1]
        if size is not None and size != image_size:
            raise ValueError("all calibration images must use the same resolution")
        size = image_size
        found, corners = cv2.findChessboardCornersSB(gray, (args.columns, args.rows))
        if found:
            objects.append(obj.copy())
            pixels.append(corners.astype(np.float32))
    if len(objects) < 12:
        raise ValueError(f"only {len(objects)} checkerboard views found; need at least 12 diverse views")
    rms, k, distortion, _, _ = cv2.calibrateCamera(objects, pixels, size, None, None)
    if not np.isfinite(rms) or rms > 1.5:
        raise ValueError(f"calibration RMS {rms:.2f}px exceeds 1.5px; recapture sharper, varied views")
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(dict(version=1, image_size=size, camera_matrix=k.tolist(),
                       dist_coeffs=distortion.reshape(-1).tolist(), rms_px=rms, views=len(objects)), f, indent=2)
        f.write("\n")
    print(f"Saved {args.output}: {len(objects)} views, RMS {rms:.3f}px. Verify against measured distances.")


def align(args):
    with open(args.intrinsics, encoding="utf-8") as f:
        d = json.load(f)
    cal = Calibration(tuple(d["image_size"]), np.asarray(d["camera_matrix"], dtype=float),
                      np.asarray(d["dist_coeffs"], dtype=float), args.marker_m, args.id, np.eye(3),
                      args.chair_width_m, args.marker_to_front_m, args.camera_to_bike_front_m)
    frame = cv2.imread(args.image)
    if frame is None:
        raise ValueError("cannot read alignment image")
    raw, reason = MarkerRanger(cal).raw_pose(frame)
    if raw is None:
        raise ValueError(f"alignment failed: {reason}")
    rotation, translation, _, error = raw
    cal.reference_rotation = rotation
    cal.save(args.output)
    print(f"Saved {args.output}: marker camera depth {translation[2]:.2f}m, fit error {error:.2f}px.")
    print("Reference image must have been taken while looking straight down the marked approach lane.")
    print("Re-align if the marker or helmet camera moves. Intrinsics are specific to lens/focus/resolution.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("marker", help="generate printable DICT_4X4_50 marker")
    p.add_argument("--id", type=int, default=0)
    p.add_argument("--output", default="front_marker.png")
    p.set_defaults(run=marker)
    p = sub.add_parser("capture", help="save upright front-camera calibration/alignment images")
    p.add_argument("--camera", type=int, default=cfg.FRONT_CAMERA_INDEX)
    p.add_argument("--source", choices=["auto", "usb", "picamera2"], default=cfg.CAMERA_SOURCE)
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--output", required=True)
    p.set_defaults(run=capture)
    p = sub.add_parser("intrinsics", help="calibrate from checkerboard images")
    p.add_argument("--images", required=True, help="quoted glob of images")
    p.add_argument("--columns", type=int, default=9, help="inner corners horizontally")
    p.add_argument("--rows", type=int, default=6, help="inner corners vertically")
    p.add_argument("--square-m", type=float, required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(run=intrinsics)
    p = sub.add_parser("align", help="save lane reference orientation and measured target/bike geometry")
    p.add_argument("--intrinsics", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--id", type=int, default=0)
    p.add_argument("--marker-m", type=float, default=0.20)
    p.add_argument("--chair-width-m", type=float, required=True)
    p.add_argument("--marker-to-front-m", type=float, required=True)
    p.add_argument("--camera-to-bike-front-m", type=float, required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(run=align)
    args = ap.parse_args()
    try:
        args.run(args)
    except (ValueError, OSError, KeyError, cv2.error) as e:
        ap.exit(1, f"Calibration error: {e}\n")


if __name__ == "__main__":
    main()
