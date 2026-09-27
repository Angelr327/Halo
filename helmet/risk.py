"""Turns per-vehicle metrics into alerts. 100% local and deterministic.

Tiers: 1 LOW (approaching, far)      -> overlay only, or a gentle tick on fast_open_road
       2 MEDIUM (blind spot/closing) -> directional buzz
       3 HIGH (imminent, behind or cutting close) -> strong buzz + DANGER light + short local phrase

Gemini can switch profiles and temporarily RAISE the light. It can never lower
the safety floors below, and it never decides whether a HIGH alert fires.
"""
import math
from dataclasses import dataclass

from . import config as cfg
from .perception import CENTER, LEFT, RIGHT

SIDE_CODE = {LEFT: "L", RIGHT: "R", CENTER: "B"}
SPOKEN_LABEL = {"car": "Car", "truck": "Truck", "bus": "Bus", "motorcycle": "Motorbike",
                "bicycle": "Bike", "person": "Person", "unknown": "Something"}
SPOKEN_SIDE = {LEFT: "left", RIGHT: "right", CENTER: "behind"}


@dataclass
class Fire:
    track_id: int
    label: str
    zone: str
    tier: int
    side: str
    reason: str
    t: float

    def phrase(self):
        return f"{SPOKEN_LABEL.get(self.label, 'Vehicle')} {SPOKEN_SIDE.get(self.zone, 'behind')}!"


class AlertPolicy:
    def __init__(self):
        self.profile_name = cfg.DEFAULT_PROFILE
        self.profile_set_t = -1e9
        self.side_last = {"L": -1e9, "R": -1e9}
        self.light_until = {1: -1e9, 2: -1e9}
        self.gemini_light = (0, -1e9)
        self.last_local_speech = -1e9

    @property
    def profile(self):
        return cfg.PROFILES.get(self.profile_name, cfg.PROFILES["standard"])

    def thresholds(self, label):
        p, scale = self.profile, cfg.CLASS_TTC_SCALE.get(label, 1.0)
        high = max(p["ttc_high"] * scale, cfg.FLOOR_TTC_HIGH_S)          # safety floor
        med = max(p["ttc_med"] * scale, cfg.FLOOR_TTC_MED_S, high)
        return high, med

    def raw_tier(self, tr):
        high, med = self.thresholds(tr.label)
        close_m = cfg.CLASS_CLOSE_PASS_M.get(tr.label, cfg.CLOSE_PASS_M)
        clr = [c for c in (tr.clearance_m, tr.pred_clearance_m) if c is not None]
        clr = min(clr) if clr else None                   # closest now OR at contact
        close = clr is not None and clr < close_m
        in_lane = tr.zone == CENTER or tr.on_path         # in your lane now, or heading into it
        if tr.approaching and tr.ttc <= high and (in_lane or close):
            what = "behind" if tr.zone == CENTER else "cutting in" if tr.on_path else "close pass"
            return 3, f"TTC {tr.ttc:.1f}s {what}"
        if tr.zone in (LEFT, RIGHT):
            in_spot = (tr.alongside and tr.was_approaching) or (tr.approaching and tr.ttc <= med)
            near = clr is not None and clr < 1.5 * close_m
            if in_spot and (not self.profile["med_needs_close"] or near):
                return 2, "alongside" if tr.alongside else f"blind spot, TTC {tr.ttc:.1f}s"
        if in_lane and tr.approaching and tr.ttc <= med:
            return 2, f"closing behind, TTC {tr.ttc:.1f}s"
        if tr.approaching:
            return 1, f"approaching, TTC {tr.ttc:.1f}s"
        return 0, ""

    def _side_free(self, side, tier, t):
        if tier >= 3:
            return True                        # HIGH is never delayed by an earlier buzz
        sides = ("L", "R") if side == "B" else (side,)
        return all(t - self.side_last[s] >= cfg.SIDE_MIN_GAP_S for s in sides)

    def evaluate(self, tracks, t, shaky):
        """Returns alerts to execute this frame, most severe first."""
        fires = []
        for tr in tracks:
            if not tr.matched_now:
                if tr.coasting(t):
                    tr.tier, tr.shown_tier, tr.cand_tier, tr.cand_count = 0, 0, 0, 0
                continue
            tier, reason = self.raw_tier(tr)
            if tier == tr.cand_tier:
                tr.cand_count += 1
            else:
                tr.cand_tier, tr.cand_count = tier, 1
            tr.tier, tr.reason = tier, reason
            # Overlay colour: escalate only once confirmed (so one noisy frame doesn't flash
            # red when nothing fires), drop immediately.
            if tier == 0 or tr.cand_count >= cfg.CONFIRM_FRAMES[tier]:
                tr.shown_tier = tier
            else:
                tr.shown_tier = min(tr.shown_tier, tier)
            if tier == 0 or tr.cand_count < cfg.CONFIRM_FRAMES[tier]:
                continue
            if tier == 1 and not self.profile["low_haptic"]:
                continue
            if shaky and tier < 3:
                tr.reason += " [shake-gated]"
                continue
            cooldown = cfg.COOLDOWN_S[tier] * self.profile["cooldown_scale"]
            escalation = tier > tr.fired_tier
            repeat = tier == tr.fired_tier and t - tr.fired_t >= cooldown
            if not (escalation or repeat):
                continue
            side = SIDE_CODE.get(tr.zone, "B")
            if not self._side_free(side, tier, t):
                continue
            for s in (("L", "R") if side == "B" else (side,)):
                self.side_last[s] = t
            tr.fired_tier, tr.fired_t = tier, t
            fires.append(Fire(tr.id, tr.label, tr.zone, tier, side, reason, t))
        fires.sort(key=lambda f: -f.tier)
        return fires

    def evaluate_contacts(self, contacts, tracks, t, shaky):
        """Alerts from side-sensor contacts (fusion.py). A contact linked to a camera track shares
        that track's cooldown, so the same vehicle never alerts twice for the same tier."""
        by_id = {tr.id: tr for tr in tracks}
        fires = []
        for c in contacts:
            if c.tier == 0 or (shaky and c.tier < 3):
                continue
            owner = by_id.get(c.track_id) or c
            cooldown = cfg.COOLDOWN_S[c.tier] * self.profile["cooldown_scale"]
            if not (c.tier > owner.fired_tier or t - owner.fired_t >= cooldown):
                continue
            side = SIDE_CODE[c.zone]
            if not self._side_free(side, c.tier, t):
                continue
            self.side_last[side] = t
            owner.fired_tier, owner.fired_t = c.tier, t
            c.fired_tier, c.fired_t = c.tier, t
            level = 2 if c.tier >= 3 else 1
            self.light_until[level] = t + cfg.LIGHT_HOLD_S[level]
            fires.append(Fire(c.track_id if c.track_id is not None else -1, c.label, c.zone, c.tier, side,
                              c.reason, t))
        return fires

    def light_level(self, tracks, t):
        """0 NORMAL, 1 ALERT (something closing), 2 DANGER (HIGH alert)."""
        for tr in tracks:
            if not tr.matched_now or tr.cand_count < cfg.CONFIRM_FRAMES.get(max(tr.tier, 1), 2):
                continue
            _, med = self.thresholds(tr.label)
            if tr.tier == 3:
                self.light_until[2] = t + cfg.LIGHT_HOLD_S[2]
            elif tr.approaching and tr.ttc <= med:
                self.light_until[1] = t + cfg.LIGHT_HOLD_S[1]
        level = 2 if t < self.light_until[2] else 1 if t < self.light_until[1] else 0
        g_level, g_until = self.gemini_light
        return max(level, g_level if t < g_until else 0)

    def may_speak_local(self, t):
        if cfg.LOCAL_SPEECH and t - self.last_local_speech >= cfg.LOCAL_SPEECH_MIN_GAP_S:
            self.last_local_speech = t
            return True
        return False

    # ---- Gemini-facing controls (called only through guardrails in main.py)
    def raise_light(self, level, seconds, t):
        level = 2 if level == "danger" else 1
        seconds = min(8.0, max(1.0, float(seconds)))
        self.gemini_light = (level, t + seconds)
        return level, seconds

    def set_profile(self, name, t, force=False):
        if name not in cfg.PROFILES or name == self.profile_name:
            return False
        if not force and t - self.profile_set_t < cfg.PROFILE_MIN_DWELL_S:
            return False
        self.profile_name, self.profile_set_t = name, t
        return True

    def cycle_profile(self, t):
        names = list(cfg.PROFILES)
        nxt = names[(names.index(self.profile_name) + 1) % len(names)] if self.profile_name in names else names[0]
        self.set_profile(nxt, t, force=True)
        return nxt


class SceneTrigger:
    """Event-driven scene checks: startup, traffic-density change, or stale profile + new vehicle."""

    def __init__(self):
        self.start_t = None
        self.last_t = -1e9
        self.bucket = None
        self.startup_done = False

    def check(self, t, vehicles_per_min, new_track):
        if self.start_t is None:
            self.start_t = t
        bucket = 0 if vehicles_per_min <= 2 else 1 if vehicles_per_min <= 8 else 2
        reason = None
        if not self.startup_done:
            if t - self.start_t >= cfg.SCENE_STARTUP_DELAY_S:
                reason = "startup"
        elif bucket != self.bucket and t - self.last_t >= cfg.SCENE_MIN_GAP_S:
            reason = f"traffic density changed ({vehicles_per_min}/min)"
        elif new_track and t - self.last_t >= cfg.SCENE_STALE_S:
            reason = "profile stale"
        if reason:
            self.startup_done, self.last_t, self.bucket = True, t, bucket
        return reason


def fmt_ttc(x):
    return "inf" if x is None or not math.isfinite(x) else f"{x:.1f}s"
