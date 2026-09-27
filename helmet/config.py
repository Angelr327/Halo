"""
All tunable settings in one place.

While main.py is running, edit this file and press  C  in the debug window to
hot-reload thresholds (code reads cfg.X at use time, so new values apply at once).
Settings marked [restart] are only read at startup.
"""

import os


def _is_raspberry_pi():
    try:
        with open("/proc/device-tree/model") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


IS_PI = _is_raspberry_pi()        # Pi-friendly defaults below switch on automatically

# ---------------------------------------------------------------- input
CAMERA_SOURCE = "auto"            # [restart] "auto" (Pi camera if present, else USB) | "usb" | "picamera2"
CAMERA_INDEX = 0                  # [restart]
CAPTURE_WIDTH = 640               # [restart]
CAPTURE_HEIGHT = 480              # [restart]
CAPTURE_FPS = 30                  # [restart]
DISABLE_AUTOFOCUS = True          # [restart] C920 focus "breathing" changes apparent size -> fake TTC
MIRROR_VIEW = True                # rear camera: flip so image-left == rider's LEFT (verify: calibration step 1)
CAMERA_TIMEOUT_S = 1.0            # no new frame for this long -> CAMERA FAULT

# ---------------------------------------------------------------- detector
MODEL_PATH = "yolo26n.pt" if IS_PI else "yolov8n.pt"   # [restart] YOLO26 is faster on ARM CPUs
IMGSZ = 320 if IS_PI else 416     # [restart] bigger = more range, fewer FPS
AUTO_EXPORT_NCNN = IS_PI          # [restart] convert .pt to NCNN once (fastest format on a Pi) and reuse it
CONF_THRESHOLD = 0.35
DEVICE = "cpu"                    # [restart] "mps" on Apple Silicon
VEHICLE_CLASSES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}   # COCO ids
DEMO_PERSON_AS_VEHICLE = False    # True for the stationary demo: walking teammates count as "vehicles"
DUPLICATE_IOU = 0.6               # same object labelled car AND truck -> keep the more confident box

# Typical rear-view widths (m). Used for lateral offset and rough distance.
CLASS_WIDTH_M = {"car": 1.8, "truck": 2.5, "bus": 2.55, "motorcycle": 0.8, "bicycle": 0.6, "person": 0.5}

# Camera geometry (only affects the distance readout, not TTC or zones)
HFOV_DEG = 64.0                   # C920 at 640x480 is roughly 60-70 deg; calibrate FOCAL_PX instead if you can
FOCAL_PX = None                   # set from calibration step 5: box_width_px * distance_m / real_width_m

# ---------------------------------------------------------------- tracker
HISTORY_LEN = 14                  # samples per track (~1.2 s at 12 FPS). Longer = smoother but laggier TTC
MIN_HISTORY_FOR_TTC = 8
MIN_GATE_PX = 60                  # matching gate floor
GATE_SCALE = 0.8                  # gate = GATE_SCALE * box size
MAX_SIZE_RATIO = 3.0              # reject matches whose area jumps more than this
TRACK_STALE_S = 2.5               # delete tracks unseen for this long
COAST_S = 0.35                    # unseen longer than this -> cannot trigger new alerts
MIN_BOX_W_PX = 14                 # smaller boxes are too noisy for metrics
LAT_EMA_ALPHA = 0.3               # smoothing of lateral offset

# ---------------------------------------------------------------- growth / TTC
MIN_GROWTH_RATIO = 1.06           # late-half mean area / early-half mean area
MIN_CONSISTENCY = 0.60            # fraction of frame-to-frame area increases in the buffer
TTC_CAP_S = 30.0
TTC_AGE_MAX_S = 1.0               # box clipped on a side AND top/bottom: count the last TTC down this long, then drop it

# ---------------------------------------------------------------- zones
ZONE_METHOD = "lateral"           # "lateral" (distance-invariant) or "thirds" (simple fallback)
RIDER_HALF_WIDTH_M = 0.35         # half of rider + handlebars (bars are ~0.6-0.7 m wide)
CORRIDOR_MARGIN_M = 0.25          # extra room each side of the bike. Your lane ("behind" zone) is
                                  # 2 * (RIDER_HALF_WIDTH_M + CORRIDOR_MARGIN_M) = 1.2 m wide
CLOSE_PASS_M = 1.0                # clearance under ~3 ft counts as "cutting close"
CLASS_CLOSE_PASS_M = {"person": 0.4}   # people pass at arm's length all the time; brush past to trigger
EDGE_MARGIN_PX = 4                # box touching frame edge -> vehicle is alongside
ZONE_CONFIRM_FRAMES = 3
PATH_HORIZON_S = 2.5              # predict lateral position this far ahead (or TTC, if sooner)
MIN_PATH_SAMPLES = 6              # lateral samples needed before trusting a lateral velocity
PATH_MIN_SIGNIFICANCE = 3.0       # sideways drift must be this many standard errors from zero, else "no drift"

# Overlay only (the corridor band drawn on the image; zone logic doesn't use these)
CAMERA_HEIGHT_M = 1.6             # lens height above the ground: ~1.6 on a helmet, ~0.8 for a table demo
HORIZON_FRAC = 0.5                # horizon row / image height (0.5 = camera level)

# ---------------------------------------------------------------- camera shake
GLOBAL_MOTION = True
MOTION_DOWNSCALE = 4
MOTION_MIN_RESPONSE = 0.05        # phase-correlation peak below this = unreliable estimate
SHAKE_GATE_FRAC = 0.035           # per-frame global shift > 3.5% of width = shaky frame
SHAKE_HOLD_S = 0.5
VP_TRACKING = True                # follow head yaw so "straight behind" stays correct
VP_LEAK_TAU_S = 1.5
VP_MAX_OFFSET_FRAC = 0.35
CUT_MEAN_DIFF = 45.0              # mean grey-level change that counts as a scene cut, not motion

# ---------------------------------------------------------------- alert policy
# Tiers: 0 none, 1 LOW (approaching, far), 2 MEDIUM (blind spot / closing), 3 HIGH (imminent)
CONFIRM_FRAMES = {1: 3, 2: 3, 3: 2}
COOLDOWN_S = {1: 10.0, 2: 4.0, 3: 2.5}   # per-track re-fire of the same tier
SIDE_MIN_GAP_S = 0.6              # min gap between haptic commands on the same side
FAR_DISTANCE_M = 30.0
CLASS_TTC_SCALE = {"truck": 1.3, "bus": 1.3, "car": 1.0, "motorcycle": 1.1, "bicycle": 0.7, "person": 1.0}
LIGHT_HOLD_S = {1: 1.5, 2: 2.5}   # keep ALERT / DANGER light on after the condition clears
LOCAL_SPEECH = True               # short local phrase on HIGH ("Truck left!") - no LLM involved
LOCAL_SPEECH_MIN_GAP_S = 2.5

# Safety floors: no profile and no Gemini tool call can make the system less sensitive than this.
FLOOR_TTC_HIGH_S = 2.0
FLOOR_TTC_MED_S = 3.5

# "Stricter" means stricter about what is worth interrupting you for.
PROFILES = {
    "standard":       {"ttc_high": 2.5, "ttc_med": 4.5, "cooldown_scale": 1.0, "low_haptic": False, "med_needs_close": False},
    "dense_urban":    {"ttc_high": 2.5, "ttc_med": 3.5, "cooldown_scale": 1.5, "low_haptic": False, "med_needs_close": True},
    "fast_open_road": {"ttc_high": 3.0, "ttc_med": 6.0, "cooldown_scale": 0.8, "low_haptic": True,  "med_needs_close": False},
}
DEFAULT_PROFILE = "standard"
PROFILE_MIN_DWELL_S = 60.0        # Gemini can't flip profiles faster than this

# ---------------------------------------------------------------- Gemini (never in the safety path)
GEMINI_ENABLED = True
GEMINI_MODEL = "gemini-3.5-flash-lite"   # fastest current model; try "gemini-3.8-flash" for agent mode
GEMINI_THINKING_LEVEL = "low"     # "minimal" may work on Flash-Lite (not on 3.8 Flash); None = model default
GEMINI_MODE = "describe"          # "describe" = features 1-2 (+scene JSON) | "agent" = tool calling (3-4)
GEMINI_CALL_BUDGET = 150          # hard cap per session
GEMINI_MIN_INTERVAL_S = 3.0       # hard gap between calls; set >= 60 / (your RPM limit in AI Studio)
GEMINI_TIMEOUT_S = 8.0
GEMINI_MAX_WAIT_S = {"threat": 1.5, "query": 8.0, "scene": 20.0}   # pending event expires if not sent by then
GEMINI_MAX_RESULT_AGE_S = {"threat": 4.0, "query": 12.0, "scene": 60.0}  # stale answers are dropped, not spoken
GEMINI_FRAME_SIZE = (320, 240)
GEMINI_JPEG_QUALITY = 55
GEMINI_FRAMES_PER_EVENT = 1       # 2 lets Gemini see motion (turning / pulling over) for ~2x image tokens
GEMINI_MEDIA_RESOLUTION = None    # e.g. "MEDIA_RESOLUTION_LOW" to cut tokens further
GEMINI_FAILS_BEFORE_COOLDOWN = 3
GEMINI_COOLDOWN_S = 30.0
GEMINI_MAX_WORDS = 12
DESCRIBE_TIERS = {3}              # tiers that trigger a threat description
DESCRIBE_MEDIUM_FOR = {"truck", "bus"}   # ...plus MEDIUM alerts for big vehicles
SCENE_STARTUP_DELAY_S = 4.0
SCENE_MIN_GAP_S = 90.0
SCENE_STALE_S = 180.0
VOICE_QUERY_SECONDS = 3.0

# ---------------------------------------------------------------- Arduino link
SERIAL_PORT = None                # None = auto-detect, or "COM5" / "/dev/cu.usbserial-1410"
SERIAL_BAUD = 57600               # must match the sketch
HEARTBEAT_S = 0.25
BUZZERS_ENABLED = True            # buzzers beep with STRONG/FAULT buzzes; False for a quiet demo room
SONAR_MAX_CM = 300                # ultrasonic readings beyond this are treated as "nothing there"

# ---------------------------------------------------------------- ultrasonic fusion (side sensors)
# Which sensors are fitted: name -> (side, degrees angled backward from pointing straight out).
# Names match the firmware (SL = left, SR = right). Add "SR": ("RIGHT", 5.0) when it's wired.
SONAR_MOUNT = {"SL": ("LEFT", 5.0)}
SONAR_OFFSET_M = 0.12             # sensor's distance from the helmet centreline
SONAR_MIN_M = 0.05
SONAR_STATIC_S = 3.0              # an echo that hasn't moved this long is background (wall, backpack)
SONAR_STATIC_TOL_M = 0.15
SONAR_CONTACT_M = 1.5             # an echo the camera never saw raises MED only inside this range
SONAR_CONFIRM_READINGS = 3        # ...and only after this many filtered readings
SONAR_HANDOFF_S = 1.5             # link an echo to a camera track lost from view this recently
SONAR_LOST_S = 0.4                # no echo this long = the contact has passed

# ---------------------------------------------------------------- transparent OLED HUD
HUD_ENABLED = True                # [restart] big directional arrows for MED/HIGH threats
HUD_DRIVER = "ssd1309"            # [restart] 1.51" transparent OLED (Waveshare); "ssd1306" for common 0.96" modules
HUD_INTERFACE = "spi"             # [restart] "spi" (Waveshare default) or "i2c"
HUD_GPIO_DC = 25                  # [restart] SPI only: DC and RST pins (BCM numbers)
HUD_GPIO_RST = 27
HUD_I2C_ADDRESS = 0x3C            # [restart] I2C only
HUD_ROTATE_180 = True             # panel mounted upside down
HUD_MIRROR = False                # True if the rider reads it through the back of the glass
HUD_MIN_TIER = 2                  # 2 = show MED and HIGH, 3 = HIGH only
HUD_HOLD_S = 0.8                  # keep an arrow up this long after the threat clears (no flicker)
HUD_BLINK_HZ = 4.0                # HIGH blinks filled/outline at this rate

# ---------------------------------------------------------------- speech
TTS_ENABLED = True
TTS_RATE = 200                    # words per minute (macOS/Linux); Windows maps to SAPI rate
TTS_PREFIX = ""                   # e.g. "Hey. " if your Bluetooth headset clips the first word

# ---------------------------------------------------------------- overlay
PANEL_WIDTH = 400
DISPLAY_SCALE = 1.0 if IS_PI else 1.3
WINDOW_NAME = "Blind-spot helmet - debug"

# ---------------------------------------------------------------- headless / web view (Pi)
HEADLESS = IS_PI and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
STREAM_PORT = 8080 if IS_PI else None   # overlay + control buttons at http://<pi-ip>:8080 (None = off)
STREAM_FPS = 8                    # web view refresh; costs CPU, keep it low on a Pi
STREAM_JPEG_QUALITY = 70
STREAM_TOKEN = ""                 # set a word to require http://<pi-ip>:8080/?t=<word> for the buttons
STATUS_PRINT_S = 2.0              # console status line when running headless
