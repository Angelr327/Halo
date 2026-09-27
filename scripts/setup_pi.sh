#!/usr/bin/env bash
# One-time setup on Raspberry Pi OS 64-bit. Run from the repo root, with internet:
#   bash scripts/setup_pi.sh
# Do this BEFORE the event: installs take a while and the NCNN export downloads weights.
set -euo pipefail

sudo apt update
sudo apt install -y python3-venv python3-picamera2 espeak-ng libportaudio2 ffmpeg   # ffmpeg: incident clips
sudo usermod -aG dialout,video,spi,i2c,gpio "$USER"   # Arduino serial, camera, OLED HUD
sudo raspi-config nonint do_spi 0               # transparent OLED HUD (SPI); takes effect after reboot
sudo raspi-config nonint do_i2c 0

# --system-site-packages lets the venv see apt's picamera2
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install rpi-lgpio || echo "rpi-lgpio failed: the OLED HUD will run preview-only on a Pi 5"

# Export the detector to NCNN once (fastest format on a Pi) so the first real run starts quickly
python - <<'PY'
from helmet.perception import resolve_model_path
print("Detector ready:", resolve_model_path())
PY

# Everything below needs zero hardware
python -m tests.test_synthetic
python -m tests.test_gateway
python -m tests.test_headless
python -m tests.test_incidents

echo
echo "Done. Reboot once (SPI/I2C + group changes), then:"
echo "  . .venv/bin/activate && python -m helmet.main --video your_clip.mp4"
echo "and open the printed http://<pi-ip>:8080 address on your phone or laptop."
