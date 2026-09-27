"""Forward policy, real ArUco projection, replay, and independent dual-camera workers.
Run: python -m tests.test_collision (no YOLO download, cameras, GPIO, or network).
"""
import json
import io
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys
from contextlib import redirect_stdout
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np

from helmet.collision import Calibration, CollisionPolicy, MarkerRanger, Pose, Settings, stopping_distance
from helmet.forward import DualLiveSource, DualReplaySource, ForwardEngine
from helmet.hud import Hud, render, to_panel
from helmet.perception import Detection
from helmet import snapshot
from helmet import config as cfg
from helmet.sources import RecoveringCamera


def calibration():
    return Calibration((640, 480), np.array([[800., 0, 320], [0, 800., 240], [0, 0, 1]]),
                       np.zeros(5), 0.20, 0, np.diag([1., -1., -1.]), 0.6, 0.10, 0.60)


def pose(z, x=0, yaw=0, pitch=0):
    return Pose(x, z, yaw, pitch, 0, (320, 240))


def marker_frame(z, yaw=0, x=0):
    cal = calibration()
    a = math.radians(yaw)
    rotation = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    rv, _ = cv2.Rodrigues(rotation @ cal.reference_rotation)
    tv = rotation @ np.array([x, 0., z])
    points, _ = cv2.projectPoints(MarkerRanger(cal).points, rv, tv, cal.camera_matrix, cal.dist_coeffs)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 0, 400)
    transform = cv2.getPerspectiveTransform(np.array([[0, 0], [399, 0], [399, 399], [0, 399]], np.float32),
                                           points.reshape(4, 2).astype(np.float32))
    gray = cv2.warpPerspective(marker, transform, (640, 480), borderValue=255, flags=cv2.INTER_NEAREST)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


class PolicyTests(unittest.TestCase):
    def run_path(self, fn, count=40):
        policy = CollisionPolicy(calibration())
        results = [policy.update(fn(i / 20), 100 + i / 20, associated=True) for i in range(count)]
        return policy, results

    def test_stationary_receding_and_off_path(self):
        for fn in [lambda t: pose(2), lambda t: pose(3 + t), lambda t: pose(6 - 2*t, x=3)]:
            _, rows = self.run_path(fn)
            self.assertFalse(any(r['state'] == 'BRAKE' for r in rows))
            self.assertTrue(rows[-1]['valid'])
        self.assertEqual(self.run_path(lambda t: pose(2))[1][-1]['trend'], 'stationary')

    def test_approach_speed_distance_and_boundary(self):
        policy, rows = self.run_path(lambda t: pose(8 - 2*t), 65)
        ready = [r for r in rows if r['valid']]
        self.assertTrue(ready)
        self.assertAlmostEqual(ready[0]['speed_mps'], 2.0, places=6)
        self.assertAlmostEqual(ready[0]['distance_m'], ready[0]['z'] - 0.7, places=5)
        brake = next(r for r in rows if r['state'] == 'BRAKE')
        # Trigger before the nominal physical boundary thanks to uncertainty allowance.
        self.assertGreater(brake['distance_m'], stopping_distance(2, Settings()))
        self.assertAlmostEqual(stopping_distance(2, Settings()), 4.583333333, places=6)
        self.assertGreater(stopping_distance(2, Settings(), age_s=0.29), stopping_distance(2, Settings()))
        json.dumps(rows, allow_nan=False)

    def test_turn_loss_recovery_and_timestamp_reset(self):
        p, _ = self.run_path(lambda t: pose(4 - t), 21)
        self.assertEqual(p.result['state'], 'BRAKE')
        r = p.update(pose(3, yaw=20), 101.05, associated=True)
        self.assertFalse(r['valid'])
        self.assertEqual(r['reason'], 'LOOK AHEAD')
        self.assertEqual(r['state'], 'BRAKE')  # bounded hold on lost alignment
        self.assertEqual(p.unavailable(102, 'LOOK AHEAD')['state'], 'UNAVAILABLE')
        for i in range(10):
            r = p.update(pose(3), 102.1 + i/20, associated=True)
            self.assertFalse(r['valid'])
        r = p.update(pose(3), 102.6, associated=True)
        self.assertTrue(r['valid'])
        r = p.update(pose(3), 0, associated=True)
        self.assertEqual(r['state'], 'INITIALIZING')
        self.assertIsNone(r['speed_mps'])

    def test_frame_gap_preserves_bounded_brake_hold(self):
        p, _ = self.run_path(lambda t: pose(4 - t), 21)
        self.assertEqual(p.result['state'], 'BRAKE')
        r = p.update(pose(3), 101.4, associated=True)
        self.assertEqual(r['state'], 'BRAKE')
        self.assertFalse(r['valid'])
        self.assertIsNone(r['speed_mps'])
        self.assertEqual(p.unavailable(102, 'LOST')['state'], 'UNAVAILABLE')

    def test_stale_invalid_unassociated_jump_and_speed_limit(self):
        for kwargs in [dict(pose=pose(3), associated=False), dict(pose=pose(3), associated=True, age_s=0.4),
                       dict(pose=pose(float('nan')), associated=True), dict(pose=None, associated=True)]:
            r = CollisionPolicy(calibration()).update(t=1, **kwargs)
            self.assertEqual(r['state'], 'UNAVAILABLE')
            self.assertIsNone(r['speed_mps'])
        p = CollisionPolicy(calibration())
        p.update(pose(5), 1, associated=True)
        self.assertIn('JUMP', p.update(pose(2), 1.05, associated=True)['reason'])
        _, rows = self.run_path(lambda t: pose(10 - 2.6*t))
        self.assertTrue(any(r['reason'] == 'ABOVE DEMO SPEED LIMIT' for r in rows))

    def test_noise_confirmation_and_braking_recovery(self):
        rng = np.random.default_rng(7)
        _, rows = self.run_path(lambda t: pose(3 + rng.normal(0, .008)))
        self.assertFalse(any(r['state'] == 'BRAKE' for r in rows))
        p, _ = self.run_path(lambda t: pose(4 - t), 21)
        results = [p.update(pose(3), 101.05 + i/20, associated=True) for i in range(40)]
        self.assertEqual(results[-1]['state'], 'CLEAR')
        self.assertGreaterEqual(next(r['t'] for r in results if r['state'] == 'CLEAR'), 101.8)


class GeometryTests(unittest.TestCase):
    def test_real_marker_detection_rotation_compensation(self):
        ranger = MarkerRanger(calibration())
        for z in (1, 2, 3, 4):
            p, reason = ranger.measure(marker_frame(z))
            self.assertIsNotNone(p, reason)
            self.assertAlmostEqual(p.z, z, delta=.12)
            self.assertAlmostEqual(p.x, 0, delta=.12)
        # Rasterized small planar markers can be ambiguous; reject, don't select
        # a convenient orientation and turn camera rotation into bike motion.
        self.assertIn('AMBIGUOUS', ranger.measure(marker_frame(3, 8))[1])
        angle = math.radians(8)
        rotation = np.array([[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])
        raw = (rotation @ calibration().reference_rotation, rotation @ np.array([0., 0., 3.]), (320, 240), 0.)
        with patch.object(ranger, 'raw_pose', return_value=(raw, '')):
            p, _ = ranger.measure(None)
            self.assertAlmostEqual(p.z, 3, places=5)
            self.assertAlmostEqual(p.x, 0, places=5)
            self.assertAlmostEqual(p.yaw, 8, places=5)
        self.assertIsNone(ranger.measure(np.zeros((480, 640, 3), np.uint8))[0])
        self.assertIn('SIZE', ranger.measure(np.zeros((200, 200, 3), np.uint8))[1])

    def test_calibration_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'cal.json')
            cal = calibration()
            cal.save(path)
            restored = Calibration.load(path)
            self.assertTrue(np.array_equal(cal.reference_rotation, restored.reference_rotation))
            cal.reference_rotation[0, 0] = 2
            with self.assertRaises(ValueError):
                cal.validate()

    def test_association_requires_exact_frame_marker_inside_one_chair(self):
        engine = ForwardEngine(calibration(), lambda r: None)
        p = pose(4)
        engine.associate([Detection(250, 150, 400, 350, .9, 'chair')], 1, p)
        self.assertEqual(engine.associated_t, 1)
        engine.associate([], 1.1, p)
        self.assertEqual(engine.associated_t, 1)  # brief dropout permitted
        frame = np.zeros((480, 640, 3), np.uint8)
        engine.process(frame, 2.1, p, '')
        self.assertEqual(engine.snapshot()['reason'], 'CHAIR NOT CONFIRMED')
        engine.associate([Detection(0, 0, 10, 10, .9, 'chair')], 2.2, p)
        self.assertEqual(engine.associated_t, 1)
        engine.close()


class OutputTests(unittest.TestCase):
    def test_brake_priority_pulse_and_watchdog(self):
        c = dict(state='BRAKE', valid=True, reason='BRAKING BOUNDARY')
        bright = render({'LEFT': 3}, fault=True, collision=c, brake_on=True)
        dim = render({}, collision=c, brake_on=False)
        self.assertGreater(np.count_nonzero(bright), np.count_nonzero(dim))
        self.assertGreater(np.count_nonzero(dim), 0)
        self.assertTrue(np.array_equal(bright, render({}, collision=c)))
        self.assertEqual(to_panel(bright).shape, (64, 128))
        hud = Hud(enabled=False)
        hud.update_collision(c)
        self.assertEqual(hud.collision['state'], 'BRAKE')
        hud._collision_updated -= 1
        self.assertEqual(hud.collision['state'], 'UNAVAILABLE')
        self.assertFalse(hud.collision['valid'])

    def test_snapshot_preserves_rear_fields_and_front_direction(self):
        _, rows = PolicyTests().run_path(lambda t: pose(4-t), 21)
        incidents = {'latest': 'rear-incident', 'recording': True}
        s = snapshot.build([], 1, hud_state={'LEFT': 2}, collision=rows[-1], incidents=incidents)
        self.assertEqual(s['hud'], {'LEFT': 2})
        self.assertEqual(s['cars'], [])
        self.assertEqual(s['incidents'], incidents)
        self.assertLess(s['front_obstacles'][0]['z'], 0)
        self.assertEqual(s['front_obstacles'][0]['detected_label'], 'chair')
        self.assertEqual(s['front_obstacles'][0]['display_asset'], 'tree')
        json.dumps(s, allow_nan=False)


class FakeDetector:
    model_name = 'stand-in'
    last_ms = 1.
    def __init__(self):
        self.active = False
        self.calls = []
    def detect(self, frame, class_map=None):
        assert not self.active, 'concurrent model inference'
        self.active = True
        try:
            self.calls.append('front' if class_map else 'rear')
            time.sleep(.001)
            return [Detection(100, 50, 540, 450, .99, 'chair' if class_map else 'car')]
        finally:
            self.active = False


class RuntimeTests(unittest.TestCase):
    def test_missing_camera_can_recover_after_startup(self):
        class Camera:
            def read(self, timeout):
                return 'frame', 1.0
            def release(self):
                pass
        with patch('helmet.sources.open_camera', side_effect=[RuntimeError('missing'), Camera()]):
            source = RecoveringCamera(1)
            self.assertEqual(source.read(timeout=.001), (None, None))
            source.retry_at = 0
            self.assertEqual(source.read(timeout=.001), ('frame', 1.0))
            source.release()

    def test_both_live_streams_share_one_inference_worker(self):
        class Camera:
            def read(self, timeout):
                time.sleep(.01)
                return np.zeros((480, 640, 3), np.uint8), time.monotonic()
            def release(self):
                pass
            def stale_for(self):
                return 0
        engine = ForwardEngine(calibration(), lambda r: None)
        engine.ranger.measure = lambda frame: (pose(4), '')
        detector = FakeDetector()
        source = DualLiveSource(Camera(), Camera(), detector, engine)
        try:
            rear_frames = 0
            end = time.monotonic() + .3
            while time.monotonic() < end:
                frame, _ = source.read(timeout=.03)
                rear_frames += frame is not None
            self.assertGreater(rear_frames, 4)
            self.assertGreater(detector.calls.count('front'), 4)
            self.assertLessEqual(len(source._inputs), 2)
        finally:
            source.release()

    def test_live_front_continues_when_rear_stops(self):
        start = time.monotonic()
        class Source:
            def __init__(self, fail=False):
                self.fail, self.running = fail, True
            def read(self, timeout=.1):
                time.sleep(.03)
                if not self.running or self.fail:
                    return None, None
                return np.zeros((480, 640, 3), np.uint8), time.monotonic()
            def stale_for(self):
                return 0.
            def release(self):
                self.running = False
        engine = ForwardEngine(calibration(), lambda r: None)
        engine.ranger.measure = lambda frame: (pose(3.5 - (time.monotonic()-start)), '')
        detector = FakeDetector()
        source = DualLiveSource(Source(fail=True), Source(), detector, engine)
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and engine.snapshot()['state'] != 'BRAKE':
                source.read(timeout=.05)
            self.assertEqual(engine.snapshot()['state'], 'BRAKE')
            self.assertGreater(len(detector.calls), 5)
            self.assertGreater(source.stale_for(), .5)
        finally:
            source.release()

    def test_replay_uses_shared_timestamps_pause_and_loop_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [str(Path(folder) / f'{side}.avi') for side in ('rear', 'front')]
            for path in paths:
                writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 10, (640, 480))
                self.assertTrue(writer.isOpened())
                for i in range(20):
                    writer.write(marker_frame(5 - i*.1))
                writer.release()
            def replay():
                rows = []
                engine = ForwardEngine(calibration(), rows.append)
                src = DualReplaySource(*paths, FakeDetector(), engine, realtime=False)
                try:
                    for _ in range(100):
                        src.read()
                        if src.ended:
                            break
                    self.assertTrue(src.ended)
                    return rows
                finally:
                    src.release()
            a, b = replay(), replay()
            self.assertEqual(a, b)
            self.assertTrue(any(r['valid'] for r in a))
            self.assertTrue(any(r['state'] == 'BRAKE' for r in a))
            stamps = Path(folder) / 'timestamps.json'
            stamps.write_text(json.dumps(dict(version=1, rear=[i*.1+.02 for i in range(20)],
                                              front=[i*.1+.04 for i in range(20)])))
            rows = []
            engine = ForwardEngine(calibration(), rows.append)
            src = DualReplaySource(*paths, FakeDetector(), engine, realtime=False, timestamps=str(stamps))
            try:
                frame, t = src.read()
                self.assertIsNotNone(frame)
                self.assertAlmostEqual(t, .02)
                src.read()
                self.assertAlmostEqual(rows[-1]['t'], .04)
            finally:
                src.release()

            # Exercise the actual main wiring, JSONL metadata, and shutdown.
            from helmet import main as M
            from helmet.incidents import IncidentRecorder
            class ReplayRecorder(IncidentRecorder):
                def __init__(self, **kwargs):
                    super().__init__(client=None, directory=str(Path(folder) / 'incidents'))
                    self.polled_times = []
                    self.marked = False
                    self.closing = False

                def add_frame(self, frame, t, tracks=(), contacts=()):
                    if not self.marked:
                        self.trigger_manual(t, tracks)
                        self.marked = True
                    super().add_frame(frame, t, tracks, contacts)

                def poll(self, t):
                    if not self.closing:
                        self.polled_times.append(t)
                    super().poll(t)

                def close(self, timeout=15):
                    self.closing = True
                    super().close(timeout)

            recorder = ReplayRecorder()
            calpath, logpath = Path(folder) / 'cal.json', Path(folder) / 'decisions.jsonl'
            calibration().save(calpath)
            argv = ['main', '--collision', '--collision-calibration', str(calpath),
                    '--collision-log', str(logpath), '--video', paths[0], '--front-video', paths[1],
                    '--headless', '--stream', '0', '--no-gemini', '--no-hud', '--no-realtime',
                    '--port', '/dev/no-arduino-collision-test']
            with patch.object(sys, 'argv', argv), patch.object(M, 'VehicleDetector', FakeDetector), \
                    patch.object(M, 'IncidentRecorder', return_value=recorder), \
                    patch.object(cfg, 'TTS_ENABLED', False), patch.object(cfg, 'HEADLESS', True), \
                    patch.object(cfg, 'STREAM_PORT', None), patch.object(cfg, 'GEMINI_ENABLED', False), \
                    redirect_stdout(io.StringIO()):
                M.main()
            self.assertTrue(recorder.polled_times)
            self.assertLessEqual(max(recorder.polled_times), 1.9 + 1e-6,
                                 'front-only replay events must poll incidents in media time')
            reports = recorder.list()
            self.assertEqual(len(reports), 1)
            self.assertGreater(reports[0]['duration_s'], 1.5, 'incident must retain post-roll during paired replay')
            logs = [json.loads(line) for line in logpath.read_text().splitlines()]
            self.assertEqual(logs[0]['type'], 'session')
            self.assertEqual(logs[0]['calibration']['marker_length_m'], .2)
            self.assertTrue(any(row.get('state') == 'BRAKE' for row in logs))
            engine = ForwardEngine(calibration(), lambda r: None)
            src = DualReplaySource(*paths, FakeDetector(), engine, realtime=False, loop=True)
            try:
                src.read()
                src.toggle_pause()
                old_t = src.last_t
                self.assertEqual(src.read(), (None, None))
                self.assertEqual(src.last_t, old_t)
                src.toggle_pause()
                for _ in range(42):
                    src.read()
                self.assertGreater(src.offset, 0)
                self.assertFalse(engine.snapshot()['valid'])  # new loop must warm up
            finally:
                src.release()


class ToolTests(unittest.TestCase):
    def test_marker_and_alignment_tools_produce_loadable_calibration(self):
        from tools.calibrate_front import marker, align
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            marker(SimpleNamespace(id=0, output=str(root / 'marker.png')))
            self.assertIsNotNone(cv2.imread(str(root / 'marker.png')))
            cal = calibration()
            cal.save(root / 'intrinsics.json')
            cv2.imwrite(str(root / 'reference.png'), marker_frame(2))
            align(SimpleNamespace(intrinsics=str(root / 'intrinsics.json'), image=str(root / 'reference.png'),
                                  marker_m=.2, id=0, chair_width_m=.6, marker_to_front_m=.1,
                                  camera_to_bike_front_m=.6, output=str(root / 'aligned.json')))
            measured = Calibration.load(root / 'aligned.json')
            self.assertTrue(np.allclose(measured.reference_rotation, cal.reference_rotation, atol=.02))

    def test_pair_recording_writes_matching_timestamp_sidecar(self):
        from tools import record_pair
        class Camera:
            def read(self, timeout):
                time.sleep(.01)
                return np.zeros((480, 640, 3), np.uint8), time.monotonic()
            def stale_for(self):
                return 0
            def release(self):
                pass
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            calibration().save(root / 'cal.json')
            out = root / 'recording'
            argv = ['record_pair', '--output', str(out), '--calibration', str(root / 'cal.json'), '--seconds', '.12']
            with patch.object(sys, 'argv', argv), patch.object(record_pair, 'open_camera', return_value=Camera()):
                record_pair.main()
            stamps = json.loads((out / 'timestamps.json').read_text())
            for side in ('rear', 'front'):
                cap = cv2.VideoCapture(str(out / f'{side}.avi'))
                try:
                    self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), len(stamps[side]))
                    self.assertGreater(len(stamps[side]), 1)
                    self.assertTrue(all(b > a for a, b in zip(stamps[side], stamps[side][1:])))
                finally:
                    cap.release()


if __name__ == '__main__':
    unittest.main()
