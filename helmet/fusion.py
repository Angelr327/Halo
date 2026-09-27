"""Camera + ultrasonic fusion.

The rear camera says WHAT is coming, WHICH SIDE, and HOW FAST it is closing, but it sees only
about +/-33 deg either side of straight back: a car right beside you is outside its view. The
side ultrasonic sensors see exactly there, and measure the gap to within a few cm.

Geometry (rider frame, metres; x right, negative = left; z behind). A side sensor sits on the
helmet SONAR_OFFSET_M from the centreline, pointing straight out and angled `yaw` degrees
backward (SONAR_MOUNT). An echo at range r is a surface at |x| = offset + r*cos(yaw),
z = r*sin(yaw). Clearance = that surface's distance past the rider's handlebars.

Per sensor, on every reading:
  1. clean     range gate, median of the last 3, readings dropped while the head is turning
  2. static    an echo that hasn't moved for SONAR_STATIC_S is background (a wall, a pole, your
               own backpack) and is ignored until it changes
  3. contact   a moving echo is a contact on that side
  4. hand-off  the contact is linked to the camera track that was just on that side (at the
               frame edge, or lost from view in the last SONAR_HANDOFF_S): now it has a class
               and we know it approached
  5. tier      linked and it approached: measured clearance < close-pass distance -> HIGH,
               otherwise MED. Not linked (the camera never saw it): MED once it persists.
               Never HIGH on ultrasonic alone: it can't tell a car from a pole.
  6. pass log  when the contact ends: "car passed on your left at 0.72 m"
"""
import math
import time
from collections import deque
from dataclasses import dataclass

from . import config as cfg
from .perception import LEFT, RIGHT


@dataclass
class Contact:
    sensor: str
    zone: str                      # LEFT / RIGHT
    started: float
    range_m: float = 0.0           # latest filtered echo range
    clearance_m: float = 0.0       # gap between the object and the rider's handlebars
    near_m: float = 0.0            # |x| of the object's near surface
    behind_m: float = 0.0
    min_clearance: float = math.inf
    readings: int = 0              # filtered readings seen while this contact is alive
    track_id: int = None           # linked camera track, if any
    label: str = "unknown"
    confirmed: bool = False        # linked to a track that was approaching
    tier: int = 0
    peak_tier: int = 0             # highest tier it reached (for the pass log)
    reason: str = ""
    fired_tier: int = 0            # alert state when no camera track owns it any more
    fired_t: float = -1e9

    @property
    def x_center(self):
        """Signed lateral centre of the object (for drawing): near surface + half its width."""
        half = cfg.CLASS_WIDTH_M.get(self.label, 0.5) / 2
        return (-1 if self.zone == LEFT else 1) * (self.near_m + half)


class _Channel:
    def __init__(self, name, zone, yaw_deg):
        self.name, self.zone = name, zone
        self.yaw = math.radians(yaw_deg)
        self.raw = deque(maxlen=3)
        self.window = deque()      # (ts, filtered) for static detection
        self.value = None          # filtered range, metres (None = no echo)
        self.static = False
        self.last_valid = -1e9
        self.new_valid = 0         # filtered readings since the last update()
        self.contact = None

    def feed(self, ts, r):
        ok = r is not None and cfg.SONAR_MIN_M <= r <= cfg.SONAR_MAX_CM / 100.0
        self.raw.append(r if ok else None)
        valid = sorted(v for v in self.raw if v is not None)
        if len(valid) < 2:                      # one echo in three: a spike or a dropout
            self.value = None
            return
        self.value = valid[len(valid) // 2]
        self.last_valid = ts
        self.new_valid += 1
        self.window.append((ts, self.value))
        while self.window and ts - self.window[0][0] > cfg.SONAR_STATIC_S:
            self.window.popleft()
        vals = [v for _, v in self.window]
        span = self.window[-1][0] - self.window[0][0]
        self.static = span >= cfg.SONAR_STATIC_S * 0.9 and max(vals) - min(vals) < cfg.SONAR_STATIC_TOL_M


class SonarFusion:
    def __init__(self):
        self.channels = {name: _Channel(name, zone, yaw) for name, (zone, yaw) in cfg.SONAR_MOUNT.items()}
        self.passes = deque(maxlen=20)          # finished passes, newest last
        self._last_ts = -1e9

    @property
    def contacts(self):
        return [ch.contact for ch in self.channels.values() if ch.contact is not None]

    def hud_state(self):
        """Contacts worth showing on the OLED: {zone: tier}."""
        out = {}
        for c in self.contacts:
            if c.tier >= cfg.HUD_MIN_TIER:
                out[c.zone] = max(out.get(c.zone, 0), c.tier)
        return out

    def update(self, tracks, t, link, shaky=False, now=None):
        """Once per frame: consume new readings, update contacts, hand off to/from camera tracks.
        `t` is the pipeline clock (track times); `now` the monotonic clock (sensor times)."""
        now = time.monotonic() if now is None else now
        for ch in self.channels.values():
            ch.new_valid = 0
        for ts, vals in list(getattr(link, "sonar_hist", ())):
            if ts <= self._last_ts:
                continue
            self._last_ts = ts
            if shaky:
                continue                         # sensors swing with the head: not trustworthy
            for name, ch in self.channels.items():
                ch.feed(ts, vals.get(name))
        by_id = {tr.id: tr for tr in tracks}
        for ch in self.channels.values():
            active = ch.value is not None and not ch.static and now - ch.last_valid < cfg.SONAR_LOST_S
            if not active:
                if ch.contact is not None and (ch.static or now - ch.last_valid >= cfg.SONAR_LOST_S):
                    if not ch.static:                    # echo gone = it passed; static = it stopped / a wall
                        self._end(ch.contact)
                    ch.contact = None
                continue
            c = ch.contact
            if c is None:
                c = ch.contact = Contact(ch.name, ch.zone, now)
            c.readings += ch.new_valid
            c.range_m = ch.value
            c.near_m = cfg.SONAR_OFFSET_M + ch.value * math.cos(ch.yaw)
            c.behind_m = ch.value * math.sin(ch.yaw)
            c.clearance_m = c.near_m - cfg.RIDER_HALF_WIDTH_M
            c.min_clearance = min(c.min_clearance, c.clearance_m)
            if c.track_id is None or c.track_id not in by_id:
                self._link(c, tracks, t)
            tr = by_id.get(c.track_id)
            if tr is not None:
                tr.measured_clearance_m = c.clearance_m
                if tr.matched_now:                # still at the frame edge: sonar beats the box
                    tr.clearance_m = c.clearance_m
                    if tr.alongside:
                        tr.lat_m = c.x_center
            self._grade(c)
            c.peak_tier = max(c.peak_tier, c.tier)

    def _link(self, c, tracks, t):
        """Hand-off: the camera track that was just on this side, if any."""
        best = None
        for tr in tracks:
            if tr.zone != c.zone or tr.lat_m is None:
                continue
            visible_close = tr.matched_now and (tr.alongside or (tr.dist_m is not None and tr.dist_m < 4.0))
            just_lost = not tr.matched_now and t - tr.last_t <= cfg.SONAR_HANDOFF_S
            if not (visible_close or just_lost):
                continue
            key = (tr.was_approaching, tr.last_t)
            if best is None or key > best[0]:
                best = (key, tr)
        if best is None:
            return
        tr = best[1]
        c.track_id, c.label, c.confirmed = tr.id, tr.label, bool(tr.was_approaching)
        c.fired_tier, c.fired_t = tr.fired_tier, tr.fired_t

    def _grade(self, c):
        clr = c.clearance_m
        if c.confirmed:
            close_m = cfg.CLASS_CLOSE_PASS_M.get(c.label, cfg.CLOSE_PASS_M)
            if clr < close_m:
                c.tier, c.reason = 3, f"close pass {clr:.2f} m measured"
            else:
                c.tier, c.reason = 2, f"beside you {clr:.2f} m measured"
        elif c.readings >= cfg.SONAR_CONFIRM_READINGS and c.range_m <= cfg.SONAR_CONTACT_M:
            c.tier, c.reason = 2, f"object beside you {clr:.2f} m"
        else:
            c.tier, c.reason = 0, ""

    def _end(self, c):
        if c.track_id is None and c.peak_tier == 0:
            return                               # a brief unclassified echo: not worth logging
        side = "left" if c.zone == LEFT else "right"
        what = c.label if c.label != "unknown" else "something"
        entry = {"label": c.label, "zone": c.zone, "min_clearance_m": round(c.min_clearance, 2),
                 "confirmed": c.confirmed, "duration_s": round(time.monotonic() - c.started, 1)}
        self.passes.append(entry)
        print(f"[PASS] {what} passed on your {side} at {c.min_clearance:.2f} m"
              + ("" if c.confirmed else " (not seen by the camera)"))
