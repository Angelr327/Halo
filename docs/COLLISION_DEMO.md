# Helmet forward collision demo: marker mode

> **No time to calibrate?** `--collision` without `--collision-calibration` runs the
> camera-only mode instead: same BRAKE warning, no marker or checkerboard. See
> [FRONT_CAMERA_ONLY.md](FRONT_CAMERA_ONLY.md). This page covers the marker mode.

`--collision --collision-calibration FILE` runs both helmet cameras. It detects a **chair**, associates a known-size ArUco marker with it, estimates approach speed to that stationary target, and shows **BRAKE / BRAKE** on the OLED. The web scene deliberately draws that chair as a tree. The detector and telemetry still say `chair`.

This is a calibrated, low-speed hackathon demo. It does not independently measure bike speed, know bike heading during head turns, or detect arbitrary collision hazards. There is no accelerometer, ultrasonic, GPS, cloud, or external speed-sensor dependency. The original rear-only invocation continues to work.

## Prepare the scene

- Use a padded stationary chair with one rigid, flat `DICT_4X4_50` marker, ID 0, horizontally centered on it. Face the marker toward the approach lane and keep the whole marker visible inside the chair's detection box. The marker must not obscure so much of the chair that YOLO stops recognizing it.
- Mark a straight approach lane and a stopping line. Hold a consistent helmet posture, looking ahead. Stop before the chair; do not test impact.
- Start well before the warning boundary so the marker and chair can be acquired and speed can initialize. The reference orientation is camera orientation relative to this fixed marker, not a compass heading.
- The initial maximum speed is **2 m/s (4.5 mph)**. Nominal warning distance at that speed is **4.58 m**, before uncertainty allowances. Lower speed if range, latency, or braking validation fails.
- **Marker size matters:** 20 cm is only a starting point. The ranger rejects markers with any side below 16 pixels. A wide-angle camera may need a 40–60 cm marker or a much slower demonstration. Validate detection comfortably beyond the warning boundary, not just beside the chair. Enter the actual size in calibration.
- Keep focus, resolution, and image orientation fixed. Rear and front cameras retain separate orientation settings; the front image is not mirrored.

## Calibrate once, align for each physical setup

Run these commands on the Pi using the same environment as the app. Stop the app before camera capture. No calibration values are silently borrowed from the rear camera.

Generate and print a marker:

```bash
python -m tools.calibrate_front marker --output front_marker.png
```

Measure the **outer black square**, excluding the white margin. Print without stretching and mount it flat.

For camera intrinsics, photograph a checkerboard in at least 12 varied positions/tilts spanning the frame. The defaults are **9 × 6 inner corners** (10 × 7 squares). Measure one square's side. Example with 25 mm squares:

```bash
python -m tools.calibrate_front capture --output calibration_views --count 24
python -m tools.calibrate_front intrinsics \
  --images 'calibration_views/*.png' --columns 9 --rows 6 --square-m 0.025 \
  --output front_intrinsics.json
```

Capture a reference while wearing the helmet normally, stationary and looking straight down the approach lane at the marked chair:

```bash
python -m tools.calibrate_front capture --output alignment --count 1
```

Use the printed image path in the next command. All geometry below is **illustrative**: replace it with measurements of your chair and normal riding posture. Offsets are forward distances along the lane. `marker-to-front-m` is how far the chair's nearest surface extends toward the rider from the marker plane; `camera-to-bike-front-m` is how far the bike's leading edge extends ahead of the helmet camera.

```bash
python -m tools.calibrate_front align \
  --intrinsics front_intrinsics.json --image alignment/front_TIMESTAMP.png \
  --marker-m 0.20 --chair-width-m 0.60 \
  --marker-to-front-m 0.10 --camera-to-bike-front-m 0.60 \
  --output front_calibration.json
```

Re-align if the helmet mount, target marker orientation, or lane direction changes. Recalibrate intrinsics if lens/focus/resolution changes. Alignment rejects missing, small, clipped, poor-fitting, or ambiguous markers. Use a larger marker or closer setup when needed; don't loosen quality gates to force a result.

The pose implementation follows [OpenCV marker detection](https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html) and [PnP pose estimation](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html). It explicitly rejects materially different planar pose solutions with similar reprojection error.

## Run live

```bash
python -m helmet.main --collision --collision-calibration front_calibration.json \
  --headless --stream 8080 --no-gemini --collision-log collision_run.jsonl
```

The OLED shows BRAKE above rear arrows while rear haptic alerts continue. The web debug stream shows both camera views; `/view` shows the front chair as a tree, rear traffic, and forward state. `--no-hud` retains the preview without physical display writes.

The front and rear captures each feed a latest-frame buffer. One YOLO worker alternates camera requests, reusing the wrapper with a front-only chair class selection. A separate front marker worker computes range/policy and updates the OLED without waiting for rear inference. A dedicated front tracker handles chair identity only. Rear `update_metrics()` and `AlertPolicy` remain rear-only.

## What the numbers mean

Speed is the negative slope of measured forward distance, using a robust 0.5-second history. It is valid only for a stationary target, a straight approach, and consistent head posture. Pose rotation is transformed into the reference axes; excessive yaw/pitch (>10°), roll (>15°), lateral motion, bad pose fits, and range jumps suspend estimates. Small head translations remain indistinguishable from bike movement.

```
warning distance = speed × (reaction + delay) + speed² / (2 × deceleration) + margin
```

Defaults live in `helmet/collision.py::Settings`: reaction 1 s, deceleration 1.5 m/s², delay at least 0.25 s, margin 0.75 m, bike half-width 0.35 m, and lateral margin 0.25 m. Increase delay based on measurements; verify deceleration on the actual bike/rider/surface. The runtime adds range and speed uncertainty, projects the fitted distance to the latest sample, and accounts for host frame age without adding the same delay twice. These are demo engineering assumptions, not certified uncertainty bounds.

States:

- `INITIALIZING`: collecting at least five samples over 0.3 s and 0.5 s of aligned tracking.
- `CLEAR`: valid measurements, no qualifying threat detected. Not a guarantee of safety.
- `CAUTION`: a closing, on-path target is within 1 m of the braking boundary.
- `BRAKE`: confirmed boundary crossing; two observations normally, immediate for a severe crossing. Holds at least 0.8 s with recovery hysteresis.
- `UNAVAILABLE`: missing chair association, stale data, poor/ambiguous pose, excessive movement, or above the demo speed limit. OLED shows `LOOK AHEAD` for orientation failure or `FRONT LOST` otherwise.

Chair detection may drop out for up to 1 s while the same configured marker remains visible. Losing the marker invalidates range immediately. An active BRAKE warning is held briefly on loss, then becomes unavailable; old speed is never treated as zero. Reacquisition starts a fresh history. If the producer stalls entirely, the OLED watchdog expires its state after 0.8 s.

Snapshot additions are additive: `front_obstacles` (camera-qualified IDs, negative z = ahead), and `collision` (state, reason, metric position, clearance, estimated approach speed/error, TTC, boundary, alignment, measurement age, and update rates). Existing rear `cars`/`hud` fields keep their meaning. The shared feed also retains rear `incidents` metadata for the iOS `/api/v1/state` endpoint; front BRAKE decisions are recorded separately in the collision JSONL log. JSON logs start with a calibration/settings session record followed by decision records.

## Record and replay both cameras

Stop the live app, then record raw clips and shared host capture timestamps. This recording tool does not issue warnings; use it only for footage collection.

```bash
python -m tools.record_pair --output demo_take_01 \
  --calibration front_calibration.json --seconds 20
python -m helmet.main --collision \
  --collision-calibration demo_take_01/calibration.json \
  --video demo_take_01/rear.avi --front-video demo_take_01/front.avi \
  --replay-timestamps demo_take_01/timestamps.json \
  --headless --stream 8080 --no-gemini --collision-log replay.jsonl
```

Replay merges frames on one media timeline and stops at the shorter clip. It slows down rather than dropping frames when processing is slower than footage. Space pauses both; `--loop` resets tracking between passes; `--no-realtime` runs the same decisions without pacing. For other clips, omit the sidecar only if both have been trimmed so frame zero is the same physical instant; preserve capture orientation and resolution.

Source timestamps are **host frame-receipt timestamps**, not sensor exposure timestamps. Logged processing age does not by itself prove capture-to-photon latency. Validate actual warning timing using an external video of the target marks and OLED, including exposure/buffering and panel delays.

## Acceptance checklist

1. Check correct camera assignment, marker size, alignment, chair detection, and readable OLED orientation through the actual helmet mount.
2. Measure range at 1, 2, 4, 6, and 8 m where visible. Target error: max(10%, 0.15 m). Determine the usable range; it must exceed the warning/acquisition distance at your chosen speed.
3. Compare approach speed against timed travel over floor marks. Target error after initialization: max(20%, 0.2 m/s).
4. With both cameras and the web view active, target ≥10 valid front ranges/s, ≥5 YOLO updates/s per camera, and 95th-percentile capture-to-OLED latency <250 ms. Check heat/power stability on the Pi. These are acceptance targets, not measured performance claims.
5. Confirm braking deceleration supports the configured value. Use lower speed or a more conservative deceleration if necessary.
6. Complete ten approaches with warnings before the independently calculated stopping boundary, and ten stationary/receding/off-path trials without BRAKE. Keep a predetermined stopping line regardless of the warning.
7. Turn/nod the head, obscure the marker, unplug each camera separately, and simulate missing frames. Verify unavailable states and reacquisition; a rear fault must not suppress a valid front BRAKE.
8. Save calibration, paired footage, JSONL decisions, and measured results. Successful trials establish the demo setup only, not road-use reliability.

Software checks (no cameras, YOLO weights, or GPIO needed):

```bash
python -m tests.test_collision
python -m tests.test_front_mono
python -m tests.test_synthetic
python -m tests.test_hud
python -m tests.test_sources
python -m tests.test_headless
python -m tests.test_view
python -m tests.test_fusion
python -m tests.test_gateway
python -m tests.test_incidents
```
