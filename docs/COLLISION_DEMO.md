# Helmet forward collision demo: marker mode

> If you don't have time to calibrate, run `--collision` without `--collision-calibration`.
> That camera-only mode gives the same BRAKE warning with no marker or checkerboard; see
> [FRONT_CAMERA_ONLY.md](FRONT_CAMERA_ONLY.md). This page covers the marker mode.

`--collision --collision-calibration FILE` runs both helmet cameras. It detects a chair, pairs
it with a known-size ArUco marker, estimates your approach speed to that stationary target, and
shows BRAKE / BRAKE on the OLED. The web scene draws the chair as a tree on purpose; the
detector and telemetry still call it `chair`.

This is a calibrated, low-speed hackathon demo. It doesn't measure bike speed on its own, know
the bike's heading during head turns, or detect arbitrary collision hazards. It doesn't depend
on an accelerometer, ultrasonic sensors, GPS, the cloud or an external speed sensor. The
rear-only commands work the same with or without it.

## Prepare the scene

- Use a padded, stationary chair with one rigid, flat `DICT_4X4_50` marker (ID 0) centred on it
  horizontally. Face the marker toward the approach lane and keep the whole marker inside the
  chair's detection box. Don't let it cover so much of the chair that YOLO stops recognizing
  the chair.
- Mark a straight approach lane and a stopping line. Hold a consistent helmet posture, looking
  ahead. Stop before the chair; don't test an impact.
- Start well before the warning boundary so the marker and chair can be picked up and the speed
  estimate can settle. The reference orientation is the camera's orientation relative to this
  fixed marker, not a compass heading.
- The initial maximum speed is 2 m/s (4.5 mph). The nominal warning distance at that speed is
  4.58 m, before uncertainty allowances. Lower the speed if range, latency or braking validation
  fails.
- Marker size matters. 20 cm is only a starting point, and the ranger rejects markers with any
  side below 16 pixels. A wide-angle camera may need a 40 to 60 cm marker or a much slower
  approach. Check detection well beyond the warning boundary, not just beside the chair, and
  enter the actual size in calibration.
- Keep focus, resolution and image orientation fixed. The rear and front cameras have separate
  orientation settings, and the front image is not mirrored.

## Calibrate once, align for each physical setup

Run these commands on the Pi in the same environment as the app, and stop the app before
capturing. Nothing is borrowed from the rear camera's calibration.

Generate and print a marker:

```bash
python -m tools.calibrate_front marker --output front_marker.png
```

Measure the outer black square, not the white margin. Print it without stretching and mount it
flat.

For the camera intrinsics, photograph a checkerboard in at least 12 varied positions and tilts
across the frame. The defaults are 9 × 6 inner corners (10 × 7 squares). Measure one square's
side. With 25 mm squares, for example:

```bash
python -m tools.calibrate_front capture --output calibration_views --count 24
python -m tools.calibrate_front intrinsics \
  --images 'calibration_views/*.png' --columns 9 --rows 6 --square-m 0.025 \
  --output front_intrinsics.json
```

Capture a reference image while wearing the helmet normally, standing still and looking straight
down the approach lane at the marked chair:

```bash
python -m tools.calibrate_front capture --output alignment --count 1
```

Use the printed image path in the next command. The geometry below is only an example; replace
it with measurements of your chair and your normal riding posture. Offsets are forward distances
along the lane. `marker-to-front-m` is how far the chair's nearest surface sticks out toward the
rider from the marker plane, and `camera-to-bike-front-m` is how far the bike's leading edge is
ahead of the helmet camera.

```bash
python -m tools.calibrate_front align \
  --intrinsics front_intrinsics.json --image alignment/front_TIMESTAMP.png \
  --marker-m 0.20 --chair-width-m 0.60 \
  --marker-to-front-m 0.10 --camera-to-bike-front-m 0.60 \
  --output front_calibration.json
```

Re-align if the helmet mount, the marker's orientation or the lane direction changes.
Recalibrate the intrinsics if the lens, focus or resolution changes. Alignment rejects missing,
small, clipped, poorly fitting or ambiguous markers. If that happens, use a larger marker or a
closer setup rather than loosening the quality checks.

The pose code follows [OpenCV marker detection](https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html)
and [PnP pose estimation](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html). It rejects
a marker when two clearly different planar poses fit it with similar reprojection error.

## Run live

```bash
python -m helmet.main --collision --collision-calibration front_calibration.json \
  --headless --stream 8080 --no-gemini --collision-log collision_run.jsonl
```

During BRAKE the OLED shows only BRAKE, while the rear haptic alerts keep firing. The web debug
stream shows both camera views, and `/view` shows the front chair as a tree along with rear
traffic and the forward state. `--no-hud` keeps the preview without writing to the physical
display.

The front and rear captures each feed a latest-frame buffer. One YOLO worker alternates between
the cameras, using a chair-only class list for the front. A separate front marker worker
computes range and the policy and updates the OLED without waiting for rear inference. A
dedicated front tracker only handles the chair's identity; the rear `update_metrics()` and
`AlertPolicy` only ever see rear detections.

## What the numbers mean

Speed is the negative slope of the measured forward distance, from a median-based fit over the last 0.5 s.
It's only valid for a stationary target, a straight approach and a consistent head posture. The
pose rotation is transformed into the reference axes. Too much yaw or pitch (over 10°), roll
(over 15°), lateral motion, a bad pose fit or a jump in range suspends the estimate. Small head
movements still can't be told apart from bike movement.

```
warning distance = speed × (reaction + delay) + speed² / (2 × deceleration) + margin
```

The defaults are in `helmet/collision.py::Settings`: reaction 1 s, deceleration 1.5 m/s², delay
at least 0.25 s, margin 0.75 m, bike half-width 0.35 m and lateral margin 0.25 m. Increase the
delay based on measurements, and check the deceleration on the actual bike, rider and surface.
At runtime the policy adds range and speed uncertainty, projects the fitted distance to the
latest sample, and accounts for the host frame's age without adding the same delay twice. These
are demo engineering assumptions, not certified uncertainty bounds.

States:

- `INITIALIZING`: collecting at least five samples over 0.3 s, and 0.5 s of aligned tracking.
- `CLEAR`: valid measurements and no qualifying threat. This doesn't guarantee safety.
- `CAUTION`: a closing target in your path is within 1 m of the braking boundary.
- `BRAKE`: a confirmed boundary crossing, normally over two observations and immediately for a
  severe crossing. It holds at least 0.8 s, with hysteresis before it clears.
- `UNAVAILABLE`: the chair isn't paired with the marker, the data is stale, the pose is poor or
  ambiguous, there's too much movement, or the speed is above the demo limit. The OLED shows
  `LOOK AHEAD` for an orientation failure and `FRONT LOST` otherwise.

Chair detection may drop out for up to 1 s while the same marker stays visible. Losing the
marker invalidates the range immediately. An active BRAKE is held briefly after a loss and then
becomes unavailable; an old speed is never treated as zero. When the marker comes back, a fresh
history starts. If the producer stalls entirely, the OLED watchdog expires its state after
0.8 s.

The snapshot gains two fields and keeps the existing ones. `front_obstacles` lists
camera-qualified IDs (negative z means ahead). `collision` holds the state, reason, metric
position, clearance, estimated approach speed and error, TTC, boundary, alignment, measurement
age and update rates. The rear `cars` and `hud` fields mean what they always did. The shared feed
also carries the rear `incidents` metadata for the iOS `/api/v1/state` endpoint, while front
BRAKE decisions go to the collision JSONL log. Each JSON log starts with a calibration and
settings record, followed by the decision records.

## Record and replay both cameras

Stop the live app, then record raw clips with shared host capture timestamps. The recording tool
doesn't issue warnings; it's only for collecting footage.

```bash
python -m tools.record_pair --output demo_take_01 \
  --calibration front_calibration.json --seconds 20
python -m helmet.main --collision \
  --collision-calibration demo_take_01/calibration.json \
  --video demo_take_01/rear.avi --front-video demo_take_01/front.avi \
  --replay-timestamps demo_take_01/timestamps.json \
  --headless --stream 8080 --no-gemini --collision-log replay.jsonl
```

Replay merges frames on one media timeline and stops at the end of the shorter clip. If
processing is slower than the footage, it slows down instead of dropping frames. Space pauses
both clips, `--loop` resets tracking between passes, and `--no-realtime` runs the same decisions
without pacing. For other clips, leave out the timestamp file only if both clips are trimmed so
that frame zero is the same moment, and keep the capture orientation and resolution.

The source timestamps are the times the host received each frame, not sensor exposure times. The
logged processing age alone doesn't prove capture-to-photon latency. To check the real warning
timing, film the floor marks and the OLED with a separate camera, which also captures exposure,
buffering and panel delays.

## Acceptance checklist

1. Check the camera assignment, marker size, alignment, chair detection, and that the OLED reads
   the right way round through the actual helmet mount.
2. Measure range at 1, 2, 4, 6 and 8 m where the marker is visible. Target error: max(10%,
   0.15 m). Find the usable range; it must exceed the warning and acquisition distance at your
   chosen speed.
3. Compare the approach speed with timed travel over floor marks. Target error after
   initialization: max(20%, 0.2 m/s).
4. With both cameras and the web view running, aim for at least 10 valid front ranges per
   second, at least 5 YOLO updates per second per camera, and a 95th-percentile capture-to-OLED
   latency under 250 ms. Check heat and power stability on the Pi. These are targets, not
   measured results.
5. Confirm the bike can brake at the configured deceleration. Use a lower speed or a more
   conservative deceleration if it can't.
6. Do ten approaches with warnings before the independently calculated stopping boundary, and
   ten stationary, receding or off-path trials without BRAKE. Keep a fixed stopping line
   whatever the warning says.
7. Turn and nod your head, cover the marker, unplug each camera separately, and simulate missing
   frames. Check the unavailable states and reacquisition. A rear fault must not suppress a
   valid front BRAKE.
8. Save the calibration, paired footage, JSONL decisions and measured results. Successful trials
   show that this demo setup works, not that it's reliable on the road.

Software checks (no cameras, YOLO weights or GPIO needed):

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
