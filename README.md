Created by: Sion King

# Blind-spot helmet

Rear-facing camera → local YOLO detection → directional haptics, a rear light that warns the
driver, and short spoken alerts. Gemini adds language on top, asynchronously, and is never
in the safety path.

```
FAST PATH (every frame, ~70-120 ms, no network)
camera ─► mirror ─► global-motion ─► YOLO ─► tracker ─► TTC / zone ─► policy ─► motors + light + "Truck left!"
                                                                          │
SLOW PATH (events only, ~1-2 s, optional)                                 ▼ submit (non-blocking)
                            GeminiGateway: budget ▸ min interval ▸ coalesce ▸ expire ▸ circuit breaker
                                                                          │
                          drained next frame through guardrails ◄─────────┘  ("Box truck passing close on your left")
```

## Raspberry Pi (current target)

The Pi replaces the laptop. The Arduino stays: it's the independent watchdog that turns
the light into a normal bike light if the Pi crashes, and NeoPixel timing is easy there.

```bash
git clone <your repo> ~/blindspot-helmet && cd ~/blindspot-helmet
bash scripts/setup_pi.sh             # apt + venv + pip + NCNN export + all tests (needs internet)
. .venv/bin/activate
python -m helmet.main --video clip.mp4 --loop     # works with NO camera, Arduino, or headphones
```

Then open the printed `http://<pi-ip>:8080` on a phone or laptop on the same network:
live overlay plus buttons for every key command. On the Pi's own desktop, add `--window`
for the normal OpenCV window.

What changes automatically on a Pi (`config.IS_PI`):
- **Detector**: `yolo26n.pt` exported once to NCNN (`yolo26n_imgsz320_ncnn_model/`), `IMGSZ = 320`.
  Benchmark with `python -m tools.bench yolo --video clip.mp4` and use the largest size that
  keeps you at 8+ FPS end to end.
- **Camera**: a Camera Module (3 Wide recommended: light, ~120 deg) is used if present,
  else the USB webcam. Force with `--camera-source usb|picamera2`.
- **Display**: headless + web view; drawing is skipped entirely when nobody is watching.
- **Speech**: `espeak-ng` through the default audio output (pair the Bluetooth headset in the
  desktop's Bluetooth menu or with `bluetoothctl`).

Auto-start at boot (no laptop at all): see `deploy/helmet.service`.

**Power, the #1 Pi gotcha.** On a normal 5V/3A supply or power bank, the Pi 5 caps total USB
current at 600 mA. The C920 plus the Arduino with motors and LEDs can exceed that, and the
symptom is a camera or serial port that randomly drops. Use the official 27 W supply for the
stationary demo. On a power bank, the Camera Module (CSI, not USB) removes the webcam's
draw. Adding `usb_max_current_enable=1` to `/boot/firmware/config.txt` lifts the cap at
your own risk.

**Heat.** Continuous YOLO throttles an uncooled Pi 5 within minutes. Fit the Active Cooler
and watch `vcgencmd measure_temp`.

## Setup (laptop)

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                       # paste your Gemini key
python -m tests.test_synthetic && python -m tests.test_gateway && python -m tests.test_headless   # no hardware needed
```

Flash `firmware/helmet_arduino/helmet_arduino.ino` with the Arduino IDE (install
**Adafruit NeoPixel** from the Library Manager; board "Arduino Nano", processor
"ATmega328P" or "ATmega328P (Old Bootloader)" for most clones). On power-up you should feel
LEFT then RIGHT.

## Run

```bash
python -m helmet.main                          # live camera, auto-detects the Arduino
python -m helmet.main --demo-person            # stationary demo: walking teammates count as vehicles
python -m helmet.main --video clip.mp4 --loop  # recorded footage (real-time paced, video timestamps)
python -m helmet.main --no-gemini              # prove it works fully offline
python -m helmet.main --agent                  # Gemini tool calling
python -m helmet.main --log run.csv            # per-frame metrics for calibration
python -m helmet.main --model yolo26n.pt       # swap detector
python -m helmet.main --sim                    # scripted traffic, no camera/YOLO; open http://<ip>:8080/view
```

| Key | Action | Key | Action |
|---|---|---|---|
| q / Esc | quit | f | simulate camera failure |
| g | "What's behind me?" (Gemini, or local answer offline) | k | kill heartbeat → Arduino failsafe |
| v | voice question (3 s, laptop mic) | x | Gemini on/off |
| t | haptic test: left, right, both | a | describe ↔ agent mode |
| l | cycle light override | m | mirror on/off |
| p | cycle sensitivity profile | c | hot-reload `config.py` |
| space | pause video | r | record the debug window to MP4 |
| d | hide/show metrics panel | | |

The handlebar button (D3 to GND) does the same as **g**. Headless, the same commands are buttons in the web view.

## Wiring (Arduino Uno + Pi, no soldering)

**Pi ↔ Uno: only the Uno's USB cable** (USB-A on the Pi, USB-B on the Uno). It carries power,
serial and a shared ground. The OLED is the only thing wired to the Pi's pins (see the HUD section).

Run the Uno's **5V** pin to the breadboard's **+** rail and a **GND** pin to the **−** rail; every
part below takes power from those rails.

| Part | Part pin | Uno pin |
|---|---|---|
| Ultrasonic SL (left side, pointing out ~5° back) | TRIG / ECHO | D6 / D7 |
| Ultrasonic SR (right side, pointing out ~5° back) | TRIG / ECHO | D8 / D12 |
| Ultrasonic BL (back, left of centre, optional) | TRIG / ECHO | A0 / A1 |
| Ultrasonic BR (back, right of centre, optional) | TRIG / ECHO | A2 / A3 |
| Left vibration motor | via NPN (below), or module IN | D5 |
| Right vibration motor | via NPN, or module IN | D10 |
| Left buzzer | I/O (3-pin module), or via NPN | D9 |
| Right buzzer | I/O, or via NPN | D11 |
| Optional button | one leg | D3 (other leg → GND) |
| Optional NeoPixel rear light | DIN | D2 (330 Ω in series if you have one) |
| All ultrasonics, modules | VCC / GND | + rail / − rail |

The Uno is 5V, so the HC-SR04 ECHO pins connect directly (no voltage dividers, unlike the Pi).
Mark each wired sensor `true` in `SONAR_FITTED` in the sketch (only SL is on by default); only
fitted sensors are pinged, so one sensor updates ~33 times a second, two ~16.

**Bare vibration disc (2 wires): one NPN transistor each** (2N2222 / PN2222 / S8050).
Never drive a motor straight from a pin; it draws more than a pin can give.
```
Uno D5 ──[1 kΩ]── B (base)
                  C (collector) ── motor red wire ... motor other wire ── + rail (5V)
                  E (emitter)   ── − rail (GND)
diode (1N4148/1N4001) across the motor: striped end (cathode) to the 5V side
```
A bare 2-pin buzzer is wired the same way (buzzer + to 5V, − to the collector, no diode).
Buzzer and vibration modules with 3 pins (VCC, GND, IN/I/O) have the transistor built in:
IN goes straight to the Uno pin.

Check it before mounting: flash the sketch from the Pi with `bash scripts/flash_arduino.sh`
(no Arduino IDE needed; `bash scripts/flash_arduino.sh hcsr04_test` for the sensor test), then
`python -m tools.bench serial` on the Pi.
You should feel left then right at power-up; `l` / `r` buzz each side (strong also beeps);
`u` prints the four distances (wave a hand in front of each sensor); `z0` mutes the buzzers.
Total draw is roughly 250 mA from the Pi's USB (see the power note above).

### Standalone two-sensor test

`firmware/hcsr04_test/hcsr04_test.ino` tests both helmet sensors with this wiring:

| Sensor | TRIG | ECHO |
|---|---|---|
| Left | D7 | D6 |
| Right | D11 | D10 |

Connect both sensors' VCC pins to the breadboard's + rail (Arduino **5V**) and both
GND pins to the − rail (Arduino **GND**). Upload the sketch in the Arduino IDE or run
`bash scripts/flash_arduino.sh hcsr04_test`, then open Serial Monitor at **9600 baud**.
Each line shows separate **LEFT** and **RIGHT** distances (or `no echo`) and echo success
percentages. Pings alternate with a 65 ms quiet gap; the built-in LED lights if either
sensor reads under 50 cm.

This test wiring differs from the main helmet wiring above: the main firmware uses
D10/D11 for the right motor/buzzer. Restore the main wiring before uploading
`helmet_arduino.ino` again. Close Serial Monitor before using the helmet app or bench tool.

## Camera + ultrasonic fusion

The rear camera sees about ±33° either side of straight back, so a car right **beside** you is
outside its view. The side ultrasonic sensors see exactly there and measure the gap to a few cm.
The camera says *what* it is and *that it approached*; the sensor says *how close* it passed.

**Mounting:** one sensor per side at the widest point of the helmet, pointing **straight out,
level** (at most ~10-15° back; the default config assumes 5°). A car's flat side reflects sound
away at steep angles, so angled-back sensors miss cars. Tilt slightly up, never down, and check
that each reads `---` with nobody around (a steady reading = it sees your shoulder or backpack).
Tell the software which are fitted in `helmet/config.py`: `SONAR_MOUNT = {"SL": ("LEFT", 5.0)}`
(add `"SR": ("RIGHT", 5.0)` for the right one).

**Pipeline (`helmet/fusion.py`):** per sensor, readings are range-gated, median-filtered, and
dropped while your head is turning; an echo that hasn't moved for 3 s is background (a wall,
your backpack) and ignored. A moving echo becomes a *contact*, linked to the camera track that
was just on that side (at the frame edge, or lost from view in the last 1.5 s):

| Situation | Alert |
|---|---|
| The camera saw it approach, the sensor measures it beside you inside the close-pass distance (1.0 m, 0.4 m for people) | **HIGH** "close pass 0.32 m measured" |
| Same, but a wider gap | **MED** "beside you 1.35 m measured" |
| Moving echo under 1.5 m the camera never saw | **MED** "object beside you" (never HIGH: it can't tell a car from a pole) |
| Camera fault | sensor contacts keep giving MED side alerts |

Every pass is logged, e.g. `[PASS] car passed on your left at 0.72 m`. In the 2.5D view the car
moves up beside the bike with a "0.72 m gap" label; an echo the camera never saw is drawn as an
orange column. `--sim` and `--sim --demo-person` include hand-off scenes with simulated echoes.

## 2.5D view (phone or laptop)

Open **`http://<pi-ip>:8080/view`** (the startup line prints it). It's a 3D rear-view mirror:
your bike in front, the road behind you, and every tracked car placed where the pipeline
measured it, tinted by alert tier (grey tracked, yellow approaching, orange blind spot, red
danger). The blind-spot zones and screen edges light up from the **same state the OLED shows**,
with the same arrows and the same 4 Hz blink for HIGH, so the phone, OLED and buzz always agree.
Cars show their time-to-contact, a cutting-in car shows its predicted path, and the ultrasonic
sensors draw arcs beside the bike. The camera button adds the live debug feed.

- **How:** the Pi sends a ~700-byte JSON snapshot per frame over `/state` (server-sent events);
  the phone draws the scene with three.js, which is bundled, so it works on a hotspot with no
  internet. No video is sent for the view itself.
- **No hardware?** `python -m helmet.main --sim` runs scripted traffic (car behind, normal left
  pass, car cutting in, close pass right, two cars, steady follower) through the real tracker,
  alerts, OLED and Arduino. Good for building the view and as a demo backup.
- **`--demo-person`:** people are drawn as walking figures instead of cars, the banner says
  "Person…", and a DEMO MODE badge shows. `--sim --demo-person` rehearses the stationary demo
  with no hardware: teammates jogging at you, walking past on the 1.5 m line, cutting in,
  brushing past, and standing still (which must stay quiet).
- **Laptop:** add `--stream 8080` to any run (`--video clip.mp4 --stream 8080`).
- Distance comes from one camera (±20-30%); side and time-to-contact are the reliable parts.

## Transparent OLED HUD (Pi)

Big arrows in the rider's peripheral vision for MED/HIGH threats: an arrow on the threat's side
(outline = MED, filled and blinking = HIGH), chevrons for "behind", an X when the camera is down,
and nothing at all (fully transparent) when it's clear. The debug overlay shows the same picture
top-left, labelled HUD, so you can check it without looking at the panel.

Wiring for the 1.51" transparent OLED (SSD1309, SPI by default) to the Pi's 40-pin header:

| OLED | Pi pin | | OLED | Pi pin |
|---|---|---|---|---|
| VCC | 3.3V (pin 1) | | CS | GPIO8 / CE0 (pin 24) |
| GND | GND (pin 6) | | DC | GPIO25 (pin 22) |
| DIN | GPIO10 / MOSI (pin 19) | | RST | GPIO27 (pin 13) |
| CLK | GPIO11 / SCLK (pin 23) | | | |

Enable SPI once (`sudo raspi-config nonint do_spi 0`, reboot). On a Pi 5, luma needs
`pip install rpi-lgpio` for the DC/RST pins (the old RPi.GPIO doesn't support the Pi 5).
The startup line `HUD:` says whether the panel was found; if not, it runs preview-only and
everything else is unaffected. Using I2C instead (resistor change on the back of some boards):
wire SDA/SCL to pins 3/5 and set `HUD_INTERFACE = "i2c"`.

Orientation: arrows are drawn from the rider's view. `HUD_ROTATE_180 = True` is for a panel
mounted upside down. If the rider sees arrows pointing the wrong way, it's usually because they
read the glass from the back: set `HUD_MIRROR = True`. Check with a teammate on your LEFT: the
arrow must point to your left.

## Serial protocol (57600 baud, newline-terminated)

`L<n>` `R<n>` `B<n>` haptics (0 stop, 1 gentle, 2 medium, 3 strong, 4 fault) ·
`M<n>` light 0 normal / 1 alert / 2 danger (sent every 250 ms = heartbeat) ·
`F1`/`F0` host fault · `X` all off · `?` status.
Board replies `READY`, `BTN`, `FAILSAFE`, `LINK OK`, `ERR <line>`.
No command for 1.5 s → the light becomes a normal flashing bike light and both motors
give a long buzz.

## Calibration procedure

Tune by watching numbers, not by guessing. Every metric is in the side panel:
`box` (width x height in pixels, for `FOCAL_PX`), `grow` (late/early size ratio), `cons` (fraction of frames that grew), `TTC`, `dist`,
`lat` (lateral offset, − = your left), `->` (predicted `lat` when it reaches you, `PATH` = heading
into your lane), `clr` (passing clearance), `[area|h|w]` (which box size TTC used), zone, tier and
the reason. A tier in brackets `(raw HIGH)` is what this frame says; the box only turns that colour
once it holds for `CONFIRM_FRAMES`, which is also when an alert actually fires.
Run with `--demo-person --no-gemini --log calib.csv`, edit `helmet/config.py`, press **c**.

**Your lane.** The shaded band is the "behind" corridor: bike half-width `RIDER_HALF_WIDTH_M` plus
`CORRIDOR_MARGIN_M` each side (1.2 m total by default). The band is drawn in perspective from
`CAMERA_HEIGHT_M`, so set that for your setup (helmet ≈ 1.6, table demo ≈ 0.8). The exact test is the
short cyan bar across each box's top: it is your lane at that object's distance, and a box that
overlaps it is zone `CENTER`. The arrow on a box points to where it will be sideways at contact.

**Setup.** Tape marks on the floor behind the helmet at 2, 4, 6, 8, 10 m on a centre line,
plus a parallel line 1.5 m to the left and right. Helmet at head height (≈1.6 m), camera
level and pointing straight back.

1. **Orientation.** A teammate stands on the LEFT line. The box must be on the left half
   with negative `lat` and zone `LEFT`. If not, press **m** and set `MIRROR_VIEW` to match.
   Press **t** while wearing the helmet: you must feel left, right, then both.
2. **Jitter floor.** Teammate stands still at 4 m for 10 s. Watch `grow` and `cons`.
   Set `MIN_GROWTH_RATIO` a little above the highest `grow` you see (typically 1.05-1.08)
   and `MIN_CONSISTENCY` above the highest `cons` (typically 0.55-0.65). TTC should
   read `inf` or > 20 s nearly all the time.
3. **TTC accuracy.** Teammate walks from 10 m straight at the camera at a steady pace
   while someone films the screen. TTC should count down about 1 s per second and
   reach ~0 at the helmet. Reading consistently high = lag: shorten `HISTORY_LEN`.
   Jumpy = lengthen it. Inside ~3.5 m the feet leave the frame and `[area]`/`[h]` switches to `[w]`
   (width); TTC should keep counting down through that switch.
4. **Lateral.** Stand on the 1.5 m side line at 3, 6 and 9 m. `lat` should read
   about ±1.5 m at every distance (it is distance-independent). If it's biased, the camera
   is yawed; if it's scaled, fix `CLASS_WIDTH_M` for that class.
5. **Distance (optional).** At a measured distance D, read the box width w px (first number after `box`):
   `FOCAL_PX = w * D / real_width_m`. Best done with a real car (1.8 m) in a parking lot.
6. **Shake.** Wear the helmet, nod and turn your head while the teammate stands still at
   5 m. `SHAKY` should light up during movement. No MED/HIGH may fire and the zone must
   not flip. Raise `SHAKE_GATE_FRAC` if normal riding posture keeps triggering SHAKY.
7. **Tiers.** Jog at the camera from 10 m: HIGH (red, both motors, strobe, "Person behind!")
   should fire around 2-3 s out. Walk past on the left line: MED left, not HIGH. Walk past
   0.95 m to the left: HIGH left ("close pass"; 0.7 m is inside the lane, so it's HIGH "behind").
   Start 2 m left and walk diagonally into the centre line: HIGH "cutting in" before you reach it.
   Walk in and stand still half out of frame at the edge: MED "alongside" at most, never HIGH.
8. **Real traffic.** Run each recorded clip with `--video clip.mp4 --log clipN.csv`
   (without `--demo-person`). Tally per clip: passes detected, correct side, false alerts,
   missed HIGHs. These are your pitch numbers.
9. **Venue.** Repeat steps 2 and 7 in the demo room. Lighting and background change jitter.

Commit the final values with a message like "calibrated at venue".

## Troubleshooting

- **Pi: `numpy.dtype size changed` when importing picamera2**: the venv's NumPy is newer
  than the one apt built picamera2 against. `pip install "numpy<2"` in the venv (older OS), or
  use a USB camera.
- **Pi: serial permission denied**: log out and back in after setup (dialout group).
- **Pi: web view unreachable**: same Wi-Fi as the Pi? Hackathon networks often isolate clients;
  use a phone hotspot for both. Set `STREAM_TOKEN` so strangers can't press your buttons.

- **Low FPS**: `python -m tools.bench yolo` compares models and sizes. Try `yolo26n.pt`
  (faster on CPU), `IMGSZ = 320`, or OpenVINO on Intel:
  `yolo export model=yolov8n.pt format=openvino imgsz=416` then `--model yolov8n_openvino_model/`.
- **No Arduino**: data-capable USB cable, CH340 driver on Windows/macOS, close the Arduino
  IDE serial monitor (only one program can hold the port), or pass `--port`.
- **Speech clips the first word** (Bluetooth power-saving): set `TTS_PREFIX = "Hey. "`.
- **Don't use the bone-conduction headset's mic**: it switches Bluetooth into call mode and
  wrecks audio. Use the laptop mic for **v**.
- **Gemini 429 errors**: raise `GEMINI_MIN_INTERVAL_S` to 60 / (your RPM in AI Studio).
- Main API here is `generate_content`; the newer Interactions API also exists but isn't needed.
