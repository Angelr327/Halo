"""HUD arrows: right side, right orientation after upside-down mounting, blink, hold.
Run:  python -m tests.test_hud      (no display or luma needed)"""
import time
from types import SimpleNamespace

from helmet import config as cfg
from helmet.hud import W, Hud, render, threat_state, to_panel
from helmet.perception import CENTER, LEFT, RIGHT


def lit(img, x0, x1):
    return int((img[:, x0:x1] > 0).sum())


def tip_column(img):
    """x of the arrow tip: the lit column closest to a panel edge, as (left_edge_x, right_edge_x)."""
    cols = [x for x in range(W) if img[:, x].any()]
    return cols[0], cols[-1]


def test_left_threat_points_left_for_the_rider():
    img = render({LEFT: 2})
    assert lit(img, 0, W // 2) > 0 and lit(img, W // 2, W) == 0
    first, _ = tip_column(img)
    assert first < 6, "left arrow tip should reach the rider's left edge"
    print("  rider view: LEFT threat -> arrow on the left, pointing left")


def test_upside_down_mount_flips_panel_pixels():
    cfg.HUD_ROTATE_180, cfg.HUD_MIRROR = True, False
    panel = to_panel(render({LEFT: 2}))
    # Upside-down panel: the rider's left is the panel's right, arrow tip on the panel's right edge
    assert lit(panel, W // 2, W) > 0 and lit(panel, 0, W // 2) == 0
    assert tip_column(panel)[1] > W - 7
    # ...and turning the panel over again gives back the rider's view exactly
    assert (panel[::-1, ::-1] == render({LEFT: 2})).all()
    cfg.HUD_MIRROR = True                 # read through the back of the glass: left stays left
    assert lit(to_panel(render({LEFT: 2})), 0, W // 2) > 0
    cfg.HUD_MIRROR = False
    print("  upside-down mount: panel pixels rotated 180 deg, rider still sees a left arrow")


def test_high_is_filled_and_blinks_but_never_vanishes():
    med, hi_on, hi_off = render({RIGHT: 2}), render({RIGHT: 3}, blink_on=True), render({RIGHT: 3}, blink_on=False)
    assert lit(hi_on, 0, W) > 2 * lit(med, 0, W)          # filled vs outline
    assert 0 < lit(hi_off, 0, W) < lit(hi_on, 0, W)       # blink-off phase is an outline, not blank
    print("  MED outline, HIGH filled + blinking to outline")


def test_behind_and_fault_and_nothing():
    behind = render({CENTER: 3})
    assert lit(behind, 44, 84) > 0 and lit(behind, 0, 44) == 0 and lit(behind, 84, W) == 0
    assert lit(render({}, fault=True), 0, W) > 0
    assert lit(render({}), 0, W) == 0                     # no threat: panel dark (fully transparent)
    assert lit(render({LEFT: 1}), 0, W) == 0              # LOW is overlay-only
    print("  behind = centre chevrons, fault = X, idle = dark, LOW not shown")


def test_state_uses_confirmed_tier_and_holds():
    tr = lambda zone, tier, live=True: SimpleNamespace(matched_now=live, zone=zone, shown_tier=tier)
    assert threat_state([tr(LEFT, 2), tr(LEFT, 3), tr(RIGHT, 1), tr(CENTER, 3, live=False)]) == {LEFT: 3}
    hud = Hud(enabled=False)                              # no hardware: preview only
    hud.update([tr(RIGHT, 3)])
    hud.update([])
    assert hud._state == {RIGHT: 3}, "arrow should stay up briefly after the threat clears"
    cfg_hold, cfg.HUD_HOLD_S = cfg.HUD_HOLD_S, 0.05
    hud.update([tr(RIGHT, 3)])
    time.sleep(0.08)
    hud.update([])
    cfg.HUD_HOLD_S = cfg_hold
    assert hud._state == {}
    print(f"  per-zone hold {cfg.HUD_HOLD_S}s, then clears; status '{hud.status}'")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
