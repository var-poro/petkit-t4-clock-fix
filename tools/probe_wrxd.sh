#!/bin/bash
# Probes WRXD without a multimeter: the computer input (DevKit TX0) is put on WRXD instead of WTXD,
# RX unplugged, GND in place. We listen to what travels on the ESP32 receive line.
#  - 5A A5 frames: contact is good and the ISD drives this line (contention with the DevKit)
#  - noise without frames: the pad floats, no contact (conformal coating)
#  - silence: line held idle, contact likely, ISD quiet
set -uo pipefail
DEFAULT_PY=~/.local/share/uv/tools/esphome/bin/python
PY=${PYTHON:-$( [ -x "$DEFAULT_PY" ] && echo "$DEFAULT_PY" || echo python3 )}
PORT=${PORT:-$(ls /dev/cu.usbserial-* 2>/dev/null | head -1 || true)}
[ -n "$PORT" ] || { echo "No USB-serial adapter."; exit 1; }
DURATION=${DURATION:-40}

echo "== Listening to WRXD for ${DURATION} s: plug 12 V normally (without pressing OK)."
"$PY" -I - "$PORT" "$DURATION" <<'EOF'
import sys, time, serial
port, duration = sys.argv[1], float(sys.argv[2])
s = serial.Serial()
s.port, s.baudrate, s.timeout = port, 115200, 0.2
s.dtr = s.rts = False
s.open(); s.reset_input_buffer()
buf, end = bytearray(), time.time() + duration
while time.time() < end:
    buf += s.read(4096)
    if buf.count(b"\x5a\xa5") >= 20:
        break
s.close()

frames = buf.count(b"\x5a\xa5")
print(f"{len(buf)} bytes received, {frames} ISD frame starts (5A A5)")
if buf:
    print("start: " + buf[:48].hex(" "))
if frames >= 3:
    print("WRXD CONTACT GOOD, and the ISD talks on this line: it is what overrides the DevKit."); sys.exit(0)
if not buf:
    print("Silence: line held idle. Contact likely, but the ISD said nothing."); sys.exit(3)
print("Noise without frames: the WRXD pad floats, the wire does not touch it (coating?)."); sys.exit(2)
EOF
