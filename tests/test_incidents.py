"""Incident reports: recording, merging, cooldown, the clip, Gemini analysis, and the phone API.
Run:  python -m tests.test_incidents      (no camera, Arduino or API key needed)"""
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import cv2
import numpy as np

from helmet import config as cfg
from helmet.incidents import IncidentRecorder
from helmet.perception import Detection
from helmet.risk import Fire
from helmet.streamer import Streamer

PORT = 8768


def _track(z, tier=3, tid=7, measured=None):
    w = 512 * 1.8 / z
    det = Detection(320 - w / 2, 250 - w * 0.4, 320 + w / 2, 250 + w * 0.4, 0.9, "car")
    return SimpleNamespace(id=tid, label="car", det=det, matched_now=True, shown_tier=tier, ttc=z / 9.0,
                           dist_m=z, lat_m=-0.2, measured_clearance_m=measured, zone="CENTER")


def _frame(z):
    img = np.full((480, 640, 3), 70, np.uint8)
    w = int(512 * 1.8 / z)
    cv2.rectangle(img, (320 - w // 2, 250 - int(w * 0.4)), (320 + w // 2, 250 + int(w * 0.4)), (220, 220, 220), -1)
    return img


def run_approach(rec, fire_at=6.0, secs=None, fps=15.0, extra_fire_at=None, tid=7):
    """A car closing from behind; a HIGH alert fires at `fire_at`."""
    if secs is None:
        latest_fire = max(at for at in (fire_at, extra_fire_at) if at is not None)
        secs = latest_fire + cfg.INCIDENT_POST_S + 1.0
    ids, t = [], 0.0
    while t < secs:
        z = max(1.0, 40 - 3.5 * t)
        tr = _track(z, tid=tid)
        for at in (fire_at, extra_fire_at):
            if at is not None and abs(t - at) < 0.5 / fps:
                ids.append(rec.trigger(Fire(tid, "car", "CENTER", 3, "B", f"TTC {z / 9:.1f}s behind", t), t, [tr]))
        rec.add_frame(_frame(z), t, [tr], ())
        t += 1 / fps
    return ids


def wait_written(rec, timeout=30):
    deadline = time.monotonic() + timeout
    while (rec.writing or rec.open) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_high_alert_saves_clip_and_report():
    d = tempfile.mkdtemp()
    rec = IncidentRecorder(client=None, directory=d)
    ids = run_approach(rec)
    wait_written(rec)
    assert len(ids) == 1 and ids[0]
    meta = rec.get(ids[0])
    assert meta["severity"] == "HIGH" and meta["side"] == "behind" and meta["label"] == "car"
    assert meta["clip"] == "clip.mp4" and meta["thumb"] == "thumb.jpg"
    expected_duration = min(cfg.INCIDENT_PRE_S, 6.0) + cfg.INCIDENT_POST_S
    assert abs(meta["duration_s"] - expected_duration) < 0.3, meta["duration_s"]
    assert meta["min_ttc_s"] is not None and meta["trace"] and meta["analysis_status"] == "not_configured"
    assert "closed fast from directly behind" in meta["local_summary"] and "warned" in meta["local_summary"]
    cap = cv2.VideoCapture(rec.file_path(ids[0], "clip.mp4"))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    ok, first = cap.read()
    cap.release()
    assert ok and first.shape[1] == cfg.INCIDENT_FRAME_WIDTH and abs(n - meta["frames"]) <= 1
    assert abs(fps - cfg.INCIDENT_FPS) < 0.5, f"15 fps camera should give a {cfg.INCIDENT_FPS} fps clip, got {fps:.2f}"
    assert abs(n / fps - meta["duration_s"]) < 0.3, "clip must play back in real time"
    ts = [p[0] for p in meta["trace"]]
    assert ts == sorted(set(ts)), "trace must not repeat timestamps"
    print(f"  {ids[0]}: {meta['duration_s']} s clip, {n} frames @ {fps:.0f} fps, '{meta['local_summary']}'")


def test_alerts_during_post_roll_merge_and_cooldown_blocks_repeats():
    d = tempfile.mkdtemp()
    rec = IncidentRecorder(client=None, directory=d)
    ids = run_approach(rec, fire_at=6.0, extra_fire_at=8.0)
    wait_written(rec)
    assert ids[0] == ids[1], "second alert while recording should join the same incident"
    meta = rec.get(ids[0])
    base_duration = min(cfg.INCIDENT_PRE_S, 6.0) + cfg.INCIDENT_POST_S
    assert len(meta["events"]) == 2 and meta["duration_s"] > base_duration
    after_recording = 8.0 + cfg.INCIDENT_POST_S + 2.0
    again = rec.trigger(Fire(7, "car", "CENTER", 3, "B", "again", after_recording), after_recording)
    assert again is None, "same vehicle inside the cooldown must not open another incident"
    other = rec.trigger(Fire(8, "car", "LEFT", 3, "L", "close pass", after_recording), after_recording)
    assert other is not None
    # a different vehicle alerting while one is recording gets its own incident afterwards
    rec2 = IncidentRecorder(client=None, directory=tempfile.mkdtemp())
    t = 0.0
    while t < 8.0 + cfg.INCIDENT_POST_S + 2.0:
        z = max(1.0, 40 - 3.5 * t)
        a, b = _track(z, tid=1), _track(z + 3, tid=2)
        if abs(t - 6.0) < 0.03:
            rec2.trigger(Fire(1, "car", "CENTER", 3, "B", "TTC 1.0s behind", t), t, [a, b])
        if abs(t - 8.0) < 0.03:
            assert rec2.trigger(Fire(2, "car", "LEFT", 3, "L", "TTC 1.2s close pass", t), t, [a, b]) is None
        rec2.add_frame(_frame(z), t, [a, b], ())
        t += 1 / 15
    wait_written(rec2)
    metas = rec2.list()
    assert len(metas) == 2 and {m["track_id"] for m in metas} == {1, 2}, [m["track_id"] for m in metas]
    assert all(len(m["events"]) == 1 for m in metas)
    print(f"  merged 2 alerts into {ids[0]}; same car blocked by cooldown; a different car opens {other};"
          f" two cars at once -> two reports")


def test_med_alerts_follow_the_min_tier_setting():
    d = tempfile.mkdtemp()
    rec = IncidentRecorder(client=None, directory=d)
    med = Fire(3, "car", "LEFT", 2, "L", "blind spot", 1.0)
    assert rec.trigger(med, 1.0) is None                 # default: HIGH only
    saved, cfg.INCIDENT_MIN_TIER = cfg.INCIDENT_MIN_TIER, 2
    try:
        assert rec.trigger(med, 1.0) is not None
    finally:
        cfg.INCIDENT_MIN_TIER = saved
    manual = IncidentRecorder(client=None, directory=tempfile.mkdtemp())
    assert manual.trigger_manual(5.0) is not None        # the rider can always mark one
    print("  MED ignored by default, recorded with INCIDENT_MIN_TIER = 2; manual marks always work")


class FakeModels:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.fail:
            raise RuntimeError("quota")
        return SimpleNamespace(text=json.dumps(self.reply))


def test_gemini_analysis_is_stored_and_failures_are_reported():
    reply = {"classification": "near_miss", "severity": 4, "vehicle": "grey sedan", "confidence": "medium",
             "maneuver": "closed fast in the rider's lane", "summary": "A grey sedan closed quickly from behind."}
    models = FakeModels(reply)
    saved = cfg.GEMINI_MIN_INTERVAL_S
    cfg.GEMINI_MIN_INTERVAL_S = 0
    try:
        rec = IncidentRecorder(client=SimpleNamespace(models=models), directory=tempfile.mkdtemp())
        ids = run_approach(rec)
        wait_written(rec)
        deadline = time.monotonic() + 10
        while rec.get(ids[0])["analysis_status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.05)
        meta = rec.get(ids[0])
        assert meta["analysis_status"] == "done" and meta["analysis"]["vehicle"] == "grey sedan", meta
        parts = models.calls[0][0].parts
        images = [p for p in parts if getattr(p, "inline_data", None)]
        assert len(images) == cfg.INCIDENT_KEYFRAMES and "Measured facts" in parts[-1].text
        bad = IncidentRecorder(client=SimpleNamespace(models=FakeModels(fail=True)), directory=tempfile.mkdtemp())
        ids = run_approach(bad)
        wait_written(bad)
        deadline = time.monotonic() + 10
        while bad.get(ids[0])["analysis_status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert bad.get(ids[0])["analysis_status"] == "failed" and "quota" in bad.get(ids[0])["analysis_error"]
    finally:
        cfg.GEMINI_MIN_INTERVAL_S = saved
    print(f"  Gemini got {len(images)} key frames + facts -> '{meta['analysis']['summary']}'; errors recorded")


def test_side_sensor_decides_the_side():
    rec = IncidentRecorder(client=None, directory=tempfile.mkdtemp())
    t, contact = 0.0, None
    while t < 6.0 + cfg.INCIDENT_POST_S + 1.0:
        z = max(1.0, 40 - 3.5 * t)
        tr = _track(z)
        if abs(t - 6.0) < 0.03:
            rec.trigger(Fire(7, "car", "CENTER", 3, "B", "TTC 2.0s behind", t), t, [tr])
        contacts = [SimpleNamespace(track_id=7, zone="LEFT", clearance_m=0.31)] if 7.0 < t < 7.5 else []
        rec.add_frame(_frame(z), t, [tr], contacts)
        t += 1 / 15
    wait_written(rec)
    m = rec.list()[0]
    assert m["zone"] == "LEFT" and m["measured_clearance_m"] == 0.31 and "passed close on your left" in m["local_summary"], m
    print(f"  '{m['local_summary']}'")


def test_phone_api():
    d = tempfile.mkdtemp()
    rec = IncidentRecorder(client=None, directory=d)
    ids = run_approach(rec)
    wait_written(rec)
    st = Streamer(PORT)
    st.incidents = rec
    get = lambda p, h=None: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{p}", headers=h or {}), timeout=3)
    try:
        lst = json.loads(get("/api/v1/incidents").read())
        item = lst["incidents"][0]
        assert item["id"] == ids[0] and item["clip_url"] == f"/api/v1/incidents/{ids[0]}/clip.mp4"
        assert json.loads(get(item["url"]).read())["severity"] == "HIGH"
        full = get(item["clip_url"])
        body = full.read()
        assert full.headers["Content-Type"] == "video/mp4" and full.headers["Accept-Ranges"] == "bytes"
        part = get(item["clip_url"], {"Range": "bytes=0-99"})
        assert part.status == 206 and part.headers["Content-Range"] == f"bytes 0-99/{len(body)}" and part.read() == body[:100]
        assert get(item["thumb_url"]).headers["Content-Type"] == "image/jpeg"
        for bad in (f"/api/v1/incidents/{ids[0]}/meta.json", "/api/v1/incidents/..%2f..%2fetc/clip.mp4",
                    "/api/v1/incidents/nope"):
            try:
                get(bad)
                raise AssertionError(f"{bad} should be 404")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        post = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/v1/incidents", data=b"",
                                                             method="POST"), timeout=3)
        assert post.status == 202 and st.get_key() == ord("i"), "POST should mark an incident via the main loop"
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{item['url']}/analyze", data=b"",
                                                          method="POST"), timeout=3)
            raise AssertionError("analyze without an API key should be 409")
        except urllib.error.HTTPError as e:
            assert e.code == 409
    finally:
        st.close()
    print(f"  list, report, clip ({len(body)} B, byte ranges), thumb, mark (POST), path tricks blocked")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
