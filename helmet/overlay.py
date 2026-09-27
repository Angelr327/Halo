"""Debug overlay. We never call YOLO's result.plot(), so its labels can't cover ours.
All numbers go in a side panel next to the image; the image only gets boxes, a short
ID tag at the box's bottom edge, guides, and big indicators the audience can read."""
import math
import time

import cv2
import numpy as np

from . import config as cfg
from .outputs import LIGHT_NAMES
from .perception import corridor_half_m, focal_px
from .risk import fmt_ttc

TIER_COLOR = {0: (170, 170, 170), 1: (0, 215, 215), 2: (0, 140, 255), 3: (0, 0, 255)}
TIER_NAME = {0: "-", 1: "LOW", 2: "MED", 3: "HIGH"}
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, s, org, scale=0.45, color=(235, 235, 235), thick=1, bg=None):
    if bg is not None:
        (tw, th), base = cv2.getTextSize(s, FONT, scale, thick)
        x, y = org
        cv2.rectangle(img, (x - 2, y - th - 3), (x + tw + 2, y + base), bg, -1)
    cv2.putText(img, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def _sparkline(img, values, x, y, w, h, color):
    cv2.rectangle(img, (x, y), (x + w, y + h), (70, 70, 70), 1)
    if len(values) < 2:
        return
    lo, hi = min(values), max(values)
    rng = max(hi - lo, 1e-6)
    pts = [(x + int(i * w / (len(values) - 1)), y + h - int((v - lo) / rng * h)) for i, v in enumerate(values)]
    cv2.polylines(img, [np.array(pts, np.int32)], False, color, 1, cv2.LINE_AA)


def _corridor_band(img, vp):
    """Your lane on the road: bike + margin, in perspective (guide only; needs CAMERA_HEIGHT_M).
    The exact per-object test is the short bar drawn under each box."""
    h, w = img.shape[:2]
    y_h = h * cfg.HORIZON_FRAC
    y_far = y_h + focal_px(w) * cfg.CAMERA_HEIGHT_M / 40.0      # stop drawing 40 m out
    if y_far >= h - 1:
        return
    half = corridor_half_m()
    # a ground point at row y is at range Z = f*H/(y - y_h); its half-width in px is f*half/Z
    hw = [half * (y - y_h) / cfg.CAMERA_HEIGHT_M for y in (y_far, h - 1)]
    poly = np.array([(vp - hw[0], y_far), (vp + hw[0], y_far), (vp + hw[1], h - 1), (vp - hw[1], h - 1)], np.int32)
    layer = img.copy()
    cv2.fillPoly(layer, [poly], (255, 255, 0))
    cv2.addWeighted(layer, 0.15, img, 0.85, 0, img)
    cv2.polylines(img, [poly], True, (255, 255, 0), 1, cv2.LINE_AA)


class Overlay:
    def __init__(self):
        self.show_panel = True

    def render(self, frame, c):
        h, w = frame.shape[:2]
        pw = cfg.PANEL_WIDTH if self.show_panel else 0
        canvas = np.zeros((h, w + pw, 3), np.uint8)
        img = frame.copy() if frame is not None else np.zeros((h, w, 3), np.uint8)
        t_wall = time.monotonic()

        # ---- guides
        vp = int(c["vp_x"])
        if cfg.ZONE_METHOD == "lateral":
            _corridor_band(img, vp)
        for yy in range(0, h, 14):
            cv2.line(img, (vp, yy), (vp, yy + 7), (255, 255, 0), 1)
        if cfg.ZONE_METHOD == "thirds":
            for xx in (w // 3, 2 * w // 3):
                cv2.line(img, (xx, 0), (xx, h), (120, 120, 120), 1)

        # ---- tracks
        for tr in c["tracks"]:
            d = tr.det
            col = TIER_COLOR[tr.shown_tier] if tr.matched_now else (90, 90, 90)
            thick = 3 if tr.shown_tier >= 2 else 1
            cv2.rectangle(img, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), col, thick)
            if tr.matched_now and not tr.alongside and cfg.ZONE_METHOD == "lateral":
                # your lane at THIS object's distance: box overlaps the bar <=> zone CENTER
                px_per_m = d.w / cfg.CLASS_WIDTH_M.get(tr.label, 1.8)
                yb = int(d.y1) + 4                   # top edge: the ID tag sits at the bottom
                hw = int(corridor_half_m() * px_per_m)
                cv2.line(img, (vp - hw, yb), (vp + hw, yb), (255, 255, 0), 2)
                if tr.pred_lat_m is not None:        # where it will be sideways at contact
                    px = int(vp + tr.pred_lat_m * px_per_m)
                    if abs(px - d.cx) > 4:
                        cv2.arrowedLine(img, (int(d.cx), yb + 10), (px, yb + 10), col, 2, tipLength=0.25)
            tag = f"#{tr.id} {tr.label} {tr.zone[0] if tr.zone else '?'}"
            ty = min(h - 4, int(d.y2) + 14)
            _text(img, tag, (int(d.x1) + 2, ty), 0.42, (0, 0, 0), 1, bg=col)

        # ---- motor indicators (audience can't feel the buzz, so show it)
        for side, x in (("L", 28), ("R", w - 28)):
            on = t_wall - c["link"].motor_flash[side] < 0.35
            cv2.circle(img, (x, 28), 18, (0, 0, 255) if on else (60, 60, 60), -1)
            _text(img, side, (x - 6, 34), 0.6, (255, 255, 255), 2)

        # ---- HUD preview: exactly what the rider sees on the transparent OLED
        hud = c.get("hud")
        if hud is not None:
            hx, hy = 8, 54
            cv2.rectangle(img, (hx - 2, hy - 2), (hx + hud.shape[1] + 1, hy + hud.shape[0] + 1), (90, 90, 90), 1)
            roi = img[hy:hy + hud.shape[0], hx:hx + hud.shape[1]]
            roi[:] = roi // 3
            roi[hud > 0] = (255, 220, 120)
            _text(img, "HUD", (hx, hy + hud.shape[0] + 14), 0.4, (170, 170, 170))

        # ---- rear light indicator
        lvl = c["light"]
        blink_hz = {0: 1.0, 1: 2.5, 2: 5.0}[lvl]
        lit = (t_wall * blink_hz) % 1.0 < 0.5
        col = {0: (0, 0, 150), 1: (0, 0, 220), 2: (0, 0, 255)}[lvl] if lit else (40, 40, 40)
        cv2.rectangle(img, (w // 2 - 60, 8), (w // 2 + 60, 30), col, -1)
        _text(img, f"LIGHT {LIGHT_NAMES[lvl]}", (w // 2 - 52, 25), 0.45, (255, 255, 255), 1)

        # ---- captions (what was just spoken)
        y = h - 12
        for ts, src, text in reversed(list(c["speaker"].captions)):
            if t_wall - ts > 6:
                continue
            _text(img, f"[{src}] {text}"[:70], (10, y), 0.55, (255, 255, 255), 1, bg=(0, 0, 0))
            y -= 24

        if c["fault"]:
            cv2.rectangle(img, (0, h // 2 - 40), (w, h // 2 + 40), (0, 0, 180), -1)
            _text(img, "CAMERA FAULT - alerts offline", (20, h // 2 + 10), 0.9, (255, 255, 255), 2)
        if c["shaky"]:
            _text(img, "SHAKE", (w - 90, 60), 0.6, (0, 255, 255), 2)
        if c.get("incident"):
            _text(img, "SAVING INCIDENT", (w // 2 - 70, 52), 0.55, (255, 255, 255), 1, bg=(0, 0, 160))
        if c["recording"] and int(t_wall * 2) % 2 == 0:
            cv2.circle(img, (60, 60), 7, (0, 0, 255), -1)
            _text(img, "REC", (72, 66), 0.5, (0, 0, 255), 2)
        if c["paused"]:
            _text(img, "PAUSED", (w // 2 - 45, 60), 0.7, (255, 255, 255), 2, bg=(0, 0, 0))

        canvas[:, :w] = img
        if self.show_panel:
            self._panel(canvas[:, w:], c)
        if cfg.DISPLAY_SCALE != 1.0:
            canvas = cv2.resize(canvas, None, fx=cfg.DISPLAY_SCALE, fy=cfg.DISPLAY_SCALE,
                                interpolation=cv2.INTER_LINEAR)
        return canvas

    def _panel(self, p, c):
        p[:] = (28, 28, 28)
        y = 16
        g, link, pol, m = c["gateway"], c["link"], c["policy"], c["motion"]

        def line(s, color=(220, 220, 220), scale=0.42):
            nonlocal y
            _text(p, s[:62], (8, y), scale, color)
            y += 16

        line(f"FPS {c['fps']:.1f}  det {c['det_ms']:.0f}ms  frame age {c['age_ms']:.0f}ms", (255, 255, 255))
        line(f"shift dx {m.dx:+.1f} dy {m.dy:+.1f}  resp {m.response:.2f}  vp {m.vp_offset:+.0f}px"
             + ("  SHAKY" if c["shaky"] else ""), (0, 255, 255) if c["shaky"] else (220, 220, 220))
        line(f"profile {pol.profile_name}  light {LIGHT_NAMES[c['light']]}  zones {cfg.ZONE_METHOD}"
             f"  mirror {'on' if cfg.MIRROR_VIEW else 'OFF'}")
        line(f"serial: {link.status}")
        if link.sonar_fresh():
            son = "  ".join(f"{k} {'-' if v is None else f'{v:.2f}m'}" for k, v in link.sonar.items())
            line(f"  sonar {son}", (200, 200, 120))
        tx = " ".join(list(link.tx_log)[-5:])
        rx = " ".join(list(link.rx_log)[-2:])
        line(f"  tx {tx}   rx {rx}", (170, 170, 170))
        gstate = "ON" if g.available else "OFF"
        line(f"Gemini {gstate} [{cfg.GEMINI_MODE}] {cfg.GEMINI_MODEL}", (140, 220, 140) if g.available else (120, 120, 255))
        line(f"  calls {g.calls}/{cfg.GEMINI_CALL_BUDGET}  {'IN FLIGHT' if g.in_flight else ''}"
             f"  last {g.last_latency or 0:.1f}s  rej {sum(g.rejected.values())}")
        line(f"  {g.status}", (170, 170, 170))
        if g.last_text:
            line(f"  > {g.last_text}", (140, 220, 140))
        if c.get("scene_note"):
            line(f"scene: {c['scene_note']}", (200, 180, 255))
        y += 4
        cv2.line(p, (0, y), (p.shape[1], y), (80, 80, 80), 1)
        y += 16

        tracks = sorted(c["tracks"], key=lambda tr: (-tr.shown_tier, tr.ttc if math.isfinite(tr.ttc) else 1e9))
        for tr in tracks[:5]:
            if y > p.shape[0] - 70:
                break
            col = TIER_COLOR[tr.shown_tier]
            stale = "" if tr.matched_now else "  (coasting)"
            raw = f" (raw {TIER_NAME[tr.tier]})" if tr.tier != tr.shown_tier else ""
            line(f"#{tr.id} {tr.label} {tr.zone or '?'}  {TIER_NAME[tr.shown_tier]}{raw}{stale}", col, 0.48)
            line(f"  box {tr.det.w:.0f}x{tr.det.h:.0f}px  grow {tr.growth:.2f}x ({tr.growth_pct_s:+.0f}%/s)"
                 f"  cons {tr.consistency:.2f} [{tr.scale_axis}]")
            clr = "-" if tr.clearance_m is None else f"{tr.clearance_m:.1f}m"
            lat = "-" if tr.lat_m is None else f"{tr.lat_m:+.1f}m"
            dist = "-" if tr.dist_m is None else f"{tr.dist_m:.0f}m"
            pred = "" if tr.pred_lat_m is None else f" ->{round(tr.pred_lat_m, 1) + 0.0:+.1f}" + (" PATH" if tr.on_path else "")
            line(f"  TTC {fmt_ttc(tr.ttc)}  dist {dist}  lat {lat}{pred}  clr {clr}"
                 + ("  EDGE" if tr.alongside else ""))
            _sparkline(p, [s.area for s in tr.hist], 12, y - 8, 150, 18, col)
            _text(p, tr.reason[:30], (170, y + 6), 0.38, (170, 170, 170))
            y += 28

        _text(p, "q quit  g ask  v voice  t test  l light  p profile", (8, p.shape[0] - 26), 0.38, (150, 150, 150))
        _text(p, "f cam-fault  k kill-hb  x gemini  a agent  m mirror  c reload  r rec  d panel",
              (8, p.shape[0] - 10), 0.38, (150, 150, 150))
