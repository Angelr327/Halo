"""Camera orientation: an upside-down rear camera comes out the right way up, and the
horizontal mirror is applied on top. Run:  python -m tests.test_sources   (no camera needed)"""
import time

import cv2
import numpy as np

from helmet import config as cfg
from helmet import sources


def _marked():
    img = np.zeros((48, 64, 3), np.uint8)
    img[:8, :8] = 255                       # white square in the TOP-LEFT of the scene
    return img


class FakeCap:
    def __init__(self, *a):
        self.frame = cv2.rotate(_marked(), cv2.ROTATE_180)   # camera mounted upside down sees it bottom-right
    def set(self, *a):
        return True
    def read(self):
        time.sleep(0.01)
        return True, self.frame.copy()
    def release(self):
        pass


def test_upside_down_usb_camera_is_rotated_back():
    real = cv2.VideoCapture
    cv2.VideoCapture = FakeCap
    try:
        cam = sources.LiveCamera(0, rotate_180=True)
        frame, _ = cam.read(timeout=1.0)
        cam.release()
        upright = sources.LiveCamera(0, rotate_180=False)
        raw, _ = upright.read(timeout=1.0)
        upright.release()
    finally:
        cv2.VideoCapture = real
    assert frame[:8, :8].min() == 255 and frame[-8:, -8:].max() == 0, "rotation not applied"
    assert raw[-8:, -8:].min() == 255, "rotate_180=False must leave the frame alone"
    mirrored = cv2.flip(frame, 1)           # what main.py does next for a rear camera
    assert mirrored[:8, -8:].min() == 255   # stays at the top, now on the right: rotation + mirror = vertical flip
    print("  upside-down camera -> upright frame; MIRROR_VIEW still applied after")


def test_config_defaults_for_the_helmet():
    assert cfg.CAMERA_ROTATE_180 is True and cfg.FRONT_CAMERA_ROTATE_180 is False
    assert cfg.CAMERA_INDEX != cfg.FRONT_CAMERA_INDEX
    print(f"  rear camera {cfg.CAMERA_INDEX} upside down, front camera {cfg.FRONT_CAMERA_INDEX} upright")


def test_camera_chosen_by_connector_not_number():
    both = [{"Id": "/base/axi/pcie@1000120000/rp1/i2c@88000/ov5647@36", "Num": 0},
            {"Id": "/base/axi/pcie@1000120000/rp1/i2c@80000/ov5647@36", "Num": 1}]
    assert sources.pi_camera_num("i2c@80000", both) == 1
    only = [dict(both[1], Num=0)]                    # the other camera dropped out: numbers shift
    assert sources.pi_camera_num("i2c@80000", only) == 0
    try:
        sources.pi_camera_num("i2c@88000", only)
        raise AssertionError("a missing camera must fail loudly, not fall back to the other one")
    except RuntimeError as e:
        assert "i2c@88000" in str(e)
    print("  connector i2c@80000 found as camera 1, then as camera 0 when the other drops out")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
