"""Helmet outputs: Arduino serial link and speech.

Serial protocol (newline-terminated ASCII, see firmware):
  L<n>/R<n>/B<n>  haptic pattern n (1 gentle, 2 medium, 3 strong, 4 fault, 0 stop)
  M<n>            light mode (0 normal, 1 alert, 2 danger) - also the heartbeat
  F1 / F0         host-detected fault on / off
  C<n>            n short chirps (1-3) on both buzzers, no vibration (front BRAKE)
  Z1 / Z0         buzzers on / muted (a strong buzz also chirps twice on its side)
Board -> host: READY, BTN, FAILSAFE, LINK OK, U <SL> <SR> <BL> <BR> (ultrasonic cm, -1 = none)
The heartbeat is sent from the vision loop, so if that loop freezes the Arduino's
watchdog trips and the light falls back to a normal flashing bike light.
"""
import itertools
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

from . import config as cfg

LIGHT_NAMES = {0: "NORMAL", 1: "ALERT", 2: "DANGER"}
SONAR_NAMES = ("SL", "SR", "BL", "BR")   # side-left, side-right, back-left, back-right
PORT_HINTS = ("arduino", "ch340", "ch34", "wch", "usb serial", "usb-serial", "usbserial",
              "usbmodem", "ttyusb", "ttyacm", "cp210", "ftdi", "nano")


class HelmetLink:
    def __init__(self, port=None):
        self.requested_port = port or cfg.SERIAL_PORT
        self.ser = None
        self.port = None
        self.status = "no Arduino (simulated)"
        self.light = 0
        self.last_hb = 0.0
        self.last_retry = 0.0
        self.heartbeat_enabled = True
        self.was_connected = False
        self.tx_log = deque(maxlen=6)
        self.rx_log = deque(maxlen=4)
        self.motor_flash = {"L": -1e9, "R": -1e9}      # for the on-screen motor indicators
        self.sonar = dict.fromkeys(SONAR_NAMES)          # metres, None = no echo / no data
        self.sonar_t = -1e9
        self.sonar_hist = deque(maxlen=64)               # (monotonic time, readings) for fusion
        self._rx_buf = b""
        self._connect()

    def _connect(self):
        try:
            import serial
            import serial.tools.list_ports as lp
        except ImportError:
            self.status = "pyserial missing (simulated)"
            return
        port = self.requested_port
        if not port:
            for p in lp.comports():
                desc = f"{p.device} {p.description} {p.manufacturer} {p.hwid}".lower()
                if any(h in desc for h in PORT_HINTS):
                    port = p.device
                    break
        if not port:
            self.status = "no Arduino found (simulated)"
            return
        try:
            self.ser = serial.Serial(port, cfg.SERIAL_BAUD, timeout=0, write_timeout=0.05)
            self.port = port
            deadline = time.monotonic() + 3.0            # opening the port resets the Nano
            while time.monotonic() < deadline:
                self.poll()
                if any("READY" in line for line in self.rx_log):
                    break
                time.sleep(0.05)
            self.status = f"Arduino on {port}"
            self.was_connected = True
            self.send("Z1" if cfg.BUZZERS_ENABLED else "Z0")
        except Exception as e:
            self.ser = None
            self.status = f"serial error: {type(e).__name__} (simulated)"

    def send(self, cmd, now=None):
        now = time.monotonic() if now is None else now
        if not cmd.startswith("M"):
            self.tx_log.append(cmd)
        if self.ser is None:
            return
        try:
            self.ser.write((cmd + "\n").encode("ascii"))
        except Exception as e:
            self.status = f"serial lost: {type(e).__name__}"
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def buzz(self, side, pattern, now=None):
        now = time.monotonic() if now is None else now
        self.send(f"{side}{pattern}", now)
        for s in (("L", "R") if side == "B" else (side,)):
            self.motor_flash[s] = now

    def chirp(self, n, now=None):
        self.send(f"C{max(0, min(3, int(n)))}", now)       # the firmware plays 1-3

    def set_fault(self, on):
        self.send("F1" if on else "F0")

    def tick(self, light_level):
        """Call every loop iteration: heartbeat + light state + reconnect + read."""
        now = time.monotonic()
        self.light = light_level
        if self.heartbeat_enabled and now - self.last_hb >= cfg.HEARTBEAT_S:
            self.send(f"M{light_level}", now)
            self.last_hb = now
        if self.ser is None and self.was_connected and now - self.last_retry > 3.0:
            self.last_retry = now
            self._connect()
        return self.poll()

    def poll(self):
        """Returns board events such as 'BTN'."""
        events = []
        if self.ser is None:
            return events
        try:
            n = self.ser.in_waiting
            if n:
                self._rx_buf += self.ser.read(n)
        except Exception:
            return events
        while b"\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split(b"\n", 1)
            text = line.decode("ascii", "ignore").strip()
            if text.startswith("U "):                     # ultrasonic report, ~8/s: not logged
                self._parse_sonar(text)
                continue
            if text:
                self.rx_log.append(text)
                if text == "BTN":
                    events.append("BTN")
        return events

    def _parse_sonar(self, text):
        try:
            cms = [int(v) for v in text.split()[1:]]
        except ValueError:
            return
        if len(cms) == len(SONAR_NAMES):
            self.inject_sonar({n: (cm / 100.0 if 2 <= cm <= cfg.SONAR_MAX_CM else None)
                               for n, cm in zip(SONAR_NAMES, cms)})

    def inject_sonar(self, readings, ts=None):
        """Record one set of ultrasonic readings (metres or None). The simulator calls this too."""
        self.sonar = dict(readings)
        self.sonar_t = time.monotonic() if ts is None else ts
        self.sonar_hist.append((self.sonar_t, self.sonar))

    def sonar_fresh(self):
        return time.monotonic() - self.sonar_t < 0.5

    def close(self):
        if self.ser is not None:
            try:
                self.send("B0")
                self.send("M0")
                self.ser.close()
            except Exception:
                pass


class FrontChirp:
    """Chirps both buzzers once when the front warning turns to BRAKE.
    Call every loop iteration with hud.collision; a flickering BRAKE chirps at most once per
    FRONT_CHIRP_GAP_S."""

    def __init__(self):
        self.last_state = None
        self.last_t = -1e9

    def update(self, collision, link, now):
        state = (collision or {}).get("state")
        entered = state == "BRAKE" and self.last_state != "BRAKE"
        self.last_state = state
        if not entered or cfg.FRONT_BRAKE_CHIRPS <= 0 or now - self.last_t < cfg.FRONT_CHIRP_GAP_S:
            return False
        self.last_t = now
        link.chirp(cfg.FRONT_BRAKE_CHIRPS, now)
        return True


class Speaker:
    """Non-blocking TTS with priorities. 0 = urgent local alert, 1 = Gemini context, 2 = info.
    Stale messages are dropped instead of spoken late."""

    def __init__(self):
        self.q = queue.PriorityQueue()
        self._seq = itertools.count()
        self.captions = deque(maxlen=4)
        self.backend_name = "none"
        if cfg.TTS_ENABLED:
            threading.Thread(target=self._run, daemon=True).start()

    def say(self, text, priority=1, max_age=4.0, source="local"):
        if not text:
            return
        self.captions.append((time.monotonic(), source, text))
        if cfg.TTS_ENABLED:
            self.q.put((priority, next(self._seq), time.monotonic(), max_age, text))

    def _make_backend(self):
        rate = cfg.TTS_RATE
        if sys.platform == "darwin" and shutil.which("say"):
            self.backend_name = "say"
            return lambda s: subprocess.run(["say", "-r", str(rate), s], check=False)
        if sys.platform.startswith("win"):
            try:
                import comtypes.client
                comtypes.CoInitialize()
                voice = comtypes.client.CreateObject("SAPI.SpVoice")
                voice.Rate = max(-10, min(10, int((rate - 180) / 20)))
                self.backend_name = "SAPI"
                return lambda s: voice.Speak(s)
            except Exception:
                pass
        for exe, args in (("spd-say", ["-w"]), ("espeak-ng", ["-s", str(rate)]), ("espeak", ["-s", str(rate)])):
            if shutil.which(exe):
                self.backend_name = exe
                return lambda s, e=exe, a=args: subprocess.run([e, *a, s], check=False)
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", rate)
            self.backend_name = "pyttsx3"

            def speak(s):
                engine.say(s)
                engine.runAndWait()
            return speak
        except Exception:
            self.backend_name = "print"
            return lambda s: print(f"[TTS] {s}")

    def _run(self):
        speak = self._make_backend()        # created inside this thread (COM needs that on Windows)
        while True:
            _, _, created, max_age, text = self.q.get()
            if time.monotonic() - created > max_age:
                continue
            try:
                speak(cfg.TTS_PREFIX + text)
            except Exception as e:
                print(f"[TTS error] {e}: {text}")
