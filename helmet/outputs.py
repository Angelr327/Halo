"""Helmet outputs: Arduino serial link, speech and beeps.

Serial protocol (newline-terminated ASCII, see firmware):
  L<n>/R<n>/B<n>  haptic pattern n (1 gentle, 2 medium, 3 strong, 4 fault, 0 stop)
  M<n>            light mode (0 normal, 1 alert, 2 danger) - also the heartbeat
  F1 / F0         host-detected fault on / off
  C<n>            n short chirps (1-3) on both buzzers, no vibration (bench test)
  Z1 / Z0         optional buzzers on / muted (a strong buzz also chirps twice on its side)
Board -> host: READY, BTN, FAILSAFE, LINK OK, U <SL> <SR> <BL> <BR> (ultrasonic cm, -1 = none)
The heartbeat is sent from the vision loop, so if that loop freezes the Arduino's
watchdog trips and the light falls back to a normal flashing bike light.
"""
import itertools
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np

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


BEEP_RATE = 48000
# kind -> (pitch Hz, beeps, seconds on, seconds between): rear is two calm notes, front BRAKE
# three quicker, higher ones, so the rider can tell them apart without looking.
BEEP_SOUNDS = {"rear": (1047, 2, 0.08, 0.07), "front": (1319, 3, 0.06, 0.05)}
BEEP_PAN = {"L": (1.0, 0.15), "R": (0.15, 1.0), "B": (1.0, 1.0)}    # (left ear, right ear)


def beep_clip(kind, side, volume=None, lead_in_s=0.0):
    """Soft sine beeps as float32 stereo samples, panned toward `side` (L, R or B for both)."""
    freq, count, on_s, gap_s = BEEP_SOUNDS[kind]
    volume = cfg.BEEP_VOLUME if volume is None else volume
    t = np.arange(int(on_s * BEEP_RATE)) / BEEP_RATE
    note = np.sin(2 * np.pi * freq * t)
    fade = int(0.008 * BEEP_RATE)                        # 8 ms ramps: no click at either end
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, fade))
    note[:fade] *= ramp
    note[-fade:] *= ramp[::-1]
    gap = np.zeros(int(gap_s * BEEP_RATE))
    mono = np.concatenate([np.zeros(int(lead_in_s * BEEP_RATE))]
                          + [part for _ in range(count) for part in (note, gap)][:-1])
    left, right = BEEP_PAN.get(side, BEEP_PAN["B"])
    return (volume * np.stack([mono * left, mono * right], axis=1)).astype(np.float32)


def _sound_server_running():
    """True when something mixes our stream with speech (PipeWire, PulseAudio, macOS, Windows)."""
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    run = os.environ.get("XDG_RUNTIME_DIR", "")
    return bool(run) and any(os.path.exists(os.path.join(run, n)) for n in ("pipewire-0", "pulse/native"))


class AudioOut:
    """Plays beep clips on the default audio output, the same one speech uses.

    Bluetooth earbuds go to sleep after a few seconds of silence and cut off the start of the
    next sound, which would swallow a 60 ms beep whole. So when a sound server can mix us with
    speech, the stream stays open and plays silence between beeps. Without one, each beep runs
    through a command-line player with a short silent lead-in instead."""

    PLAYERS = ("pw-play", "paplay", "aplay", "afplay")

    def __init__(self):
        self.status = "off"
        failed = ""
        self._clips = {}
        self._queue = deque()
        self._pos = 0
        self._lock = threading.Lock()
        self._stream = None
        self._player = None
        self._files = {}
        if cfg.BEEP_KEEP_AWAKE and _sound_server_running():
            try:
                import sounddevice as sd
                self._stream = sd.OutputStream(samplerate=BEEP_RATE, channels=2, dtype="float32",
                                               callback=self._fill)
                self._stream.start()
                self.status = "default output, kept awake"
                return
            except Exception as e:                        # no sounddevice / PortAudio: use a player
                self._stream = None
                failed = f"stream failed ({type(e).__name__}), "
        if sys.platform.startswith("win"):
            self._player = "winsound"
        else:
            self._player = next((p for p in self.PLAYERS if shutil.which(p)), None)
        self.status = failed + (self._player or "no audio player found (silent)")

    def _fill(self, out, frames, time_info, status):
        out.fill(0)
        done = 0
        with self._lock:
            while done < frames and self._queue:
                clip = self._queue[0]
                n = min(frames - done, len(clip) - self._pos)
                out[done:done + n] = clip[self._pos:self._pos + n]
                done += n
                self._pos += n
                if self._pos >= len(clip):
                    self._queue.popleft()
                    self._pos = 0

    def play(self, kind, side):
        if self._stream is not None:
            clip = self._clips.get((kind, side))
            if clip is None:
                clip = self._clips[(kind, side)] = beep_clip(kind, side)
            with self._lock:
                self._queue.append(clip)                   # plays after any beep still sounding
            return
        if self._player is None:
            return
        path = self._files.get((kind, side))
        if path is None:
            path = self._files[(kind, side)] = self._write_wav(kind, side)
        try:
            if self._player == "winsound":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                subprocess.Popen([self._player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.status = f"{self._player} failed: {type(e).__name__}"
            self._player = None

    def _write_wav(self, kind, side):
        import tempfile
        import wave
        pcm = (beep_clip(kind, side, lead_in_s=cfg.BEEP_LEAD_IN_S) * 32767).astype("<i2")
        fd, path = tempfile.mkstemp(prefix=f"helmet-beep-{kind}-{side}-", suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(BEEP_RATE)
            w.writeframes(pcm.tobytes())
        return path

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        for path in self._files.values():
            try:
                os.remove(path)
            except OSError:
                pass


class Beeper:
    """Short beeps in the rider's earbuds or speaker: two calm notes for a HIGH rear alert,
    panned to its side, and three quicker, higher notes when the front warning turns to BRAKE.

    Cooldowns, so a busy road doesn't turn it into a nag:
      - the same warning (rear left, rear right, rear behind, front) beeps at most every BEEP_REPEAT_S
      - a rear beep is skipped within BEEP_MIN_GAP_S of any other beep
      - a front BRAKE ignores that gap (it plays straight after a rear beep); only its own repeat applies
    A skipped beep is dropped, never played late. The vibration, light and OLED don't depend on it."""

    def __init__(self, out=None):
        self.out = out if out is not None else AudioOut() if cfg.BEEP_ENABLED else None
        self.last = {}
        self.last_any = -1e9
        self.front_state = None
        self.played = deque(maxlen=8)                    # (time, "rear-L" / "front"), newest last

    @property
    def status(self):
        return self.out.status if self.out is not None else "off"

    def rear(self, tier, side, now=None):
        """Call with every rear alert that fires (risk.Fire tier and side)."""
        return tier in cfg.BEEP_TIERS and self._beep("rear", side, now, urgent=False)

    def front(self, collision, now=None):
        """Call every loop iteration with hud.collision; beeps when BRAKE starts."""
        state = (collision or {}).get("state")
        entered = state == "BRAKE" and self.front_state != "BRAKE"
        self.front_state = state
        return entered and cfg.BEEP_FRONT_BRAKE and self._beep("front", "B", now, urgent=True)

    def _beep(self, kind, side, now, urgent):
        if not cfg.BEEP_ENABLED:
            return False
        now = time.monotonic() if now is None else now
        key = "front" if kind == "front" else f"rear-{side}"
        if now - self.last.get(key, -1e9) < cfg.BEEP_REPEAT_S:
            return False
        if not urgent and now - self.last_any < cfg.BEEP_MIN_GAP_S:
            return False
        self.last[key] = self.last_any = now
        self.played.append((now, key))
        if self.out is not None:
            self.out.play(kind, side)
        return True

    def test(self, gap_s=0.8):
        """Rear left, rear right, then front BRAKE, ignoring the cooldowns (checks the earbuds)."""
        if self.out is None:
            return

        def run():
            for kind, side in (("rear", "L"), ("rear", "R"), ("front", "B")):
                self.out.play(kind, side)
                time.sleep(gap_s)
        threading.Thread(target=run, daemon=True).start()

    def close(self):
        if self.out is not None:
            self.out.close()


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
