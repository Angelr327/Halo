"""Blind-spot helmet main loop.

    python -m helmet.main                       # live camera
    python -m helmet.main --video clip.mp4      # recorded footage, real-time paced
    python -m helmet.main --demo-person         # teammates walking count as vehicles
    python -m helmet.main --log run.csv         # per-frame metrics for offline calibration

Order of work every frame (the fast path never waits on the network):
  capture -> mirror -> global motion -> YOLO -> tracker -> metrics -> policy
  -> haptics/light/local speech  -> [submit Gemini event, non-blocking]
  -> drain Gemini results through guardrails -> overlay
"""
import argparse
import csv
import importlib
import math
import os
import re
import socket
import threading
import time
from collections import deque

import cv2

from . import config as cfg
from .gemini_gateway import GeminiEvent, GeminiGateway, facts_for, prepare_frame, record_wav
from .hud import Hud
from . import snapshot
from .outputs import HelmetLink, Speaker
from .overlay import Overlay
from .perception import CENTER, LEFT, RIGHT, GlobalMotion, Tracker, VehicleDetector, update_metrics
from .risk import SPOKEN_LABEL, AlertPolicy, SceneTrigger
from .sources import VideoSource, open_camera
from .streamer import Streamer


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_args():
    ap = argparse.ArgumentParser(description="Blind-spot detection helmet")
    ap.add_argument("--video", help="recorded footage instead of the live camera")
    ap.add_argument("--no-realtime", action="store_true", help="process every video frame (slower than real time)")
    ap.add_argument("--loop", action="store_true", help="loop the video")
    ap.add_argument("--camera", type=int, default=None, help="camera index")
    ap.add_argument("--port", help="Arduino serial port (default: auto-detect)")
    ap.add_argument("--no-gemini", action="store_true")
    ap.add_argument("--no-hud", action="store_true", help="don't drive the transparent OLED")
    ap.add_argument("--sim", action="store_true", help="scripted traffic instead of the camera (no YOLO); web view on")
    ap.add_argument("--agent", action="store_true", help="Gemini tool-calling mode")
    ap.add_argument("--demo-person", action="store_true", help="count people as vehicles (stationary demo)")
    ap.add_argument("--model", help="YOLO weights, e.g. yolo26n.pt")
    ap.add_argument("--log", help="write per-frame track metrics to this CSV")
    ap.add_argument("--headless", action="store_true", default=None, help="no OpenCV window (default on a Pi without a desktop)")
    ap.add_argument("--window", action="store_true", help="force the OpenCV window even on a Pi")
    ap.add_argument("--stream", type=int, default=None, help="web view port (0 = off; default 8080 on a Pi)")
    ap.add_argument("--camera-source", choices=["auto", "usb", "picamera2"], help="camera type")
    return ap.parse_args()


def apply_overrides(args):
    if args.no_gemini:
        cfg.GEMINI_ENABLED = False
    if args.agent:
        cfg.GEMINI_MODE = "agent"
    if args.demo_person:
        cfg.DEMO_PERSON_AS_VEHICLE = True
    if args.model:
        cfg.MODEL_PATH = args.model
    if args.headless:
        cfg.HEADLESS = True
    if args.window:
        cfg.HEADLESS = False
    if args.stream is not None:
        cfg.STREAM_PORT = args.stream or None
    if args.camera_source:
        cfg.CAMERA_SOURCE = args.camera_source
    if args.sim and args.stream is None and not cfg.STREAM_PORT:
        cfg.STREAM_PORT = 8080                  # the simulator is for the web view: always serve it


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))      # no packet is sent; just picks the outbound interface
            return sk.getsockname()[0]
    except OSError:
        return "localhost"


def clean_speech(text):
    text = re.sub(r"[*_#`>\[\]]", "", text or "").strip().strip('"')
    words = text.split()
    return " ".join(words[: cfg.GEMINI_MAX_WORDS + 3])


def local_summary(tracks):
    """Answer 'what's behind me?' with no network at all."""
    live = [tr for tr in tracks if tr.matched_now]
    if not live:
        return "Nothing detected behind."
    live.sort(key=lambda tr: tr.dist_m if tr.dist_m else 999)
    side = {LEFT: "on your left", RIGHT: "on your right", CENTER: "behind you"}
    parts = []
    for tr in live[:2]:
        s = f"{SPOKEN_LABEL.get(tr.label, 'Vehicle')} {side.get(tr.zone, 'behind you')}"
        if tr.dist_m:
            s += f", about {tr.dist_m:.0f} meters"
        parts.append(s)
    return ". ".join(parts) + "."


class State:
    def __init__(self):
        self.t = 0.0
        self.last_frame = None
        self.frames = deque(maxlen=3)
        self.fault = False
        self.sim_fault = False
        self.light = 0
        self.light_override = None
        self.fps = 0.0
        self.last_loop = time.monotonic()
        self.age_ms = 0.0
        self.writer = None
        self.writer_size = None
        self.scene_note = ""
        self.voice = {"busy": False, "wav": None, "err": None}
        self.scheduled = []
        self.last_status = 0.0


def main():
    args = parse_args()
    load_dotenv()
    apply_overrides(args)

    if args.sim:
        from .sim import SimDetector, SimSource
        source = SimSource()
        detector = SimDetector(source)
    else:
        source = VideoSource(args.video, realtime=not args.no_realtime, loop=args.loop) if args.video \
            else open_camera(args.camera)
        print("Loading YOLO...")
        detector = VehicleDetector()
    motion, tracker, policy, scene = GlobalMotion(), Tracker(), AlertPolicy(), SceneTrigger()
    link, speaker, gateway, overlay = HelmetLink(args.port), Speaker(), GeminiGateway(), Overlay()
    hud = Hud(enabled=not args.no_hud)
    st = State()
    headless = cfg.HEADLESS
    streamer = Streamer(cfg.STREAM_PORT) if cfg.STREAM_PORT else None
    print(f"Detector: {detector.model_name} @ {cfg.IMGSZ} | Camera: {type(source).__name__} | "
          f"Serial: {link.status} | Gemini: {gateway.status if not gateway.available else cfg.GEMINI_MODEL}")
    print(f"HUD: {hud.status}")
    if streamer:
        tok = f"?t={cfg.STREAM_TOKEN}" if cfg.STREAM_TOKEN else ""
        print(f"Web view: http://{local_ip()}:{cfg.STREAM_PORT}/{tok}")
        print(f"2.5D view: http://{local_ip()}:{cfg.STREAM_PORT}/view{tok}")
    if headless and not streamer:
        print("Headless with no web view: status prints only. Ctrl+C to stop.")

    log_file = open(args.log, "w", newline="") if args.log else None
    log = csv.writer(log_file) if log_file else None
    if log:
        log.writerow(["t", "id", "label", "conf", "x1", "y1", "x2", "y2", "area", "growth", "consistency",
                      "ttc", "lat_m", "clearance_m", "dist_m", "zone", "alongside", "tier", "reason",
                      "shaky", "gdx", "gdy", "vp_offset", "scale_axis", "lat_v", "pred_lat_m", "on_path"])

    if not headless:
        cv2.namedWindow(cfg.WINDOW_NAME, cv2.WINDOW_NORMAL)

    # ------------------------------------------------------------ helpers
    def ask(question="What's behind me?", audio=None):
        if st.last_frame is None:
            return
        if not gateway.available:
            speaker.say(local_summary(tracker.tracks), priority=1, max_age=5, source="local")
            return
        ev = GeminiEvent("query", frames=[prepare_frame(st.last_frame)], facts=facts_for(tracker.tracks),
                         question=question if audio is None else "", audio_wav=audio, priority=0)
        if gateway.submit(ev) != "queued":
            speaker.say(local_summary(tracker.tracks), priority=1, max_age=5, source="local")

    def start_voice():
        if st.voice["busy"]:
            return
        st.voice.update(busy=True, wav=None, err=None)
        speaker.say("Listening.", priority=2, max_age=2, source="system")

        def run():
            try:
                st.voice["wav"] = record_wav(cfg.VOICE_QUERY_SECONDS)
            except Exception as e:
                st.voice["err"] = f"{type(e).__name__}: {e}"
            st.voice["busy"] = False
        threading.Thread(target=run, daemon=True).start()

    def execute_fire(f):
        link.buzz(f.side, f.tier)
        print(f"[ALERT] t={f.t:.2f} #{f.track_id} {f.label} {f.zone} tier={f.tier} ({f.reason})")
        if f.tier == 3 and policy.may_speak_local(f.t):
            speaker.say(f.phrase(), priority=0, max_age=1.5, source="local")
        wants = f.tier in cfg.DESCRIBE_TIERS or (f.tier == 2 and f.label in cfg.DESCRIBE_MEDIUM_FOR)
        if wants and gateway.available:
            tr = next((x for x in tracker.tracks if x.id == f.track_id), None)
            if tr is None:
                return
            box = (tr.det.x1, tr.det.y1, tr.det.x2, tr.det.y2)
            recent = list(st.frames)[-max(1, cfg.GEMINI_FRAMES_PER_EVENT):]
            frames = [prepare_frame(fr, box if i == len(recent) - 1 else None) for i, fr in enumerate(recent)]
            gateway.submit(GeminiEvent("threat", frames=frames, facts=facts_for(tracker.tracks, tr), priority=1))

    def apply_gemini():
        for a in gateway.drain():
            if time.monotonic() - a.created > cfg.GEMINI_MAX_RESULT_AGE_S.get(a.event_kind, 5.0):
                print(f"[gemini] dropped stale {a.kind} ({a.event_kind})")
                continue
            if a.kind == "speak":
                speaker.say(clean_speech(a.args.get("text")), priority=1, max_age=3.0, source="gemini")
            elif a.kind == "light" and not st.fault:
                lvl, secs = policy.raise_light(a.args.get("level"), a.args.get("seconds", 3), st.t)
                print(f"[gemini] light -> {lvl} for {secs:.0f}s")
            elif a.kind == "profile":
                if policy.set_profile(a.args.get("profile"), st.t, force=(a.event_kind == "query")):
                    print(f"[gemini] profile -> {policy.profile_name}: {a.args.get('reason')}")
            elif a.kind == "scene_note":
                st.scene_note = (a.args.get("text") or "")[:60]

    def set_fault(on):
        st.fault = on
        link.set_fault(on)
        if on:
            tracker.tracks.clear()
            motion.prev = None
            speaker.say("Rear camera offline.", priority=0, max_age=5, source="system")
        else:
            speaker.say("Rear camera back.", priority=2, max_age=3, source="system")

    def show(frame):
        if headless and not (streamer and streamer.wants_frames()) and st.writer is None:
            return                                   # nobody is looking: skip drawing, save CPU
        canvas = overlay.render(frame, {
            "tracks": tracker.tracks, "motion": motion, "vp_x": motion.vp_x(frame.shape[1]),
            "fps": st.fps, "det_ms": detector.last_ms, "age_ms": st.age_ms, "link": link,
            "policy": policy, "gateway": gateway, "speaker": speaker, "light": st.light,
            "fault": st.fault, "shaky": motion.shaky(st.t), "recording": st.writer is not None,
            "paused": getattr(source, "paused", False), "scene_note": st.scene_note, "hud": hud.preview})
        if st.writer is not None:
            st.writer.write(cv2.resize(canvas, st.writer_size))
        if streamer:
            streamer.publish(canvas)
        if not headless:
            cv2.imshow(cfg.WINDOW_NAME, canvas)

    last_snap = [0.0]

    def publish_snapshot(now, shaky=False):
        if streamer is None or now - last_snap[0] < 0.06:         # every frame, at most ~15 per second
            return
        last_snap[0] = now
        streamer.publish_state(snapshot.build(
            tracker.tracks, st.t, hud_state=hud.state, fault=st.fault, shaky=shaky, light=st.light,
            fps=st.fps, det_ms=detector.last_ms, link=link, profile=policy.profile_name,
            captions=speaker.captions, scene=getattr(source, "title", None)))

    def poll_key():
        k = cv2.waitKey(1) & 0xFF if not headless else 255
        if k == 255 and streamer:
            k = streamer.get_key()
        elif headless:
            time.sleep(0.001)
        return k

    def status_line(now):
        if headless and now - st.last_status >= cfg.STATUS_PRINT_S:
            st.last_status = now
            live = sum(1 for tr in tracker.tracks if tr.matched_now)
            top = max((tr.tier for tr in tracker.tracks), default=0)
            print(f"[status] {st.fps:4.1f} FPS  det {detector.last_ms:3.0f} ms  vehicles {live}  max tier {top}  "
                  f"light {st.light}  profile {policy.profile_name}  serial: {link.status}  "
                  f"gemini {gateway.calls}/{cfg.GEMINI_CALL_BUDGET} {gateway.status}"
                  + ("  CAMERA FAULT" if st.fault else ""))

    def handle_key(key):
        nonlocal_now = time.monotonic()
        if key in (ord("q"), 27):
            return False
        if key == ord(" "):
            source.toggle_pause()
        elif key == ord("g"):
            ask()
        elif key == ord("v"):
            start_voice()
        elif key == ord("t"):
            st.scheduled += [(nonlocal_now, "L2"), (nonlocal_now + 0.8, "R2"), (nonlocal_now + 1.6, "B3")]
            speaker.say("Left. Right. Both.", priority=2, max_age=3, source="system")
        elif key == ord("l"):
            seq = [None, 0, 1, 2]
            st.light_override = seq[(seq.index(st.light_override) + 1) % len(seq)]
        elif key == ord("p"):
            print(f"profile -> {policy.cycle_profile(st.t)}")
        elif key == ord("f"):
            st.sim_fault = not st.sim_fault
        elif key == ord("k"):
            link.heartbeat_enabled = not link.heartbeat_enabled
            print(f"heartbeat {'ON' if link.heartbeat_enabled else 'OFF (Arduino failsafe in ~1.5 s)'}")
        elif key == ord("x"):
            gateway.user_enabled = not gateway.user_enabled
        elif key == ord("a"):
            cfg.GEMINI_MODE = "describe" if cfg.GEMINI_MODE == "agent" else "agent"
        elif key == ord("m"):
            cfg.MIRROR_VIEW = not cfg.MIRROR_VIEW
            tracker.tracks.clear()
        elif key == ord("c"):
            importlib.reload(cfg)
            apply_overrides(args)
            print("config reloaded")
        elif key == ord("d"):
            overlay.show_panel = not overlay.show_panel
        elif key == ord("r"):
            if st.writer is None and st.last_frame is not None:
                h, w = st.last_frame.shape[:2]
                size = (int((w + (cfg.PANEL_WIDTH if overlay.show_panel else 0)) * cfg.DISPLAY_SCALE),
                        int(h * cfg.DISPLAY_SCALE))
                name = time.strftime("rec_%Y%m%d_%H%M%S.mp4")
                st.writer = cv2.VideoWriter(name, cv2.VideoWriter_fourcc(*"mp4v"), max(5, round(st.fps)), size)
                st.writer_size = size
                print(f"recording -> {name}")
            elif st.writer is not None:
                st.writer.release()
                st.writer = None
        return True

    # ------------------------------------------------------------ main loop
    running = True
    try:
        while running:
            frame, t = (None, None) if st.sim_fault else source.read(timeout=0.1)
            now = time.monotonic()

            for item in [s for s in st.scheduled if s[0] <= now]:
                st.scheduled.remove(item)
                link.buzz(item[1][0], int(item[1][1:]))
            if st.voice["wav"] is not None:
                wav, st.voice["wav"] = st.voice["wav"], None
                ask(audio=wav)
            if st.voice["err"]:
                print(f"voice input unavailable ({st.voice['err']}); using typed question")
                st.voice["err"] = None
                ask()

            if frame is None:
                if getattr(source, "ended", False):
                    break
                if source.is_live and (st.sim_fault or source.stale_for() > cfg.CAMERA_TIMEOUT_S) and not st.fault:
                    set_fault(True)
                if st.fault:
                    st.light = 0                      # fail-visible: normal flashing bike light
                hud.update([], fault=st.fault)
                publish_snapshot(now)
                link.tick(st.light if st.light_override is None else st.light_override)
                apply_gemini()
                if st.last_frame is not None:
                    show(st.last_frame // 3 if st.fault else st.last_frame)
                running = handle_key(poll_key())
                status_line(now)
                continue

            if st.fault:
                set_fault(False)
            if cfg.MIRROR_VIEW:
                frame = cv2.flip(frame, 1)
            st.t = t
            st.age_ms = (now - t) * 1000 if source.is_live else 0.0
            dt_loop = now - st.last_loop
            st.last_loop = now
            if dt_loop > 0:
                st.fps = 0.9 * st.fps + 0.1 / dt_loop if st.fps else 1.0 / dt_loop

            h, w = frame.shape[:2]
            motion.update(frame, t, [(tr.det.x1, tr.det.y1, tr.det.x2, tr.det.y2)
                                     for tr in tracker.tracks if not tr.coasting(t)])
            shaky = motion.shaky(t)
            dets = detector.detect(frame)
            before = tracker._next_id
            tracker.update(dets, t, motion.dx, motion.dy)
            vp = motion.vp_x(w)
            for tr in tracker.tracks:
                if tr.matched_now:
                    update_metrics(tr, t, w, vp, shaky, frame_h=h)

            for f in policy.evaluate(tracker.tracks, t, shaky):
                execute_fire(f)
            st.light = policy.light_level(tracker.tracks, t)
            hud.update(tracker.tracks)
            publish_snapshot(now, shaky)

            st.frames.append(frame)
            st.last_frame = frame
            reason = scene.check(t, tracker.vehicles_per_minute(t), tracker._next_id != before)
            if reason and gateway.available:
                gateway.submit(GeminiEvent("scene", frames=[prepare_frame(frame)],
                                           facts=facts_for(tracker.tracks) + f" | trigger: {reason}", priority=2))

            for ev in link.tick(st.light if st.light_override is None else st.light_override):
                if ev == "BTN":
                    ask()
            apply_gemini()

            if log:
                for tr in tracker.tracks:
                    if tr.matched_now:
                        d = tr.det
                        log.writerow([f"{t:.3f}", tr.id, tr.label, f"{d.conf:.2f}", int(d.x1), int(d.y1), int(d.x2),
                                      int(d.y2), int(d.area), f"{tr.growth:.3f}", f"{tr.consistency:.2f}",
                                      f"{tr.ttc:.2f}" if math.isfinite(tr.ttc) else "", tr.lat_m, tr.clearance_m,
                                      tr.dist_m, tr.zone, int(tr.alongside), tr.tier, tr.reason, int(shaky),
                                      f"{motion.dx:.1f}", f"{motion.dy:.1f}", f"{motion.vp_offset:.0f}",
                                      tr.scale_axis, tr.lat_v, tr.pred_lat_m, int(tr.on_path)])

            show(frame)
            running = handle_key(poll_key())
            status_line(now)
            if not headless and cv2.getWindowProperty(cfg.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                running = False
    except KeyboardInterrupt:
        pass
    finally:
        if streamer:
            streamer.close()
        if st.writer is not None:
            st.writer.release()
        if log_file:
            log_file.close()
        link.close()
        hud.close()
        source.release()
        if not headless:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
