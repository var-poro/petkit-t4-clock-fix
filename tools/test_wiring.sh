#!/bin/bash
# Checks the wiring to the litter box before tools/backup.sh.
# Run BEFORE plugging in 12 V, then plug the litter box in while holding OK.
#  1) listens to what the litter box says at boot: validates GND and TX0 -> WTXD (litter -> computer)
#  2) asks for its identity: validates RX -> WRXD (computer -> litter) and download mode
# If $WORKDIR/MAC exists (written by backup.sh), the chip MAC must match it.
set -uo pipefail
cd "$(dirname "$0")/.."
DEFAULT_PY=~/.local/share/uv/tools/esphome/bin/python
PY=${PYTHON:-$( [ -x "$DEFAULT_PY" ] && echo "$DEFAULT_PY" || echo python3 )}
DEFAULT_ESPTOOL=~/.local/share/uv/tools/esphome/bin/esptool
ESPTOOL=${ESPTOOL:-$( [ -x "$DEFAULT_ESPTOOL" ] && echo "$DEFAULT_ESPTOOL" || echo esptool )}
PORT=${PORT:-$(ls /dev/cu.usbmodemflip* /dev/cu.usbserial-* 2>/dev/null | head -1 || true)}
[ -n "$PORT" ] || { echo "No USB-serial adapter."; exit 1; }
WD=${WORKDIR:-device}
EXPECTED_MAC=$( [ -f "$WD/MAC" ] && tr -d ' \n' < "$WD/MAC" || true)
DURATION=${DURATION:-30}

identify() {  # identify <attempts>: 0 if the chip answers with the expected MAC (or any MAC if none saved)
  OUT=$("$ESPTOOL" --chip esp32 --port "$PORT" --before no-reset --after no-reset --connect-attempts "$1" chip-id 2>&1)
  MAC=$(grep -oiE 'MAC: *([0-9a-f]{2}:){5}[0-9a-f]{2}' <<<"$OUT" | head -1 | awk '{print tolower($2)}')
  [ -n "$MAC" ] && { [ -z "$EXPECTED_MAC" ] || [ "$MAC" = "$EXPECTED_MAC" ]; }
}

ready() {
  if [ -n "$EXPECTED_MAC" ]; then echo "OK: litter chip $MAC, ready."
  else echo "OK: chip $MAC, ready (no $WD/MAC to compare: make sure DevKit EN is held to GND)."; fi
}

# Already plugged in download mode (or stub still running): no need to wait for boot.
echo "== Already in download mode?"
if identify 1; then
  ready; exit 0
fi
echo "Not yet."

echo "== Listening ${DURATION}s: plug 12 V while holding OK."
"$PY" -I - "$PORT" "$DURATION" <<'EOF'
import sys, time, serial
port, duration = sys.argv[1], float(sys.argv[2])
s = serial.Serial()
s.port, s.baudrate, s.timeout = port, 115200, 0.2
s.dtr = s.rts = False
s.open(); s.reset_input_buffer()
buf, end = bytearray(), time.time() + duration
while time.time() < end:
    d = s.read(4096)
    if not d:
        continue
    if not buf:
        print("(data incoming)")
    buf += d
    if b"waiting for download" in buf:
        break
    if b"rst:" in buf:
        end = min(end, time.time() + 4)
s.close()

text = bytes(b for b in buf if b in (9, 10, 13) or 32 <= b < 127).decode()
if text.strip():
    print("--- received ---\n" + text.strip()[-1500:] + "\n---")
readable = len(text)
if b"DOWNLOAD" in buf or b"waiting for download" in buf:
    print("OK: litter talks, download mode."); sys.exit(10)
if b"rst:" in buf:
    print("Litter -> computer OK, but normal boot: unplug 12 V, retry holding OK."); sys.exit(11)
if readable > 20 and readable > len(buf) / 2:
    print("Litter -> computer OK (already running): unplug, hold OK, replug."); sys.exit(12)
if not buf:
    print("Silence: litter already on, or unpowered. Trying anyway."); sys.exit(3)
# With nothing connected the line floats: continuous bytes with no text (nulls or 0xEB/0xDF seen).
print(f"{len(buf)} bytes of noise, line floating: check WTXD wire, GND, 12 V."); sys.exit(2)
EOF
R=$?
case $R in 10|3) ;; *) exit $R ;; esac

echo "== Identify"
identify 7
if [ -z "$MAC" ]; then
  echo "$OUT" | tail -5
  echo "No answer: check WRXD wire, or download mode."; exit 5
fi
if [ -z "$EXPECTED_MAC" ] || [ "$MAC" = "$EXPECTED_MAC" ]; then
  ready
else
  echo "Wrong chip ($MAC, expected $EXPECTED_MAC): DevKit not held in reset (EN-GND?)."; exit 6
fi
