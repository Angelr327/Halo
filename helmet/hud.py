"""Transparent OLED HUD: big directional arrows for blind-spot threats.

Hardware: 1.51" transparent OLED, 128x64, SSD1309 (Waveshare and similar), via luma.oled.
The eye can't focus on a screen this close, so it shows only large shapes readable in
peripheral vision: an arrow on the side of the threat, chevrons for "behind".
  MED  -> outlined shape       HIGH -> filled shape, blinking (never fully off)

Everything is drawn from the RIDER's point of view, then transformed for how the panel is
mounted (HUD_ROTATE_180 for upside down, HUD_MIRROR if the rider reads it through the back
of the glass). The debug overlay previews the rider's view, so what you see on the laptop
is what the rider sees on the helmet.

The fast path only hands over a small state; SPI/I2C writes happen on this module's own
thread, so a slow or missing display can never delay an alert.
"""
import threading
import time

import cv2
import numpy as np

from . import config as cfg
from .perception import CENTER, LEFT, RIGHT

W, H = 128, 64

# Rider-view shapes (x right, y down). Left arrow: tip at the far left, points at the threat.
_LEFT_ARROW = np.array([(2, 32), (24, 6), (24, 20), (42, 20), (42, 44), (24, 44), (24, 58)], np.int32)
_RIGHT_ARROW = np.array([(W - 1 - x, y) for x, y in _LEFT_ARROW], np.int32)
_CHEVRONS = [np.array([(48, 10 + dy), (64, 24 + dy), (80, 10 + dy), (80, 20 + dy), (64, 34 + dy), (48, 20 + dy)],
                      np.int32) for dy in (0, 20)]          # two "V"s pointing down: from behind


def threat_state(tracks):
    """Highest confirmed tier per zone among live tracks, e.g. {LEFT: 3, CENTER: 2}."""
    state = {}
    for tr in tracks:
        if tr.matched_now and tr.zone and tr.shown_tier >= cfg.HUD_MIN_TIER:
            state[tr.zone] = max(state.get(tr.zone, 0), tr.shown_tier)
    return state


def render(state, fault=False, blink_on=True):
    """Rider-view frame: uint8 (64, 128), 255 = lit pixel."""
    img = np.zeros((H, W), np.uint8)
    if fault:
        cv2.line(img, (44, 12), (84, 52), 255, 5)          # big X = rear camera offline
        cv2.line(img, (84, 12), (44, 52), 255, 5)
        return img
    for zone, shapes in ((LEFT, [_LEFT_ARROW]), (RIGHT, [_RIGHT_ARROW]), (CENTER, _CHEVRONS)):
        tier = state.get(zone, 0)
        if tier < cfg.HUD_MIN_TIER:
            continue
        for poly in shapes:
            if tier >= 3 and blink_on:
                cv2.fillPoly(img, [poly], 255)
            else:
                cv2.polylines(img, [poly], True, 255, 2)
    return img


def to_panel(img):
    """Rider view -> pixels as the physical panel must show them."""
    if cfg.HUD_ROTATE_180:
        img = img[::-1, ::-1]
    if cfg.HUD_MIRROR:
        img = img[:, ::-1]
    return np.ascontiguousarray(img)


def _open_device():
    from luma.core.interface.serial import i2c, spi
    from luma.oled.device import ssd1306, ssd1309
    if cfg.HUD_INTERFACE == "i2c":
        serial = i2c(port=1, address=cfg.HUD_I2C_ADDRESS)
    else:
        serial = spi(port=0, device=0, gpio_DC=cfg.HUD_GPIO_DC, gpio_RST=cfg.HUD_GPIO_RST,
                     bus_speed_hz=8_000_000)
    device = {"ssd1309": ssd1309, "ssd1306": ssd1306}[cfg.HUD_DRIVER](serial, width=W, height=H, rotate=0)
    device.contrast(255)                                   # transparent panels are dim; run them bright
    return device


class Hud:
    def __init__(self, enabled=True):
        self.device = None
        self.status = "off"
        self.preview = render({})                           # rider view, for the debug overlay
        self._state, self._fault = {}, False
        self._held = {}                                     # zone -> (tier, until)
        self._running = False
        if not (enabled and cfg.HUD_ENABLED):
            return
        try:
            self.device = _open_device()
            self.status = f"{cfg.HUD_DRIVER} over {cfg.HUD_INTERFACE}"
        except Exception as e:                              # no display, no luma, no SPI: preview only
            hint = ""
            if "RPi" in repr(e) or "GPIO" in repr(e) or "SOC peripheral" in repr(e):
                hint = " (Pi 5: pip install rpi-lgpio)"
            self.status = f"preview only: {type(e).__name__}: {e}"[:90] + hint
        self._running = True
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def update(self, tracks, fault=False):
        """Called once per frame from the main loop. Cheap: no drawing, no I/O."""
        now = time.monotonic()
        for zone, tier in threat_state(tracks).items():
            old, until = self._held.get(zone, (0, 0.0))
            self._held[zone] = (max(tier, old if now < until else 0), now + cfg.HUD_HOLD_S)
        self._state = {z: tier for z, (tier, until) in self._held.items() if now < until}
        self._fault = fault
        if self._running:
            self._wake.set()

    def _loop(self):
        shown = None
        while self._running:
            self._wake.wait(timeout=0.05)                   # also wakes itself to animate blinking
            self._wake.clear()
            blink_on = (time.monotonic() * cfg.HUD_BLINK_HZ) % 1.0 < 0.5
            img = render(self._state, self._fault, blink_on)
            self.preview = img
            if self.device is not None and (shown is None or not np.array_equal(img, shown)):
                try:
                    from PIL import Image
                    self.device.display(Image.fromarray(to_panel(img)).convert("1"))
                    shown = img
                except Exception as e:
                    self.status = f"display error: {e}"[:90]
                    self.device = None

    def close(self):
        self._running = False
        if self.device is not None:
            try:
                self.device.clear()
            except Exception:
                pass
