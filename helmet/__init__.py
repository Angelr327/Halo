import os
import sys

# pip's opencv-python bundles Qt without the Wayland plugin or any fonts, so on a Wayland
# desktop (Raspberry Pi OS) the debug window spams warnings. Use XWayland and system fonts.
# Qt reads these when the first window opens, so setting them here covers every entry point.
if sys.platform.startswith("linux"):
    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    if os.path.isdir("/usr/share/fonts/truetype/dejavu"):
        os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts/truetype/dejavu")
