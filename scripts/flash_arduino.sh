#!/usr/bin/env bash
# Compile and upload Arduino firmware from the Pi's terminal (no Arduino IDE needed).
#   bash scripts/flash_arduino.sh              # helmet firmware (firmware/helmet_arduino)
#   bash scripts/flash_arduino.sh hcsr04_test  # the standalone HC-SR04 test sketch
# Optional: BOARD=arduino:avr:nano:cpu=atmega328old PORT=/dev/ttyUSB0 bash scripts/flash_arduino.sh
# First run needs internet (installs arduino-cli, the AVR core and the NeoPixel library, ~1-2 min).
set -euo pipefail
cd "$(dirname "$0")/.."

SKETCH="firmware/${1:-helmet_arduino}"
BOARD="${BOARD:-arduino:avr:uno}"
[ -d "$SKETCH" ] || { echo "No sketch folder $SKETCH"; exit 1; }

export PATH="$HOME/.local/bin:$PATH"
if ! command -v arduino-cli >/dev/null; then
  echo "== installing arduino-cli to ~/.local/bin"
  mkdir -p "$HOME/.local/bin"
  curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR="$HOME/.local/bin" sh
fi

if ! arduino-cli core list 2>/dev/null | grep -q "^arduino:avr"; then
  echo "== installing the Arduino AVR core (Uno/Nano support)"
  arduino-cli core update-index
  arduino-cli core install arduino:avr
fi
if grep -q "Adafruit_NeoPixel" "$SKETCH"/*.ino && ! arduino-cli lib list 2>/dev/null | grep -q "Adafruit NeoPixel"; then
  echo "== installing the Adafruit NeoPixel library"
  arduino-cli lib install "Adafruit NeoPixel"
fi

if [ -z "${PORT:-}" ]; then
  PORT=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -n1 || true)
  [ -n "$PORT" ] || { echo "No Arduino found on /dev/ttyACM* or /dev/ttyUSB*. Check the USB cable (data, not charge-only)."; exit 1; }
fi

if pgrep -f "helmet.main|tools.bench" >/dev/null; then
  echo "The helmet app or bench tool is running and holds the serial port. Stop it (q / Ctrl+C) and re-run."
  exit 1
fi

echo "== compiling $SKETCH for $BOARD"
arduino-cli compile --fqbn "$BOARD" "$SKETCH"
echo "== uploading to $PORT"
if ! arduino-cli upload -p "$PORT" --fqbn "$BOARD" "$SKETCH"; then
  echo "Upload failed. If it says 'permission denied': sudo usermod -aG dialout \$USER, then log out and back in."
  echo "Nano clones may need: BOARD=arduino:avr:nano:cpu=atmega328old bash scripts/flash_arduino.sh"
  exit 1
fi
echo "== done: $SKETCH is running on the board"
if [ "${1:-helmet_arduino}" = "helmet_arduino" ]; then
  echo "Check it with:  python -m tools.bench check"
else
  echo "Watch its output with:  arduino-cli monitor -p $PORT -c baudrate=9600   (Ctrl+C to exit)"
fi
