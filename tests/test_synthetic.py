"""Synthetic scenarios through tracker -> metrics -> policy, with YOLO-like jitter.
Run:  python -m tests.test_synthetic      (no camera, YOLO or Arduino needed)
Re-run after changing thresholds to make sure you didn't break the basics."""
import math
import random

from helmet import config as cfg
from helmet.perception import Detection, Tracker, focal_px, update_metrics
from helmet.risk import AlertPolicy

W, H, FPS = 640, 480, 12.0


class Snap:
    def __init__(self, ttc, ready, zone):
        self.ttc, self.ready, self.zone = ttc, ready, zone


def box_for(X, Z, width_m=1.8, jitter=0.03, pos_jitter=4.0, shift=0.0):
    """Pinhole projection of a vehicle at lateral X (m, - = rider's left) and range Z (m)."""
    f = focal_px(W)
    w = f * width_m / Z * (1 + random.uniform(-jitter, jitter))
    h = w * 0.8 * (1 + random.uniform(-jitter, jitter))
    cx = W / 2 + f * X / Z + random.uniform(-pos_jitter, pos_jitter) + shift
    cy = 250 + random.uniform(-pos_jitter, pos_jitter)
    x1, x2 = max(0.0, cx - w / 2), min(W - 1.0, cx + w / 2)
    y1, y2 = max(0.0, cy - h / 2), min(H - 1.0, cy + h / 2)
    if x2 - x1 < 3:
        return None
    return Detection(x1, y1, x2, y2, 0.8, "car")


def person_box(X, Z, cam_h=1.6, height_m=1.75, width_m=0.5, jitter=0.04, pos_jitter=3.0):
    """A walking person seen by a level camera cam_h above the floor. Close up, the feet
    (and for a low camera, the head) leave the frame, so the box is clipped top/bottom."""
    f = focal_px(W)
    w = f * width_m / Z * (1 + random.uniform(-jitter, jitter))
    cx = W / 2 + f * X / Z + random.uniform(-pos_jitter, pos_jitter)
    y1 = H / 2 + f * (cam_h - height_m) / Z + random.uniform(-pos_jitter, pos_jitter)
    y2 = H / 2 + f * cam_h / Z + random.uniform(-pos_jitter, pos_jitter)
    x1, x2 = max(0.0, cx - w / 2), min(W - 1.0, cx + w / 2)
    y1, y2 = max(0.0, y1), min(H - 1.0, y2)
    if x2 - x1 < 3:
        return None
    return Detection(x1, y1, x2, y2, 0.8, "person")


def run(scenario, seconds, shake_px=0.0, seed=1, boxer=None):
    random.seed(seed)
    trk, pol = Tracker(), AlertPolicy()
    fires, log = [], []
    shift_prev = 0.0
    for i in range(int(seconds * FPS)):
        t = i / FPS
        X, Z, truth_ttc = scenario(t)
        shift = random.uniform(-shake_px, shake_px) if shake_px else 0.0
        gdx = shift - shift_prev             # what phase correlation would report
        shift_prev = shift
        if boxer:
            det = boxer(X, Z) if Z > 0.5 else None
        else:
            det = box_for(X, Z, shift=shift) if Z > 0.5 else None
        trk.update([det] if det else [], t, gdx, 0.0)
        shaky = abs(gdx) / W > cfg.SHAKE_GATE_FRAC
        for tr in trk.tracks:
            if tr.matched_now:
                update_metrics(tr, t, W, W / 2, shaky, frame_h=H)
        fires += pol.evaluate(trk.tracks, t, shaky)
        for tr in trk.tracks:
            if tr.matched_now:
                log.append((t, truth_ttc, Snap(tr.ttc, tr.ready, tr.zone)))
    return fires, log, trk


def approach_behind(t):          # car directly behind, closing at 10 m/s from 40 m
    Z = 40 - 10 * t
    return 0.0, Z, Z / 10


def test_ttc_accuracy_and_high_alert():
    fires, log, _ = run(approach_behind, 3.8)
    errs = [abs(tr.ttc - truth) / truth for t, truth, tr in log
            if tr.ready and 1.0 < truth < 3.5 and math.isfinite(tr.ttc)]
    assert errs, "TTC never became ready"
    med_err = sorted(errs)[len(errs) // 2]
    high = [f for f in fires if f.tier == 3]
    assert high, "no HIGH alert for a car closing from directly behind"
    first_high_truth = 4.0 - high[0].t
    print(f"  approach: median TTC error {med_err:.0%}, first HIGH at true TTC {first_high_truth:.2f}s, "
          f"zone {high[0].zone}")
    assert med_err < 0.30
    assert high[0].zone == "CENTER"
    assert first_high_truth >= 1.2, "HIGH fired too late"


def test_stationary_and_receding_are_quiet():
    fires, _, _ = run(lambda t: (0.0, 15.0, math.inf), 6.0)
    assert not [f for f in fires if f.tier >= 2], f"false alerts on a parked car: {fires}"
    fires, _, _ = run(lambda t: (0.0, 8 + 5 * t, math.inf), 5.0)
    assert not [f for f in fires if f.tier >= 2], f"false alerts on a receding car: {fires}"
    print("  stationary + receding: no alerts")


def test_normal_pass_left_is_medium():
    fires, _, _ = run(lambda t: (-2.6, 40 - 10 * t, (40 - 10 * t) / 10), 4.2)
    tiers = {f.tier for f in fires}
    sides = {f.side for f in fires if f.tier >= 2}
    print(f"  normal pass (2.6 m left): tiers {sorted(tiers)}, sides {sides}")
    assert 2 in tiers and 3 not in tiers and sides == {"L"}


def test_close_pass_left_is_high():
    fires, _, _ = run(lambda t: (-1.6, 40 - 10 * t, (40 - 10 * t) / 10), 4.2)
    high = [f for f in fires if f.tier == 3]
    print(f"  close pass (1.6 m left): HIGH on {[f.side for f in high]}")
    assert high and high[0].side == "L"


def test_head_shake_does_not_alert_or_flip_zone():
    fires, log, _ = run(lambda t: (-2.6, 15.0, math.inf), 6.0, shake_px=30)
    zones = {tr.zone for _, _, tr in log[20:]}
    print(f"  head shake +/-30px on a parked car: {len(fires)} alerts, zones seen {zones}")
    assert not [f for f in fires if f.tier >= 2]
    assert zones == {"LEFT"}


def test_shake_does_not_hide_real_threat():
    fires, _, _ = run(approach_behind, 3.8, shake_px=30)
    assert [f for f in fires if f.tier == 3], "real approach missed under head shake"
    print("  approach under head shake: HIGH still fires")


def walk(X0, Z0=7.0, speed=1.4, X1=None, stop_z=0.0):
    """Person walking toward the camera at 1.4 m/s, lateral X0 -> X1 (m) by the time they arrive."""
    X1 = X0 if X1 is None else X1

    def scen(t):
        Z = max(stop_z, Z0 - speed * t)
        X = X0 + (X1 - X0) * (1 - Z / Z0)
        return X, Z, (Z / speed if Z > stop_z else math.inf)
    return scen


def test_person_straight_at_head_height_camera_is_high():
    # Below ~3.4 m the feet leave the frame. Area-based TTC then stalled around 2.5 s.
    for seed in (1, 2, 3):
        fires, log, _ = run(walk(0.0), 4.8, seed=seed, boxer=person_box)
        high = [f for f in fires if f.tier == 3]
        assert high, f"seed {seed}: person walking straight at the rider never went HIGH"
        truth = (7.0 - 1.4 * high[0].t) / 1.4
        errs = [abs(tr.ttc - tt) / tt for _, tt, tr in log if tr.ready and 0.8 < tt < 2.5 and math.isfinite(tr.ttc)]
        med_err = sorted(errs)[len(errs) // 2]
        assert truth >= 1.2 and med_err < 0.30, f"seed {seed}: HIGH at true TTC {truth:.2f}s, err {med_err:.0%}"
    print(f"  person straight on, head-height camera: HIGH at true TTC {truth:.2f}s, close-range TTC err {med_err:.0%}")


def test_person_on_side_line_is_medium_not_high():
    # README calibration step 7: walking past on the 1.5 m line = MED on that side, not HIGH.
    for seed in (1, 2, 3):
        fires, _, _ = run(walk(-1.5), 4.8, seed=seed, boxer=person_box)
        assert not [f for f in fires if f.tier == 3], f"seed {seed}: HIGH for a normal 1.5 m pass: {fires}"
        assert {f.side for f in fires if f.tier == 2} <= {"L"}
    print("  person passing on the 1.5 m line: no HIGH")


def test_person_brushing_past_is_high():
    # Just outside the 1.2 m lane, body ~0.35 m from the bike: close pass on the left.
    for seed in (1, 2, 3):
        fires, _, _ = run(walk(-0.95), 4.8, seed=seed, boxer=person_box)
        high = [f for f in fires if f.tier == 3]
        assert high and high[0].side == "L", f"seed {seed}: {fires}"
    print(f"  person brushing past 0.95 m left: HIGH {high[0].side} ({high[0].reason})")


def test_standing_still_at_frame_edge_is_quiet():
    # Walks in 1.3 m left, stops half out of frame. Aging the last TTC used to run it to 0 -> HIGH.
    fires, _, _ = run(walk(-1.3, Z0=6.0, stop_z=2.0), 9.0, boxer=lambda X, Z: person_box(X, Z, cam_h=0.9))
    late = [f for f in fires if f.t > 4.5]
    print(f"  standing still at the frame edge: tiers after stopping {sorted({f.tier for f in late})} (MED 'alongside' is intended)")
    assert not [f for f in late if f.tier == 3], late


def test_car_cutting_into_lane_is_high_early():
    # Starts 3 m left, steers into the rider's lane while closing at 10 m/s.
    scen = lambda t: (-3.0 * max(0.0, 1 - t / 3.0), 30 - 10 * t, (30 - 10 * t) / 10)
    fires, _, _ = run(scen, 2.9)
    high = [f for f in fires if f.tier == 3]
    assert high, "no HIGH for a car cutting into the rider's lane"
    truth = 3.0 - high[0].t
    print(f"  car cutting in from 3 m left: first HIGH at true TTC {truth:.2f}s ({high[0].reason})")
    assert truth >= 2.0 and "cutting in" in high[0].reason


def test_person_veering_away_is_not_high():
    for seed in (1, 2, 3):
        fires, _, _ = run(walk(-0.8, X1=-2.6), 4.8, seed=seed, boxer=person_box)
        assert not [f for f in fires if f.tier == 3], f"seed {seed}: HIGH for someone veering away: {fires}"
    print("  person veering away from the lane: no HIGH")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
