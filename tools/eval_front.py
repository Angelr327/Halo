"""Evidence table for the camera-only front warning (simulated, no hardware needed).

    python -m tools.eval_front             # 20 runs per case, under a minute

Scripted approaches with YOLO-like box jitter, fed through the real MonoFrontEngine at the
rates the front camera gets on a Pi (it shares YOLO with the rear camera). "On time" means
BRAKE fired before the marker mode's physical stopping distance, collision.stopping_distance:
1 s reaction + 0.25 s delay + braking at 1.5 m/s^2 + 0.75 m margin.
These are simulation results, not road measurements.
"""
import argparse
import math

import numpy as np

from helmet.collision import Settings, stopping_distance
from helmet.front_mono import MonoFrontEngine, MonoSettings
from helmet.perception import Detection
from helmet.sim import FrontScenario

BLANK = np.zeros((480, 640, 3), np.uint8)

# (detections per second, closing speed m/s, start distance m)
TIMING = [(7.5, 1.0, 10), (7.5, 1.4, 10), (7.5, 2.0, 10), (7.5, 2.6, 10),
          (5.0, 1.0, 10), (5.0, 1.4, 10), (5.0, 2.0, 10), (5.0, 2.6, 10), (5.0, 2.6, 14)]
# name: (label, lateral m, start m, closing speed m/s, stops at m)
QUIET = {"standing 3 m from a chair": ("chair", 0.1, 3.0, 0.0, 3.0),
         "person standing 4 m ahead": ("person", 0.0, 4.0, 0.0, 4.0),
         "walking past a chair 1.5 m right": ("chair", 1.5, 9.0, 1.4, -2.0),
         "walking past a chair 1.0 m left": ("chair", -1.0, 9.0, 1.4, -2.0),
         "backing away from a chair": ("chair", 0.0, 1.5, -1.0, 99.0)}


def approach(label, x, z0, speed, hz=7.5, seed=0, stop_at=0.5, seconds=None, sway_px=0.0, settings=None):
    """One scripted approach through a fresh engine -> (gap to the bike's front at first BRAKE, or None; results).
    sway_px shifts every box sideways like a rider glancing around (+-60 px is about +-5 deg)."""
    scene, engine = FrontScenario(seed), MonoFrontEngine(lambda r: None, settings=settings)
    bike_front = engine.settings.bike_front_m
    seconds = seconds if seconds is not None else (z0 - stop_at) / max(speed, 1e-9) + 1.5
    first, rows, t = None, [], 0.0
    while t < seconds:
        z = max(stop_at, z0 - speed * t) if speed >= 0 else min(stop_at, z0 - speed * t)
        dx = sway_px * math.sin(2 * math.pi * 0.7 * t)
        dets = [Detection(d.x1 + dx, d.y1, d.x2 + dx, d.y2, d.conf, d.label) for d in scene.boxes([(label, x, z)])]
        engine.on_detections(dets, t, None, BLANK, age_s=0.1)
        rows.append(engine.snapshot())
        if rows[-1]["state"] == "BRAKE" and first is None:
            first = z - bike_front
        t += 1 / hz
    return first, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=20, help="random seeds per case")
    ap.add_argument("--brake-ttc", type=float, default=MonoSettings().brake_ttc_s)
    args = ap.parse_args()
    settings = MonoSettings(brake_ttc_s=args.brake_ttc)
    print(f"BRAKE at {settings.brake_ttc_s:.1f} s to contact, {args.runs} runs per case (simulated)\n")
    print("Walking/jogging straight at a chair: does BRAKE come before the stopping distance?")
    print(f"{'rate':>7} {'speed':>9} {'start':>6} {'stop dist':>10} {'on time':>9} {'gap at BRAKE (min / median)':>30}")
    for hz, v, z0 in TIMING:
        need = stopping_distance(v, Settings())
        gaps = [approach("chair", 0.0, z0, v, hz, seed, settings=settings)[0] for seed in range(args.runs)]
        ok = sum(g is not None and g >= need for g in gaps)
        got = [g for g in gaps if g is not None]
        spread = f"{min(got):.2f} / {np.median(got):.2f} m" if got else "never fired"
        print(f"{hz:>5.1f}/s {v:>5.1f} m/s {z0:>4} m {need:>8.2f} m {ok:>4}/{args.runs:<4} {spread:>30}")
    print("\nThings that must NOT brake (rider glancing around, +-30 px):")
    for name, (label, x, z0, v, stop) in QUIET.items():
        bad = sum("BRAKE" in {r["state"] for r in approach(label, x, z0, v, seed=seed, stop_at=stop, seconds=7.0,
                                                               sway_px=30, settings=settings)[1]}
                  for seed in range(args.runs))
        print(f"  {name:<36} false BRAKE in {bad}/{args.runs} runs")


if __name__ == "__main__":
    main()
