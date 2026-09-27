"""Beeps in the earbuds: which alerts beep, the cooldowns, and the sound itself.
Run: python -m tests.test_beeps (no audio device needed; a fake output records what would play).
"""
import threading
import unittest
from collections import deque
from unittest.mock import patch

import numpy as np

from helmet import config as cfg
from helmet.outputs import BEEP_RATE, AudioOut, Beeper, HelmetLink, beep_clip


class FakeOut:
    status = "fake"

    def __init__(self):
        self.plays = []

    def play(self, kind, side):
        self.plays.append((kind, side))

    def close(self):
        pass


def beeper():
    out = FakeOut()
    return Beeper(out), out


@patch.object(cfg, "BEEP_ENABLED", True)
class CooldownTests(unittest.TestCase):
    def test_only_high_rear_alerts_beep(self):
        b, out = beeper()
        self.assertFalse(b.rear(1, "L", now=0))
        self.assertFalse(b.rear(2, "L", now=10))
        self.assertTrue(b.rear(3, "L", now=20))
        self.assertEqual(out.plays, [("rear", "L")])
        with patch.object(cfg, "BEEP_TIERS", (2, 3)):
            self.assertTrue(b.rear(2, "R", now=30))

    def test_same_warning_waits_for_the_repeat_window(self):
        b, out = beeper()
        self.assertTrue(b.rear(3, "L", now=0))
        self.assertFalse(b.rear(3, "L", now=cfg.BEEP_REPEAT_S - 0.1))
        self.assertTrue(b.rear(3, "L", now=cfg.BEEP_REPEAT_S))
        self.assertEqual(len(out.plays), 2)

    def test_other_side_waits_for_the_gap_and_is_dropped_not_delayed(self):
        b, out = beeper()
        self.assertTrue(b.rear(3, "L", now=0))
        self.assertFalse(b.rear(3, "R", now=cfg.BEEP_MIN_GAP_S - 0.1))   # dropped
        self.assertTrue(b.rear(3, "R", now=cfg.BEEP_MIN_GAP_S + 0.5))
        self.assertTrue(b.rear(3, "B", now=cfg.BEEP_MIN_GAP_S * 2 + 0.5))
        self.assertEqual(out.plays, [("rear", "L"), ("rear", "R"), ("rear", "B")])

    def test_front_brake_beeps_once_when_it_starts(self):
        b, out = beeper()
        states = [(0.0, None), (0.1, "CLEAR"), (0.2, "CAUTION"), (0.3, "BRAKE"), (0.4, "BRAKE"),
                  (1.0, "CAUTION"), (1.2, "BRAKE"),                     # flicker: inside the repeat window
                  (2.0, "CLEAR"), (3.0, "UNAVAILABLE"), (4.5, "BRAKE"), (4.6, "BRAKE")]
        beeped = [t for t, state in states if b.front(None if state is None else {"state": state}, now=t)]
        self.assertEqual(beeped, [0.3, 4.5])
        self.assertEqual(out.plays, [("front", "B")] * 2)

    def test_front_brake_is_not_blocked_by_a_recent_rear_beep(self):
        b, out = beeper()
        b.rear(3, "L", now=0)
        self.assertTrue(b.front({"state": "BRAKE"}, now=0.2))
        self.assertFalse(b.rear(3, "R", now=0.5))                  # but it does hold off the next rear beep
        self.assertEqual(out.plays, [("rear", "L"), ("front", "B")])

    def test_switches(self):
        b, out = beeper()
        with patch.object(cfg, "BEEP_FRONT_BRAKE", False):
            self.assertFalse(b.front({"state": "BRAKE"}, now=0))
        with patch.object(cfg, "BEEP_ENABLED", False):
            self.assertFalse(b.rear(3, "L", now=10))
        self.assertEqual(out.plays, [])
        with patch.object(cfg, "BEEP_ENABLED", False):
            self.assertIsNone(Beeper().out)                        # disabled: no audio device opened
            self.assertEqual(Beeper().status, "off")


class SoundTests(unittest.TestCase):
    def test_rear_is_panned_to_its_side(self):
        for side, louder in (("L", 0), ("R", 1)):
            clip = beep_clip("rear", side, volume=0.3)
            rms = np.sqrt((clip ** 2).mean(axis=0))
            self.assertGreater(rms[louder], 5 * rms[1 - louder], side)
        both = beep_clip("rear", "B", volume=0.3)
        self.assertAlmostEqual(float(np.abs(both[:, 0]).max()), float(np.abs(both[:, 1]).max()), places=5)

    def test_shape_volume_and_no_clicks(self):
        for kind, notes in (("rear", 2), ("front", 3)):
            clip = beep_clip(kind, "B", volume=0.3)
            self.assertEqual((clip.dtype, clip.shape[1]), (np.float32, 2))
            self.assertLessEqual(float(np.abs(clip).max()), 0.3 + 1e-6)
            self.assertLess(len(clip) / BEEP_RATE, 0.35)            # short: well under half a second
            self.assertLess(float(np.abs(clip[[0, -1]]).max()), 1e-3)   # starts and ends at silence
            envelope = np.convolve(np.abs(clip[:, 0]), np.ones(240) / 240, mode="same")   # 5 ms average
            loud = envelope > 0.02
            self.assertEqual(int(np.sum(np.diff(loud.astype(int)) == 1)) + int(loud[0]), notes, kind)

    def test_front_is_higher_and_quicker_than_rear(self):
        def pitch(clip):
            spectrum = np.abs(np.fft.rfft(clip[:, 0]))
            return np.fft.rfftfreq(len(clip), 1 / BEEP_RATE)[np.argmax(spectrum)]
        self.assertGreater(pitch(beep_clip("front", "B")), pitch(beep_clip("rear", "B")) + 150)

    def test_lead_in_is_silent(self):
        clip = beep_clip("rear", "L", lead_in_s=0.25)
        self.assertEqual(float(np.abs(clip[: int(0.25 * BEEP_RATE)]).max()), 0.0)

    def test_stream_plays_queued_clips_back_to_back_then_silence(self):
        out = AudioOut.__new__(AudioOut)                           # just the mixing callback, no device
        out._queue, out._pos, out._lock = deque(), 0, threading.Lock()
        a, b = np.full((300, 2), 0.1, np.float32), np.full((200, 2), 0.2, np.float32)
        out._queue.extend([a, b])
        got = []
        for frames in (128, 128, 128, 128, 128):
            buf = np.empty((frames, 2), np.float32)
            out._fill(buf, frames, None, None)
            got.append(buf.copy())
        got = np.concatenate(got)
        self.assertTrue(np.all(got[:300] == 0.1) and np.all(got[300:500] == 0.2) and np.all(got[500:] == 0))
        self.assertEqual(len(out._queue), 0)



class BuzzerTests(unittest.TestCase):
    def test_optional_buzzer_chirp_command_stays_in_the_firmware_range(self):
        link = HelmetLink("/dev/null-no-arduino")
        for n in (2, 7, -1):
            link.chirp(n)
        self.assertEqual(list(link.tx_log)[-3:], ["C2", "C3", "C0"])


if __name__ == "__main__":
    unittest.main()
