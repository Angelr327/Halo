"""The ONLY way the app talks to Gemini.

Rules this file enforces
- Never in the safety path: submit() returns instantly; calls run on a worker thread;
  results come back through a queue the main loop drains when it has time.
- Event-driven: callers submit events (threat, query, scene). There is no timer loop.
- Hard call budget + hard minimum interval between calls.
- One call in flight, one pending slot. A newer event replaces an older pending one
  (only the latest matters); a rider's question outranks automatic events.
- Pending events expire, and answers that arrive too late are dropped, not spoken.
- Circuit breaker: repeated failures pause Gemini; the helmet keeps working locally.
- Frames are downscaled (320x240) and JPEG-compressed before sending.
"""
import io
import json
import os
import queue
import threading
import time
import wave
from collections import Counter
from dataclasses import dataclass, field

import cv2

from . import config as cfg

try:
    from google import genai
    from google.genai import types
except ImportError:          # the helmet must still run without the SDK
    genai = types = None


SYSTEM_PROMPT = """You are the voice of a cyclist's rear-view safety helmet.
Images come from a rear-facing helmet camera and are MIRRORED like a rear-view mirror:
the image's left is the rider's LEFT (the edges are labelled). A local detector has already
handled every urgent alert with vibration and lights; you add brief context a buzz can't give.
Rules:
1. Plain spoken words only. No preamble, no emoji, no markdown.
2. Tracker facts are authoritative for side, distance and timing. Never contradict them.
3. Useful detail: vehicle type and size (box truck, city bus, pickup towing a trailer, e-scooter),
   side, and behaviour (passing close, overtaking another car, slowing, turning, pulling over,
   signalling, stopping).
4. Never say or imply that it is safe, clear, or okay to move or merge. Describe only what you see.
5. If the image is unclear, say less rather than guess."""

TOOL_DECLS = [
    {"name": "alert_rider_voice",
     "description": "Speak a short message to the rider through bone-conduction audio.",
     "parameters_json_schema": {"type": "object", "properties": {
         "message": {"type": "string", "description": "At most 12 words."}}, "required": ["message"]}},
    {"name": "escalate_rear_light",
     "description": "Temporarily make the rear light more conspicuous to warn the driver behind. "
                    "It can only raise the light above what the local system chose, never lower it.",
     "parameters_json_schema": {"type": "object", "properties": {
         "level": {"type": "string", "enum": ["alert", "danger"]},
         "seconds": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": ["level", "seconds"]}},
    {"name": "adjust_sensitivity",
     "description": "Switch the alert profile to fit the environment. dense_urban: filter routine "
                    "passes to avoid alert fatigue. standard: default. fast_open_road: earlier warnings. "
                    "Emergency alerts are unaffected.",
     "parameters_json_schema": {"type": "object", "properties": {
         "profile": {"type": "string", "enum": ["dense_urban", "standard", "fast_open_road"]},
         "reason": {"type": "string"}}, "required": ["profile", "reason"]}},
    {"name": "describe_scene",
     "description": "Record a one-line description of the scene for the dashboard and trip log. Not spoken.",
     "parameters_json_schema": {"type": "object", "properties": {
         "summary": {"type": "string"}}, "required": ["summary"]}},
]

SCENE_SCHEMA = {"type": "object", "properties": {
    "environment": {"type": "string", "enum": ["dense_urban", "bike_lane", "suburban", "fast_open_road", "unclear"]},
    "traffic": {"type": "string", "enum": ["light", "moderate", "heavy"]},
    "summary": {"type": "string"}}, "required": ["environment", "traffic", "summary"]}

ENV_TO_PROFILE = {"dense_urban": "dense_urban", "bike_lane": "standard",
                  "suburban": "standard", "fast_open_road": "fast_open_road"}


@dataclass
class GeminiEvent:
    kind: str                      # "threat" | "query" | "scene"
    frames: list                   # BGR frames, oldest first, already annotated
    facts: str                     # local tracker facts (authoritative)
    question: str = ""
    audio_wav: bytes = None
    priority: int = 1              # 0 = rider's own question (outranks automatic events)
    created: float = field(default_factory=time.monotonic)


@dataclass
class GeminiAction:
    kind: str                      # "speak" | "light" | "profile" | "scene_note"
    args: dict
    event_kind: str
    created: float
    latency: float


def prepare_frame(frame, box=None):
    """Copy + red box around the target + edge labels so Gemini knows which side is which."""
    img = frame.copy()
    h, w = img.shape[:2]
    if box is not None:
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), max(3, w // 160))
    fs = w / 640 * 0.9
    for text, x in (("LEFT", 8), ("RIGHT", w - int(95 * w / 640))):
        cv2.putText(img, text, (x, h - 12), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, text, (x, h - 12), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def describe_track(tr):
    side = {"LEFT": "rider's left", "RIGHT": "rider's right", "CENTER": "directly behind"}.get(tr.zone, "behind")
    bits = [f"{tr.label} on the {side}"]
    if tr.dist_m:
        bits.append(f"~{tr.dist_m:.0f} m away")
    if tr.ttc is not None and tr.ttc < 30:
        bits.append(f"reaches rider in ~{tr.ttc:.1f} s")
    if tr.clearance_m is not None and tr.zone != "CENTER":
        bits.append(f"passing clearance ~{max(0.0, tr.clearance_m):.1f} m")
    if tr.alongside:
        bits.append("now alongside")
    return ", ".join(bits)


def facts_for(tracks, target=None):
    parts = []
    if target is not None:
        parts.append("TARGET (red box): " + describe_track(target))
    others = [tr for tr in tracks if tr is not target and tr.matched_now][:4]
    if others:
        parts.append("others: " + "; ".join(describe_track(tr) for tr in others))
    return " | ".join(parts) if parts else "no vehicles tracked"


def record_wav(seconds, samplerate=16000):
    """Push-to-talk capture from the laptop mic (keep BT headphones output-only)."""
    import sounddevice as sd
    audio = sd.rec(int(seconds * samplerate), samplerate=samplerate, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


class GeminiGateway:
    def __init__(self, client=None):
        self.results = queue.Queue()
        self.calls = 0
        self.rejected = Counter()
        self.fails_in_row = 0
        self.cooldown_until = 0.0
        self.last_call_t = -1e9
        self.in_flight = False
        self.pending = None
        self.last_latency = None
        self.last_text = ""
        self.status = "idle"
        self.user_enabled = cfg.GEMINI_ENABLED
        self._thinking_ok = True
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.client = client
        if self.client is None and genai is not None and cfg.GEMINI_ENABLED:
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if key:
                self.client = genai.Client(api_key=key, http_options=types.HttpOptions(
                    timeout=int(cfg.GEMINI_TIMEOUT_S * 1000)))
        if self.client is None:
            self.status = "no API key or SDK: local-only"
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def available(self):
        return self.client is not None and self.user_enabled

    # ------------------------------------------------------------ public API
    def submit(self, ev):
        """Non-blocking. Returns 'queued' or the reason it was rejected."""
        now = time.monotonic()
        with self._lock:
            if not self.available:
                reason = "disabled"
            elif now < self.cooldown_until:
                reason = "circuit open"
            elif self.calls + (1 if self.in_flight else 0) >= cfg.GEMINI_CALL_BUDGET:
                reason = "budget"
            elif self.pending is not None and self.pending.priority < ev.priority:
                reason = "busy"
            else:
                if self.pending is not None:
                    self.rejected["replaced"] += 1
                self.pending = ev
                self._wake.set()
                return "queued"
            self.rejected[reason] += 1
            return reason

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                return out

    # ------------------------------------------------------------ worker
    def _worker(self):
        while True:
            self._wake.wait()
            with self._lock:
                ev = self.pending
                if ev is None:
                    self._wake.clear()
                    continue
                now = time.monotonic()
                wait = self.last_call_t + cfg.GEMINI_MIN_INTERVAL_S - now
                if wait <= 0:
                    self.pending = None
                    if now - ev.created > cfg.GEMINI_MAX_WAIT_S.get(ev.kind, 2.0):
                        self.rejected["expired"] += 1
                        continue
                    if (self.calls >= cfg.GEMINI_CALL_BUDGET or not self.available
                            or now < self.cooldown_until):
                        self.rejected["dropped"] += 1
                        continue
                    self.calls += 1
                    self.in_flight = True
                    self.last_call_t = now
            if wait > 0:
                time.sleep(min(wait, 0.05))
                continue
            try:
                self._execute(ev)
            finally:
                with self._lock:
                    self.in_flight = False

    def _execute(self, ev):
        t0 = time.monotonic()
        self.status = f"calling ({ev.kind})"
        try:
            try:
                resp = self._call(ev)
            except Exception as e:
                if self._thinking_ok and "thinking" in str(e).lower():
                    self._thinking_ok = False           # model rejects our thinking level: retry without
                    resp = self._call(ev)
                else:
                    raise
            latency = time.monotonic() - t0
            actions = self._parse(ev, resp)
            with self._lock:
                self.fails_in_row = 0
                self.last_latency = latency
                self.status = f"ok ({ev.kind}) {latency:.1f}s"
            for kind, args in actions:
                self.results.put(GeminiAction(kind, args, ev.kind, ev.created, latency))
        except Exception as e:
            with self._lock:
                self.fails_in_row += 1
                self.status = f"error {type(e).__name__}: {str(e)[:70]}"
                if self.fails_in_row >= cfg.GEMINI_FAILS_BEFORE_COOLDOWN:
                    self.cooldown_until = time.monotonic() + cfg.GEMINI_COOLDOWN_S
                    self.fails_in_row = 0
                    self.status += f" | paused {cfg.GEMINI_COOLDOWN_S:.0f}s, local-only"
            if ev.kind == "query":
                self.results.put(GeminiAction("speak", {"text": "Description unavailable right now."},
                                              ev.kind, ev.created, time.monotonic() - t0))

    def _call(self, ev):
        contents, config = self._build(ev)
        return self.client.models.generate_content(model=cfg.GEMINI_MODEL, contents=contents, config=config)

    # ------------------------------------------------------------ request / response
    @staticmethod
    def _jpeg(frame):
        small = cv2.resize(frame, tuple(cfg.GEMINI_FRAME_SIZE), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, int(cfg.GEMINI_JPEG_QUALITY)])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return buf.tobytes()

    def _task_text(self, ev):
        agent = cfg.GEMINI_MODE == "agent"
        n = cfg.GEMINI_MAX_WORDS
        if ev.kind == "threat":
            task = ("The local system JUST warned the rider about the vehicle in the RED box. "
                    f"In at most {n} words, tell the rider what it is and what it is doing.")
            if agent:
                task += (" Call alert_rider_voice with that phrase. Also call escalate_rear_light "
                         "if the driver does not appear to be slowing or moving over.")
        elif ev.kind == "query":
            q = ev.question or "(the rider's question is in the attached audio)"
            task = f"The rider asked: {q}\nAnswer in one sentence of at most {n} words about the scene behind them."
            if agent:
                task += " Answer by calling alert_rider_voice."
        else:
            if agent:
                task = ("Classify the road environment behind the rider. Call adjust_sensitivity with the best "
                        "profile and describe_scene with a one-line summary.")
            else:
                task = "Classify the road environment behind the rider and summarise it in under 12 words."
        return f"Tracker facts (authoritative): {ev.facts}\n\n{task}"

    def _build(self, ev):
        parts = []
        for f in ev.frames[-max(1, cfg.GEMINI_FRAMES_PER_EVENT):]:
            kw = {"media_resolution": cfg.GEMINI_MEDIA_RESOLUTION} if cfg.GEMINI_MEDIA_RESOLUTION else {}
            parts.append(types.Part.from_bytes(data=self._jpeg(f), mime_type="image/jpeg", **kw))
        if ev.audio_wav:
            parts.append(types.Part.from_bytes(data=ev.audio_wav, mime_type="audio/wav"))
        parts.append(types.Part.from_text(text=self._task_text(ev)))

        conf = {"system_instruction": SYSTEM_PROMPT, "max_output_tokens": 512}
        if self._thinking_ok and cfg.GEMINI_THINKING_LEVEL:
            conf["thinking_config"] = types.ThinkingConfig(thinking_level=cfg.GEMINI_THINKING_LEVEL)
        if cfg.GEMINI_MODE == "agent":
            conf["tools"] = [types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in TOOL_DECLS])]
            conf["tool_config"] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))
            conf["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        elif ev.kind == "scene":
            conf["response_mime_type"] = "application/json"
            conf["response_json_schema"] = SCENE_SCHEMA
        return [types.Content(role="user", parts=parts)], types.GenerateContentConfig(**conf)

    def _parse(self, ev, resp):
        actions = []
        calls = resp.function_calls or []
        for fc in calls:
            a = dict(fc.args or {})
            if fc.name == "alert_rider_voice":
                actions.append(("speak", {"text": a.get("message", "")}))
            elif fc.name == "escalate_rear_light":
                actions.append(("light", {"level": a.get("level", "alert"), "seconds": a.get("seconds", 3)}))
            elif fc.name == "adjust_sensitivity":
                actions.append(("profile", {"profile": a.get("profile"), "reason": a.get("reason", "")}))
            elif fc.name == "describe_scene":
                actions.append(("scene_note", {"text": a.get("summary", "")}))
        if not calls:
            text = (resp.text or "").strip()
            if ev.kind == "scene" and text:
                try:
                    data = json.loads(text)
                    prof = ENV_TO_PROFILE.get(data.get("environment"))
                    if prof:
                        actions.append(("profile", {"profile": prof,
                                                    "reason": f"{data.get('environment')}, {data.get('traffic')} traffic"}))
                    actions.append(("scene_note", {"text": data.get("summary", "")}))
                except (json.JSONDecodeError, AttributeError):
                    actions.append(("scene_note", {"text": text[:80]}))
            elif ev.kind == "scene":
                pass
            elif text:
                actions.append(("speak", {"text": text}))
        self.last_text = " | ".join(f"{k}:{list(v.values())[0]}" for k, v in actions)[:160]
        return actions
