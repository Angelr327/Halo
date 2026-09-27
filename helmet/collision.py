"""Calibrated stationary-target demo policy. No rear blind-spot assumptions or I/O.

All policy times use the input timeline (monotonic for live, media time for replay).
The helmet is NOT a bike orientation sensor: the rider must follow the calibrated
straight approach with a consistent posture. Marker pose only gates obvious turns.
"""
from collections import deque
from dataclasses import asdict, dataclass
import json
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class Settings:
    reaction_s: float = 1.0
    decel_mps2: float = 1.5
    delay_s: float = 0.25
    margin_m: float = 0.75
    rider_half_width_m: float = 0.35
    lateral_margin_m: float = 0.25
    max_speed_mps: float = 2.0
    window_s: float = 0.5
    min_span_s: float = 0.3
    min_samples: int = 5
    realign_s: float = 0.5
    stale_s: float = 0.3
    association_s: float = 1.0
    hold_s: float = 0.8
    clear_s: float = 0.5
    yaw_deg: float = 10.0
    pitch_deg: float = 10.0
    roll_deg: float = 15.0


@dataclass
class Calibration:
    image_size: tuple
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    marker_length_m: float
    marker_id: int
    reference_rotation: np.ndarray
    chair_width_m: float
    marker_to_front_m: float
    camera_to_bike_front_m: float

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != 1:
            raise ValueError("collision calibration requires version 1")
        c = cls(tuple(d["image_size"]), np.asarray(d["camera_matrix"], dtype=float),
                np.asarray(d["dist_coeffs"], dtype=float).reshape(-1),
                float(d["marker_length_m"]), int(d["marker_id"]),
                np.asarray(d["reference_rotation"], dtype=float), float(d["chair_width_m"]),
                float(d["marker_to_front_m"]), float(d["camera_to_bike_front_m"]))
        c.validate()
        return c

    def validate(self):
        if len(self.image_size) != 2 or any(int(x) != x or x <= 0 for x in self.image_size):
            raise ValueError("invalid calibration image size")
        k, r = self.camera_matrix, self.reference_rotation
        if k.shape != (3, 3) or not np.isfinite(k).all() or min(k[0, 0], k[1, 1]) <= 0:
            raise ValueError("invalid camera matrix")
        if not np.allclose(k[2], [0, 0, 1]):
            raise ValueError("invalid camera matrix last row")
        if self.dist_coeffs.size not in (4, 5, 8, 12, 14) or not np.isfinite(self.dist_coeffs).all():
            raise ValueError("invalid distortion coefficients")
        if (r.shape != (3, 3) or not np.isfinite(r).all()
                or not np.allclose(r.T @ r, np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(r), 1, atol=1e-5)):
            raise ValueError("invalid reference rotation")
        if not 0 <= self.marker_id < 50:
            raise ValueError("marker ID must be in DICT_4X4_50")
        values = [self.marker_length_m, self.chair_width_m, self.marker_to_front_m, self.camera_to_bike_front_m]
        if not all(math.isfinite(v) for v in values) or min(values[:2]) <= 0 or min(values[2:]) < 0:
            raise ValueError("measure positive marker/chair sizes and nonnegative forward offsets")

    def save(self, path):
        self.validate()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, allow_nan=False)
            f.write("\n")

    def to_dict(self):
        d = asdict(self)
        for k in ("camera_matrix", "dist_coeffs", "reference_rotation"):
            d[k] = d[k].tolist()
        return {"version": 1, **d}


@dataclass(frozen=True)
class Pose:
    x: float
    z: float
    yaw: float
    pitch: float
    roll: float
    center: tuple
    reprojection_px: float = 0.0


class MarkerRanger:
    def __init__(self, calibration):
        calibration.validate()
        self.cal = calibration
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(dictionary, params)
        s = calibration.marker_length_m / 2
        self.points = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float64)

    def raw_pose(self, frame):
        if tuple(frame.shape[1::-1]) != self.cal.image_size:
            return None, "CALIBRATION SIZE MISMATCH"
        corners, ids, _ = self.detector.detectMarkers(frame)
        selected = [] if ids is None else [p.reshape(4, 2) for p, i in zip(corners, ids.flatten())
                                          if i == self.cal.marker_id]
        if len(selected) != 1:
            return None, "MARKER MISSING" if not selected else "AMBIGUOUS MARKER"
        p = selected[0].astype(np.float64)
        w, h = self.cal.image_size
        if (p[:, 0].min() < 3 or p[:, 1].min() < 3 or p[:, 0].max() > w - 4
                or p[:, 1].max() > h - 4 or min(np.linalg.norm(p - np.roll(p, 1, axis=0), axis=1)) < 16):
            return None, "MARKER TOO SMALL OR CLIPPED"
        # A small planar marker can admit two different orientations with nearly
        # identical reprojection error. Do not invent a precise head direction.
        # Use y-down planar coordinates for this ambiguity check, avoiding the
        # Rodrigues singularity at a frontal marker's 180-degree x rotation.
        solutions = cv2.solvePnPGeneric(self.points @ np.diag([1., -1., -1.]), p,
                                       self.cal.camera_matrix, self.cal.dist_coeffs, flags=cv2.SOLVEPNP_IPPE)
        if solutions[0] >= 2:
            if any(not np.isfinite(v).all() or np.linalg.norm(v) > 2 * math.pi for v in solutions[1][:2]):
                return None, "UNSTABLE MARKER POSE"
            r0, r1 = (cv2.Rodrigues(v)[0] for v in solutions[1][:2])
            separation = math.degrees(math.acos(float(np.clip((np.trace(r0 @ r1.T) - 1) / 2, -1, 1))))
            errors = np.asarray(solutions[3]).reshape(-1)
            if separation > 6 and abs(errors[0] - errors[1]) < 0.5:
                return None, "AMBIGUOUS MARKER POSE"
        # Refine after the planar ambiguity check.
        ok, rv, tv = cv2.solvePnP(self.points, p, self.cal.camera_matrix, self.cal.dist_coeffs,
                                flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok or not np.isfinite(rv).all() or not np.isfinite(tv).all() or tv[2, 0] <= 0:
            return None, "INVALID POSE"
        projected, _ = cv2.projectPoints(self.points, rv, tv, self.cal.camera_matrix, self.cal.dist_coeffs)
        error = float(np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - p) ** 2, axis=1))))
        if error > 2.0:
            return None, "POOR MARKER FIT"
        rotation, _ = cv2.Rodrigues(rv)
        return (rotation, tv.reshape(3), tuple(p.mean(axis=0)), error), ""

    def measure(self, frame):
        raw, reason = self.raw_pose(frame)
        if raw is None:
            return None, reason
        rotation, translation, center, error = raw
        # Marker displacement in reference camera axes, cancelling camera rotation.
        xyz = self.cal.reference_rotation @ rotation.T @ translation
        relative = rotation @ self.cal.reference_rotation.T
        pitch, yaw, roll = cv2.RQDecomp3x3(relative)[0]
        if xyz[2] <= 0:
            return None, "TARGET NOT AHEAD"
        return Pose(float(xyz[0]), float(xyz[2]), yaw, pitch, roll, center, error), ""


def stopping_distance(speed, settings, age_s=0.0):
    delay = max(settings.delay_s, max(0.0, age_s) + 0.05)
    return speed * (settings.reaction_s + delay) + speed * speed / (2 * settings.decel_mps2) + settings.margin_m


def fit_motion(samples):
    """Median pairwise slope, projected to newest sample time (no half-window lag)."""
    ts, zs = np.asarray(samples, dtype=float).T
    slopes = [(zs[j] - zs[i]) / (ts[j] - ts[i]) for i in range(len(ts))
              for j in range(i + 1, len(ts)) if ts[j] - ts[i] >= 0.05]
    slope = float(np.median(slopes))
    projected = zs - slope * (ts - ts[-1])
    z = float(np.median(projected))
    residual = float(np.median(np.abs(projected - z))) * 1.4826
    speed_error = max(0.10, 3 * residual / max(ts[-1] - ts[0], 0.1))
    return z, -slope, residual, speed_error


class CollisionPolicy:
    def __init__(self, calibration, settings=None):
        self.cal = calibration
        self.cfg = settings or Settings()
        self.reset()

    def reset(self):
        self.samples = deque()
        self.last_t = None
        self.last_pose = None
        self.aligned_since = None
        self.brake_until = -math.inf
        self.recovery_since = None
        self.crossings = 0
        self.result = self._empty("INITIALIZING", "WAITING FOR FRONT CAMERA", 0.0)

    def _empty(self, state, reason, t):
        return dict(state=state, reason=reason, t=t, source="front", target_id=f"front:marker:{self.cal.marker_id}",
                    detected_label="chair", display_asset="tree", valid=False, alignment_valid=False,
                    x=None, z=None, distance_m=None, speed_mps=None, speed_error_mps=None,
                    trend="unknown", ttc_s=None, warning_distance_m=None, measurement_age_s=None,
                    on_path=False, speed_basis="stationary target, straight approach")

    def unavailable(self, t, reason, *, reset_history=True):
        if reset_history:
            self.samples.clear()
            self.last_pose = None
            self.aligned_since = None
            self.crossings = 0
            self.recovery_since = None
        state = "BRAKE" if t < self.brake_until else "UNAVAILABLE"
        self.result = self._empty(state, reason, t)
        return dict(self.result)

    def update(self, pose, t, *, associated=False, age_s=0.0, reason="MARKER MISSING"):
        s = self.cfg
        if not math.isfinite(t):
            raise ValueError("non-finite timestamp")
        if self.last_t is not None and t <= self.last_t:
            self.reset()  # never carry velocity or BRAKE across a seek/loop
        elif self.last_t is not None and t - self.last_t > s.stale_s:
            self.unavailable(t, "FRONT FRAME GAP")  # reset speed; preserve bounded warning hold
        self.last_t = t
        if not math.isfinite(age_s) or age_s < 0 or age_s > s.stale_s:
            return self.unavailable(t, "STALE FRONT FRAME")
        if pose is None:
            return self.unavailable(t, reason)
        if not all(math.isfinite(v) for v in (pose.x, pose.z, pose.yaw, pose.pitch, pose.roll)) or pose.z <= 0:
            return self.unavailable(t, "INVALID POSE")
        if abs(pose.yaw) > s.yaw_deg or abs(pose.pitch) > s.pitch_deg or abs(pose.roll) > s.roll_deg:
            return self.unavailable(t, "LOOK AHEAD")
        if not associated:
            return self.unavailable(t, "CHAIR NOT CONFIRMED")
        if self.last_pose is not None:
            prev_t, prev = self.last_pose
            dt = t - prev_t
            if abs(pose.z - prev.z) > 0.15 + 3.0 * dt or abs(pose.x - prev.x) > 0.10 + 0.5 * dt:
                return self.unavailable(t, "EXCESSIVE HEAD MOTION OR POSE JUMP")
        self.last_pose = t, pose
        if self.aligned_since is None:
            self.aligned_since = t
        self.samples.append((t, pose.z))
        while self.samples and t - self.samples[0][0] > s.window_s + 1e-8:
            self.samples.popleft()
        r = self._empty("INITIALIZING", "COLLECTING APPROACH SPEED", t)
        r.update(x=pose.x, z=pose.z, alignment_valid=True, measurement_age_s=age_s)
        if (len(self.samples) < s.min_samples or t - self.samples[0][0] < s.min_span_s - 1e-8
                or t - self.aligned_since < s.realign_s - 1e-8):
            if t < self.brake_until:
                r["state"] = "BRAKE"
            self.result = r
            return dict(r)
        z, speed, residual, speed_error = fit_motion(self.samples)
        if abs(speed) > s.max_speed_mps + 0.2:
            return self.unavailable(t, "ABOVE DEMO SPEED LIMIT")
        if speed_error > 0.5 or residual > 0.15:
            return self.unavailable(t, "UNSTABLE RANGE")
        distance = max(0.0, z - self.cal.marker_to_front_m - self.cal.camera_to_bike_front_m)
        range_error = max(0.05 * z, 0.03, 3 * residual)
        speed_high = max(0.0, speed * 1.05 + speed_error)
        boundary = stopping_distance(speed_high, s, age_s)
        closing = speed - speed_error > 0.10
        on_path = abs(pose.x) <= self.cal.chair_width_m / 2 + s.rider_half_width_m + s.lateral_margin_m
        crossed = closing and on_path and distance - range_error <= boundary
        self.crossings = self.crossings + 1 if crossed else 0
        severe = crossed and distance - range_error <= boundary * 0.70
        if self.crossings >= 2 or severe:
            self.brake_until = t + s.hold_s
            self.recovery_since = None
        recovering = not closing or not on_path or distance - range_error > boundary + 0.3
        if recovering:
            if self.recovery_since is None:
                self.recovery_since = t
        else:
            self.recovery_since = None
        was_braking = self.result["state"] == "BRAKE"
        brake = t < self.brake_until or (was_braking and
                (self.recovery_since is None or t - self.recovery_since < s.clear_s))
        caution = closing and on_path and distance - range_error <= boundary + 1.0
        state = "BRAKE" if brake else "CAUTION" if caution else "CLEAR"
        r.update(state=state, reason="BRAKING BOUNDARY" if brake else "TARGET CLOSING" if caution else "NO QUALIFYING THREAT",
                 valid=True, distance_m=distance, speed_mps=speed, speed_error_mps=speed_error,
                 trend="closing" if closing else "receding" if speed + speed_error < -0.10 else "stationary",
                 ttc_s=distance / speed if closing else None, warning_distance_m=boundary, on_path=on_path)
        self.result = r
        return dict(r)
