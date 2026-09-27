"""Marker-free chair detection, snapshot separation, and shared camera inference.
Run: python -m tests.test_chair_demo (no physical cameras or YOLO weights needed).
"""
import json
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np

from helmet import main, snapshot
from helmet.chair_demo import ChairDemo
from helmet.forward import DualLiveSource
from helmet.perception import Detection


FRAME = np.zeros((480, 640, 3), np.uint8)


def chair(x=320, width=100, height=180, label="chair"):
    return Detection(x - width / 2, 240 - height / 2, x + width / 2, 240 + height / 2, .9, label)


def detect(engine, detections):
    t = time.monotonic()
    context = engine.capture(FRAME, t)
    engine.associate(detections, t, context)
    return engine.snapshot()


class ChairDemoTests(unittest.TestCase):
    def test_unmarked_chairs_become_trees_without_collision_metrics(self):
        engine = ChairDemo()
        self.assertFalse(detect(engine, [chair()])["obstacles"])
        result = detect(engine, [chair()])
        tree = result["obstacles"][0]
        self.assertEqual(tree["display_asset"], "tree")
        self.assertEqual(tree["placement"], "illustrative")
        self.assertLess(tree["z"], 0)
        self.assertEqual(tree["tier"], 0)
        self.assertIsNone(tree["ttc"])
        self.assertFalse(tree["on_path"])
        self.assertNotIn("distance_m", tree)
        self.assertNotIn("speed_mps", tree)
        state = snapshot.build([], 1, hud_state={"LEFT": 2}, front_demo=result)
        self.assertIsNone(state["collision"])
        self.assertEqual(state["hud"], {"LEFT": 2})
        self.assertEqual(state["front_obstacles"], [tree])
        self.assertNotIn("obstacles", state["front_demo"])
        json.dumps(state, allow_nan=False)

    def test_multiple_chairs_keep_identity_and_correct_side(self):
        engine = ChairDemo()
        detections = [chair(x=100), chair(x=540), chair(x=320, label="person")]
        detect(engine, detections)
        trees = detect(engine, detections)["obstacles"]
        self.assertEqual(len(trees), 2)
        self.assertLess(trees[0]["x"], 0)
        self.assertGreater(trees[1]["x"], 0)
        ids = [tree["id"] for tree in trees]
        self.assertEqual(ids, [tree["id"] for tree in detect(engine, detections)["obstacles"]])
        bigger = detect(engine, [chair(x=100, height=220), chair(x=540)])["obstacles"]
        self.assertLess(abs(bigger[0]["z"]), abs(trees[0]["z"]))
        self.assertFalse(detect(engine, [])["obstacles"])

    def test_lost_camera_and_stale_inference_remove_trees(self):
        engine = ChairDemo()
        detect(engine, [chair()])
        detect(engine, [chair()])
        engine.detected_at -= 5
        self.assertFalse(engine.snapshot()["available"])
        self.assertFalse(engine.snapshot()["obstacles"])
        engine.unavailable(time.monotonic(), "FRONT CAMERA OFFLINE")
        engine.associate([chair()], time.monotonic() - 5, (640, 480))
        self.assertFalse(engine.snapshot()["obstacles"])
        detect(engine, [chair()])
        self.assertEqual(len(detect(engine, [chair()])["obstacles"]), 1)

    def test_cli_needs_no_calibration_and_rejects_conflicting_modes(self):
        with patch.object(sys, "argv", ["helmet.main", "--demo-chair", "--demo-person"]):
            args = main.parse_args()
        self.assertTrue(args.demo_chair and args.demo_person)
        self.assertIsNone(args.collision_calibration)
        for extra in (["--collision"], ["--video", "rear.mp4"], ["--sim"],
                      ["--camera", "0", "--front-camera", "0"]):
            with patch.object(sys, "argv", ["helmet.main", "--demo-chair", *extra]), patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    main.parse_args()

    def test_shared_inference_continues_with_front_camera_and_no_rear(self):
        class Camera:
            def __init__(self, fail=False):
                self.fail, self.released = fail, False
            def read(self, timeout=.1):
                time.sleep(.01)
                return (None, None) if self.fail else (FRAME, time.monotonic())
            def stale_for(self):
                return 2 if self.fail else 0
            def release(self):
                self.released = True

        class Detector:
            last_ms = 0
            def detect(self, frame, class_map=None):
                if class_map != {56: "chair"}:
                    raise AssertionError("front inference must select chairs")
                return [chair()]

        rear, front, engine = Camera(fail=True), Camera(), ChairDemo()
        source = DualLiveSource(rear, front, Detector(), engine)
        try:
            deadline = time.monotonic() + 2
            while not engine.snapshot()["obstacles"] and time.monotonic() < deadline:
                source.read(timeout=.02)
            self.assertEqual(len(engine.snapshot()["obstacles"]), 1)
            self.assertLessEqual(len(source._inputs), 2)
        finally:
            source.release()
        self.assertTrue(rear.released and front.released)


if __name__ == "__main__":
    unittest.main()
