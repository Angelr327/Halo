# Front BRAKE warning, no calibration

`--collision` without a calibration file runs the front camera through the **same pipeline as
the rear**: YOLO, tracker, time to contact from box growth, predicted path. When something ahead
is in your path and will be reached in **2.8 s or less**, the OLED shows **BRAKE**. The 3D view
draws it ahead of the bike (a chair is drawn as a tree) while rear traffic is drawn behind.
There's no marker, checkerboard, or alignment photo.

The marker mode ([COLLISION_DEMO.md](COLLISION_DEMO.md)) is still there for exact metres, with
`--collision-calibration`.

## Run it

On the Pi (stop any other run first, the cameras can only be opened once):

```bash
python -m helmet.main --collision --demo-person --headless --stream 8080 --no-gemini
```

Open `http://<pi-ip>:8080/view` on a laptop or phone on the same network. `--demo-person` makes
the **rear** camera count walking teammates as traffic, for an indoor demo. The front always
looks for people.

| Goal | Command |
|---|---|
| No hardware at all (demo backup) | `python -m helmet.main --sim --collision` (add `--demo-person` for teammates behind) |
| Record both cameras for a replay test | `python -m tools.record_pair --output take_01 --seconds 20` |
| Replay a recording | `python -m helmet.main --collision --video take_01/rear.avi --front-video take_01/front.avi --replay-timestamps take_01/timestamps.json --headless --stream 8080 --no-gemini` |
| Save every front decision | add `--collision-log front.jsonl` |
| Regenerate the evidence table below | `python -m tools.eval_front` |
| Run the tests | `python -m tests.test_front_mono` |

## How it works

1. **Detect:** YOLO looks for people, bikes, cars, motorbikes, buses, trucks and chairs in
   the front image (`FRONT_CLASSES` in `config.py`). It shares one model with the rear camera, taking
   turns, so each camera gets about half the frame rate.
2. **Track:** the rear's tracker keeps an ID on each object across frames.
3. **Time to contact:** a box that doubles in width means you've halved the distance, whatever
   the lens or the object's real size. So TTC = 2 × area / (rate of area growth) needs **no
   calibration**. It's the same principle camera-only forward collision warnings in cars use.
   Growth must be consistent over about a second, which rejects YOLO box jitter.
4. **Path:** sideways offset = (box centre − image centre) / box width × typical width. This is
   distance-independent. A line fitted through it predicts where the object will be when you
   reach it. In-path means inside your 1.2 m corridor now or at contact.
5. **Decide on time, not metres.** Stopping distance divided by speed is almost constant at
   demo speeds. Using the marker mode's formula (1 s reaction, 0.25 s delay, 1.5 m/s² braking,
   0.75 m margin):

   | Speed | Stopping distance | As time to contact |
   |---|---|---|
   | 1.0 m/s (slow walk) | 2.33 m | 2.3 s |
   | 1.4 m/s (walk) | 3.15 m | 2.3 s |
   | 2.0 m/s (jog) | 4.58 m | 2.3 s |

   BRAKE fires at **2.8 s**: 2.3 s, plus about 0.5 s for the front's detection rate and a
   2-frame confirmation. CAUTION fires at 4.5 s (orange in the view, same as the rear's
   blind-spot tier). BRAKE is held at least 0.8 s.
6. **Distance** comes from box width and a typical width per class (chair 0.5 m, person 0.5 m,
   car 1.8 m). It's rough (±20–30%) and only used for display, marked `~` in the view. Nothing
   is decided on it.

Code: `helmet/front_mono.py` (engine and policy), reusing `helmet/perception.py` (tracker, TTC, path).

## Demo script

1. Put a chair in open floor space. Start 7–8 m away, facing it.
2. Walk at a normal pace straight at it, **looking where you're going**.
   - About 4.5 s out, the tree turns orange and the banner says "Chair ahead".
   - About 2.8 s out, which is 3.5–4 m at a walk, the OLED says **BRAKE** and the tree turns red.
   - Stop. The warning clears.
3. Walk past the chair 1.5 m to one side: nothing fires.
4. Stand still 3 m from it: nothing fires.
5. Have a teammate walk at you: BRAKE, with a person model in the view.
6. Meanwhile, rear alerts keep working. A teammate approaching from behind (with `--demo-person`)
   shows up behind the bike in the same 3D view.

If the room or the camera misbehaves, run `--sim --collision`. It scripts all of the above
through the real engine and the real 3D view.

## Test cases

### Automated (no hardware; `python -m tests.test_front_mono`)

| What it proves | Test |
|---|---|
| BRAKE fires before the stopping distance at 1.0, 1.4, 2.0, 2.6 m/s (7.5 detections/s) and 1.0–2.0 m/s (5/s), 10 seeds each | `test_brake_fires_before_the_stopping_distance` |
| A person walking at you, and ±60 px of head sway (about ±5°), still brake on time | `test_person_walking_at_you_and_head_sway` |
| No BRAKE when standing still, backing away, or walking past on either side | `test_no_brake_when_stationary_receding_or_passing` |
| Stopping short clears the warning after the hold | `test_stopping_clears_after_the_hold` |
| Camera loss holds BRAKE briefly and never drops it silently; time going backwards (replay loop) starts fresh | `test_camera_loss_holds_brake_briefly_then_unavailable` |
| Same output fields as marker mode, valid JSON, decision log written | `test_result_matches_marker_fields_and_is_json`, `test_log_and_preview` |
| Every tracked object ahead reaches the 3D view; a stale feed clears them; OLED shows BRAKE | `OutputTests` |
| Live dual-camera threads, paired-video replay and simulator scenes all reach BRAKE (or don't) correctly | `RuntimeTests` |
| `--collision` needs no calibration; `--sim --collision` serves front and rear together end to end | `CommandLineTests` |

### Simulated evidence (`python -m tools.eval_front`, 20 runs per row)

Scripted approaches with YOLO-like box jitter, fed through the real engine. **On time** means
BRAKE fired before the stopping distance.

| Detections/s | Speed | Start | Stopping distance | On time | Gap at BRAKE (min / median) |
|---|---|---|---|---|---|
| 7.5 | 1.0 m/s | 10 m | 2.33 m | 20/20 | 2.50 / 2.77 m |
| 7.5 | 1.4 m/s | 10 m | 3.15 m | 20/20 | 3.54 / 3.91 m |
| 7.5 | 2.0 m/s | 10 m | 4.58 m | 20/20 | 5.43 / 5.43 m |
| 7.5 | 2.6 m/s | 10 m | 6.25 m | 20/20 | 6.58 / 6.93 m |
| 5.0 | 1.0 m/s | 10 m | 2.33 m | 20/20 | 2.50 / 2.70 m |
| 5.0 | 1.4 m/s | 10 m | 3.15 m | 20/20 | 3.54 / 3.82 m |
| 5.0 | 2.0 m/s | 10 m | 4.58 m | 20/20 | 4.90 / 5.30 m |
| 5.0 | 2.6 m/s | 10 m | 6.25 m | 0/20 | 5.54 / 5.54 m |
| 5.0 | 2.6 m/s | 14 m | 6.25 m | 20/20 | 6.42 / 6.94 m |

The one failing row isn't a threshold problem. TTC needs about 8 detections of history (1.6 s at
5/s) before its first estimate, and from 10 m at a jog that's too late. From 14 m it's on time.
So the run-up must cover about 1.5 s of tracking plus the stopping distance.

False BRAKE, 20 runs each, with ±30 px of head sway: standing 3 m from a chair **0/20**; person
standing 4 m ahead **0/20**; walking past 1.5 m right **0/20**; walking past 1.0 m left **0/20**;
backing away **0/20**.

The simulator always detects the object. Real detection range is the thing to measure on the Pi (L0 below).

### Live acceptance on the Pi (fill in the results column at the venue)

| ID | Setup | Pass if | Result |
|---|---|---|---|
| L0 | Walk backwards away from the chair watching the front box on `http://<pi-ip>:8080/` | Note the distance where the box disappears. It must exceed stopping distance + 1.5 s of walking (about 5.3 m at 1.4 m/s) | |
| L1 | Walk at the chair from 7 m at a normal pace, 5 times; tape a mark at 3.15 m and film the OLED and the mark | BRAKE before the mark every time | |
| L2 | Stand 3 m from the chair for 30 s, looking around normally | No BRAKE, no CAUTION | |
| L3 | Walk past the chair 1.5 m to each side, 3 times | No BRAKE | |
| L4 | Back away from the chair | No alert | |
| L5 | A teammate walks at you while you walk at them | BRAKE, with a person model in the view | |
| L6 | Power off, disconnect the front camera's ribbon, boot and start the app | OLED shows FRONT LOST, view says "Front camera unavailable", rear alerts work normally | |
| L7 | A teammate approaches from behind while the front is clear, then both at once | Rear arrows and haptics for the rear. With both, BRAKE takes over the OLED and the rear buzz still fires | |
| L8 | Both cameras running, open `http://<pi-ip>:8080/api/v1/state` | `collision.front_yolo_hz` at least 5 (front detections per second) | |

## Limitations (say these before a judge asks)

- **Distances are rough (±20–30%).** They're for display only. Timing comes from box growth, which is solid.
- **Straight ahead means the centre of the image.** Look where you're going. Staring at an
  object off to the side can make an object you'll miss look like one you'll hit, and the reverse.
- **YOLO has to recognise it.** The COCO classes cover people, vehicles and chairs, but not
  potholes, bollards or car doors.
- **Shared model, half the frame rate.** The front needs about 1.5 s of tracking before its first
  TTC, which sets the minimum run-up.
- **A covered or blacked-out lens reads as "nothing ahead"**, not as a fault. Only a camera
  that stops sending frames shows FRONT LOST.
- **Low objects drop out of view up close.** A level helmet camera loses a 0.9 m chair at about
  1.3 m. BRAKE is held 0.8 s, and by then you should have stopped.
- **No ego speed and no road validation.** Everything here is walking-speed, indoor, and simulated evidence.

## Roadmap

**Next (days)**
- Give the front its own inference slot or a larger `IMGSZ` for detection range. Consider a
  Raspberry Pi AI HAT (Hailo) to run both cameras at full rate.
- **Blocked-lens detection**: flag a front image that is almost uniform (lens covered, mud)
  as FRONT LOST instead of "nothing ahead". Tune the threshold on real footage so a dark room
  doesn't trigger it.
- Draw the predicted path ribbon for front objects too. The data (`pred_lat_m`) is already computed.
- Record every rehearsal (`tools.record_pair` + `--collision-log`) into a labelled clip set, and
  replay it as a regression test on each change.

**Medium term**
- An **IMU** in the helmet, to tell head turns from bike heading, and **wheel or phone-GPS speed**.
  With real speed, the fixed 2.8 s becomes a true per-speed stopping distance.
- **Focus of expansion from optical flow**, to find where you're actually heading instead of
  assuming the image centre. This removes the "look where you're going" limit.
- **Ground-plane ranging** (box bottom + camera height), to cross-check the width-based distance.
- **Fine-tune YOLO on cyclist-view data**: potholes, bollards, opening car doors, which COCO lacks.

**Long term**
- **Door-zone warnings**: parked car plus a door starting to open, a leading cause of urban
  cyclist crashes.
- A **near-miss map** from the incident reports the helmet already saves (clip + Gemini summary + location from the phone).
- **Road validation**: a labelled on-road dataset, measured photon-to-OLED latency, and
  night and rain performance, before any claim beyond a demo.
