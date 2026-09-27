"""Camera + side-ultrasonic fusion: filtering, background rejection, hand-off, alert rules,
and the simulator's pass scenes end to end.
Run:  python -m tests.test_fusion      (no hardware needed)"""
import io
import math
from contextlib import redirect_stdout
from types import SimpleNamespace

from helmet import config as cfg
from helmet import snapshot
from helmet.fusion import SonarFusion
from helmet.outputs import SONAR_NAMES
from helmet.perception import LEFT, Tracker, update_metrics
from helmet.risk import AlertPolicy
from helmet.sim import Scenario, W, H


class FakeLink:
    def __init__(self):
        self.sonar_hist, self.sonar, self.sonar_t = [], dict.fromkeys(SONAR_NAMES), -1e9

    def inject_sonar(self, readings, ts):
        self.sonar, self.sonar_t = dict(readings), ts
        self.sonar_hist.append((ts, dict(readings)))
        del self.sonar_hist[:-64]

    def sonar_fresh(self):
        return True


def feed(fu, link, values, t0, dt=0.03, tracks=(), shaky=False):
    """Feed a list of left-sensor readings (metres or None); returns the time after the last one."""
    t = t0
    for v in values:
        t += dt
        link.inject_sonar({"SL": v}, ts=t)
        fu.update(list(tracks), t, link, shaky, now=t)
    return t


def _track(**kw):
    base = dict(id=5, label="car", zone=LEFT, lat_m=-1.6, dist_m=None, alongside=False, matched_now=False,
                last_t=0.0, was_approaching=True, fired_tier=0, fired_t=-1e9, clearance_m=None, measured_clearance_m=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_filter_spikes_and_static_background():
    fu, link = SonarFusion(), FakeLink()
    t = feed(fu, link, [0.8, 2.9, 0.81, None, 0.8, 0.79], 0.0)        # one spike, one dropout
    ch = fu.channels["SL"]
    assert abs(ch.value - 0.8) < 0.02, ch.value
    fu, link = SonarFusion(), FakeLink()
    log = io.StringIO()
    with redirect_stdout(log):
        t = feed(fu, link, [1.20] * 120, 0.0)                          # a wall 1.2 m away for 3.6 s
    assert fu.channels["SL"].static and not fu.contacts, "a wall must become background"
    assert not fu.passes and "[PASS]" not in log.getvalue(), "a wall is not a pass"
    t = feed(fu, link, [0.70] * 4, t)                                  # someone steps in front of it
    assert fu.contacts and not fu.channels["SL"].static
    print("  median filter rejects a spike; a wall becomes background; a new object breaks through")


def test_handoff_close_pass_is_high_and_measured():
    fu, link, pol = SonarFusion(), FakeLink(), AlertPolicy()
    tr = _track(last_t=9.5)                                            # left the camera's view 0.5 s ago
    t = feed(fu, link, [0.40] * 4, 10.0, tracks=[tr])
    c = fu.contacts[0]
    assert c.track_id == 5 and c.confirmed and c.label == "car" and c.tier == 3, c
    expect = cfg.SONAR_OFFSET_M + 0.40 * math.cos(math.radians(5)) - cfg.RIDER_HALF_WIDTH_M
    assert abs(c.clearance_m - expect) < 0.02 and tr.measured_clearance_m == c.clearance_m
    fires = pol.evaluate_contacts(fu.contacts, [tr], t, False)
    assert [(f.tier, f.side) for f in fires] == [(3, "L")] and "measured" in fires[0].reason
    assert not pol.evaluate_contacts(fu.contacts, [tr], t + 0.1, False), "must not re-fire the same tier"
    print(f"  hand-off: {fires[0].reason}")


def test_wide_pass_is_medium_and_logged():
    fu, link = SonarFusion(), FakeLink()
    tr = _track(last_t=9.8)
    t = feed(fu, link, [1.60] * 5, 10.0, tracks=[tr])
    assert fu.contacts[0].tier == 2
    out = io.StringIO()
    with redirect_stdout(out):
        feed(fu, link, [None] * 20, t, tracks=[tr])                    # it has gone past
    assert not fu.contacts and fu.passes[-1]["confirmed"]
    assert "car passed on your left at 1.36 m" in out.getvalue(), out.getvalue()   # 1.6*cos5 + 0.12 - 0.35
    print(f"  {out.getvalue().strip()}")


def test_sensor_alone_is_medium_never_high():
    fu, link, pol = SonarFusion(), FakeLink(), AlertPolicy()
    t = feed(fu, link, [0.30, 0.31, 0.29, 0.30, 0.32], 0.0)            # very close, but no camera track
    c = fu.contacts[0]
    assert c.track_id is None and c.label == "unknown" and c.tier == 2
    fires = pol.evaluate_contacts(fu.contacts, [], t, False)
    assert [f.tier for f in fires] == [2] and fires[0].phrase() == "Something left!"
    print(f"  no camera track: MED only ({c.reason})")


def test_readings_while_head_turns_are_ignored():
    fu, link = SonarFusion(), FakeLink()
    feed(fu, link, [0.5] * 10, 0.0, shaky=True)
    assert not fu.contacts and fu.channels["SL"].value is None
    print("  readings during a head turn are dropped")


def test_snapshot_shows_contacts():
    fu, link = SonarFusion(), FakeLink()
    feed(fu, link, [0.8] * 5, 0.0)
    s = snapshot.build([], 1.0, hud_state={}, contacts=fu.contacts)
    car = s["cars"][0]
    assert car["id"] == "sonar-SL" and car["label"] == "unknown" and car["sonar_only"] and car["x"] < 0
    assert s["sonar_mount"]["SL"] == {"zone": "LEFT", "yaw": 5.0, "offset": cfg.SONAR_OFFSET_M}
    assert s["sonar_mount"]["SR"]["zone"] == "RIGHT"
    print(f"  snapshot: unknown object at x={car['x']} m, measured gap {car['measured']} m")


def run_scenes(people, seed=4):
    """The simulator through the real pipeline + fusion. Returns {scene: (fires, contacts seen)}."""
    sc, trk, pol, fu, link = Scenario(seed=seed, people=people), Tracker(), AlertPolicy(), SonarFusion(), FakeLink()
    out, t = {}, 0.0
    log = io.StringIO()
    with redirect_stdout(log):
        while True:
            t += 1 / 15
            title = sc.title()
            objs = sc.step(1 / 15)
            if sc.title() != title and sc.idx == 0:
                break
            link.inject_sonar(sc.sonar(objs), ts=t)
            trk.update(sc.boxes(objs), t)
            for tr in trk.tracks:
                if tr.matched_now:
                    update_metrics(tr, t, W, W / 2, False, frame_h=H)
            fu.update(trk.tracks, t, link, False, now=t)
            fires = pol.evaluate(trk.tracks, t, False) + pol.evaluate_contacts(fu.contacts, trk.tracks, t, False)
            fires_, contacts_ = out.setdefault(sc.title(), ([], []))
            fires_ += [(f.tier, f.zone, f.reason) for f in fires]
            contacts_ += [(c.tier, c.confirmed, round(c.clearance_m, 2), c.label) for c in fu.contacts]
    return out, log.getvalue()


def test_sim_car_passes():
    out, log = run_scenes(people=False)
    get = lambda key: next(v for k, v in out.items() if key in k)
    fires, contacts = get("Close pass on the left")
    assert any(t == 3 and conf and abs(clr - 0.25) < 0.08 for t, conf, clr, _ in contacts), contacts[:5]
    assert any(t == 3 and z == LEFT for t, z, _ in fires)
    fires, contacts = get("passing on the left, normal gap")
    assert contacts and all(t == 2 and conf for t, conf, _, _ in contacts), contacts[:5]
    assert not any(t == 3 for t, _, _ in fires)
    fires, contacts = get("Close pass on the right")                  # right sensor: measured too
    assert any(t == 3 and conf and abs(clr - 0.35) < 0.08 for t, conf, clr, _ in contacts), contacts[:5]
    assert not any(c for k, (f, c) in out.items() if "directly behind" in k), "car behind never reaches the side"
    passes = [line for line in log.splitlines() if line.startswith("[PASS]")]
    print("  " + "\n  ".join(passes))
    assert any("at 0.2" in p for p in passes) and any("at 1.3" in p for p in passes)


def test_sim_demo_person_passes():
    saved, cfg.DEMO_PERSON_AS_VEHICLE = cfg.DEMO_PERSON_AS_VEHICLE, True
    try:
        out, log = run_scenes(people=True)
    finally:
        cfg.DEMO_PERSON_AS_VEHICLE = saved
    get = lambda key: next(v for k, v in out.items() if key in k)
    fires, contacts = get("brushing past on your left")
    assert any(t == 3 and conf and lab == "person" for t, conf, _, lab in contacts), contacts[:5]
    fires, contacts = get("steps in beside you")
    assert contacts and all(not conf for _, conf, _, _ in contacts)
    assert any(t == 2 and "object beside you" in r for t, _, r in fires) and not any(t == 3 for t, _, _ in fires)
    fires, contacts = get("walking past on your left")
    assert contacts and not any(t == 3 for t, _, _ in fires), fires
    assert not get("standing still")[0]
    print("  " + "\n  ".join(line for line in log.splitlines() if line.startswith("[PASS]")))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
