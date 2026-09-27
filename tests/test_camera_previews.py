"""Independent camera feeds and on-demand capture, without camera hardware.

Run: python -m tests.test_camera_previews
"""
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import cv2
import numpy as np

from helmet import sources
from helmet.streamer import Streamer


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for camera state")


def read_frame(response):
    while True:
        line = response.readline()
        if not line:
            raise AssertionError("Camera stream ended before a frame arrived")
        if line.strip() == b"--frame":
            break
    headers = {}
    while line := response.readline().strip():
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    jpeg = response.read(int(headers["content-length"]))
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


class CameraStreamTests(unittest.TestCase):
    def setUp(self):
        self.streamer = Streamer(0)
        self.base = f"http://127.0.0.1:{self.streamer.server.server_port}"

    def tearDown(self):
        self.streamer.close()

    def get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=3)

    def test_front_rear_and_debug_are_independent(self):
        front = np.full((480, 640, 3), (0, 0, 255), np.uint8)
        rear = np.full((480, 640, 3), (255, 0, 0), np.uint8)
        debug = np.full((480, 960, 3), (0, 255, 0), np.uint8)
        self.streamer.publish_camera("front", front)
        self.streamer.publish_camera("rear", rear)
        self.streamer.publish(debug)
        with self.get("/stream.mjpg?camera=front") as f, self.get("/stream.mjpg?camera=rear") as r:
            self.assertGreater(read_frame(f)[:, :, 2].mean(), 250)
            back = read_frame(r)
            self.assertGreater(back[:, :, 0].mean(), 250)
            self.assertEqual(back.shape, (360, 480, 3))
            self.assertFalse(self.streamer.wants_frames(), "camera viewers must not request debug rendering")
            with self.get("/stream.mjpg") as d:
                overlay = read_frame(d)
                self.assertEqual(overlay.shape, debug.shape)
                self.assertGreater(overlay[:, :, 1].mean(), 250)
        wait_for(lambda: not self.streamer.wants_frames("front") and not self.streamer.wants_frames("rear"))

    def test_missing_and_stale_frames_show_placeholder_then_recover(self):
        with self.get("/stream.mjpg?camera=front") as response:
            unavailable = read_frame(response)
            self.assertEqual(unavailable.shape, (360, 480, 3))
            self.assertLess(unavailable.mean(), 30)
        frame = np.full((360, 480, 3), 255, np.uint8)
        self.streamer.publish_camera("front", frame, captured_at=time.monotonic() - 60)
        with self.get("/stream.mjpg?camera=front") as response:
            self.assertLess(read_frame(response).mean(), 30, "stale frames must not look live")
            self.streamer.publish_camera("front", frame)
            while (recovered := read_frame(response)).mean() < 250:
                pass
            self.assertGreater(recovered.mean(), 250)

    def test_preview_routes_enforce_token_and_validate_camera(self):
        with patch("helmet.streamer.cfg.STREAM_TOKEN", "preview-secret"):
            for path in ("/stream.mjpg?camera=front", "/stream.mjpg?camera=rear&t=wrong"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.get(path)
                self.assertEqual(error.exception.code, 403)
            with self.get("/stream.mjpg?camera=front&t=preview-secret") as response:
                self.assertIsNotNone(read_frame(response))
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get("/stream.mjpg?camera=unknown&t=preview-secret")
            self.assertEqual(error.exception.code, 404)


class CaptureTests(unittest.TestCase):
    def test_front_capture_opens_only_while_watched_and_can_reopen(self):
        wanted, published, opened = [False], [], []

        class FakeCamera:
            def __init__(self, index, rotate_180):
                self.index, self.rotate_180, self.released = index, rotate_180, False
                opened.append(self)

            def read(self, timeout):
                time.sleep(0.01)
                return np.zeros((48, 64, 3), np.uint8), time.monotonic()

            def release(self):
                self.released = True

        with patch.object(sources, "RecoveringCamera", FakeCamera):
            preview = sources.CameraPreview(7, True, lambda: wanted[0], published.append)
            try:
                time.sleep(0.15)
                self.assertFalse(opened)
                wanted[0] = True
                wait_for(lambda: len(published) > 0)
                self.assertEqual((opened[0].index, opened[0].rotate_180), (7, True))
                wanted[0] = False
                wait_for(lambda: opened[0].released and published[-1] is None)
                wanted[0] = True
                wait_for(lambda: len(opened) == 2)
            finally:
                preview.close()
            self.assertTrue(all(cam.released for cam in opened))
            self.assertFalse(preview._thread.is_alive())

    def test_busy_front_camera_retries_without_stopping_preview_worker(self):
        camera = Mock()
        def read(timeout):
            time.sleep(0.01)
            return np.zeros((48, 64, 3), np.uint8), time.monotonic()
        camera.read.side_effect = read
        published = []
        with patch.object(sources, "open_camera", side_effect=[RuntimeError("Device or resource busy"), camera]):
            preview = sources.CameraPreview(0, False, lambda: True, published.append)
            try:
                wait_for(lambda: len(published) > 0)
            finally:
                preview.close()
            camera.release.assert_called_once()

    def test_pi_release_closes_camera_even_when_stop_fails(self):
        camera = sources.PiCamera.__new__(sources.PiCamera)
        camera.cam = Mock()
        camera.cam.stop.side_effect = RuntimeError("camera disconnected")
        camera.release()
        camera.cam.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
