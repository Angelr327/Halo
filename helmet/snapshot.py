"""The snapshot feed: a small JSON picture of the scene for the 2.5D web view.

Every frame (up to ~15 times a second) the main loop turns the tracker's state into a few hundred bytes:
where each car is (metres), its confirmed alert tier, its predicted path, what the OLED is
showing, and the ultrasonic readings. The phone draws the 3D scene from these numbers, so
the Pi never renders or sends video for the view.

Coordinates are the rider's: x = metres to the right (negative = left), z = metres behind.
"""
import math
import time

from . import config as cfg

MAX_Z = 40.0          # beyond this the monocular distance is too rough to draw
ALONGSIDE_Z = 1.0     # a box cut off by the frame edge is beside the rider: draw it there


def _r(v, nd=2):
    return None if v is None or not math.isfinite(v) else round(v, nd)


def build(tracks, t, *, hud_state, fault=False, shaky=False, light=0, fps=0.0, det_ms=0.0,
          link=None, profile="", captions=(), scene=None, contacts=(), collision=None, incidents=None):
    cars = []
    for tr in tracks:
        if tr.lat_m is None or tr.dist_m is None:
            continue                                     # not measured yet (too few frames / too small)
        if not tr.matched_now and tr.coasting(t):
            continue                                     # lost: let the view fade it out
        cars.append({
            "id": tr.id,
            "label": tr.label,
            "x": _r(tr.lat_m),
            "z": _r(ALONGSIDE_Z if tr.alongside else min(tr.dist_m, MAX_Z)),
            "zone": tr.zone,
            "tier": tr.shown_tier,
            "ttc": _r(tr.ttc, 1),
            "path": _r(tr.pred_lat_m),
            "on_path": bool(tr.on_path),
            "alongside": bool(tr.alongside),
            "live": bool(tr.matched_now),
            "reason": tr.reason,
        })
    # Side-sensor contacts (fusion.py): a camera track that is now beside you gets its measured
    # gap and is drawn where the sensor put it; an echo the camera never saw is an "unknown".
    by_id = {c["id"]: c for c in cars}
    for k in contacts:
        beside = {"x": _r(k.x_center), "z": _r(max(k.behind_m, 0.2)), "measured": _r(k.clearance_m)}
        entry = by_id.get(k.track_id) if k.track_id is not None else None
        if entry is not None:
            entry["measured"] = beside["measured"]
            if k.tier > entry["tier"]:
                entry["tier"], entry["reason"] = k.tier, k.reason
            if entry["alongside"] or not entry["live"]:
                entry.update(beside)
            continue
        cars.append({"id": k.track_id if k.track_id is not None else f"sonar-{k.sensor}", "label": k.label,
                     "zone": k.zone, "tier": k.tier, "ttc": None, "path": None, "on_path": False,
                     "alongside": True, "live": True, "reason": k.reason, "sonar_only": k.track_id is None,
                     **beside})
    sonar = {}
    if link is not None and link.sonar_fresh():
        sonar = {k: _r(v) for k, v in link.sonar.items()}
    caption = None
    if captions:
        ts, src, text = list(captions)[-1]                # latest spoken line, if recent
        age = time.monotonic() - ts
        if age < 6.0:
            caption = {"text": text, "src": src, "age": round(age, 1)}
    front = []
    if collision and collision.get("obstacles") is not None:   # camera-only mode: everything tracked ahead
        for o in collision["obstacles"]:
            if o.get("x") is None or o.get("z") is None:
                continue
            front.append({"id": o["id"], "source": "front", "label": o["label"], "detected_label": o["label"],
                          "display_asset": o.get("display_asset"), "x": _r(o["x"]),
                          "z": _r(-min(o["z"], MAX_Z)),  # shared world: positive behind, negative ahead
                          "tier": o["tier"], "live": True, "ttc": _r(o.get("ttc"), 1), "on_path": bool(o["on_path"]),
                          "path": None, "alongside": False, "approx": True,
                          "reason": collision["reason"] if o["tier"] else ""})
    elif collision and collision.get("valid") and collision.get("x") is not None and collision.get("z") is not None:
        front.append({"id": collision["target_id"], "source": "front", "label": "chair",
                      "detected_label": "chair", "display_asset": "tree", "x": _r(collision["x"]),
                      "z": _r(-collision["z"]),  # shared world: positive behind, negative ahead
                      "tier": {"BRAKE": 3, "CAUTION": 2}.get(collision["state"], 0),
                      "live": True, "ttc": _r(collision.get("ttc_s")), "on_path": collision["on_path"],
                      "path": None, "alongside": False, "reason": collision["reason"]})
    return {
        "t": round(t, 3),
        "cars": cars,
        "front_obstacles": front,
        "collision": dict(collision) if collision else None,
        "hud": dict(hud_state),                          # exactly what the OLED is showing
        "fault": bool(fault),
        "shaky": bool(shaky),
        "light": light,
        "fps": round(fps, 1),
        "det_ms": round(det_ms),
        "serial": link.status if link is not None else "",
        "sonar": sonar,
        "profile": profile,
        "caption": caption,
        "scene": scene,
        "corridor_half_m": cfg.RIDER_HALF_WIDTH_M + cfg.CORRIDOR_MARGIN_M,
        "demo_person": bool(cfg.DEMO_PERSON_AS_VEHICLE),   # people stand in for vehicles (stationary demo)
        "sonar_mount": {k: {"zone": z, "yaw": y, "offset": cfg.SONAR_OFFSET_M} for k, (z, y) in cfg.SONAR_MOUNT.items()},
        "incidents": incidents or {"latest": None, "recording": False},   # app refreshes its list when latest changes
    }
