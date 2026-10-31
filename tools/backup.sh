#!/bin/bash
# Full 8 MB backup of the litter box ESP32 flash, read twice and compared.
# The litter box must be in download mode (hold OK while plugging in 12 V).
# Output in $WORKDIR (default ./device/): flash-8MB.bin, SHA256, MAC.
set -euo pipefail
cd "$(dirname "$0")/.."
DEFAULT_ESPTOOL=~/.local/share/uv/tools/esphome/bin/esptool
ESPTOOL=${ESPTOOL:-$( [ -x "$DEFAULT_ESPTOOL" ] && echo "$DEFAULT_ESPTOOL" || echo esptool )}
PORT=${PORT:-$(ls /dev/cu.usbmodemflip* /dev/cu.usbserial-* /dev/cu.SLAB_USBtoUART* /dev/cu.wchusbserial* 2>/dev/null | head -1 || true)}
[ -n "$PORT" ] || { echo "No USB-serial adapter."; exit 1; }
OUT=${WORKDIR:-device}; mkdir -p "$OUT"
# Never overwrite the original backup (run again after the patch, it would no longer be original).
# A backup is validated once SHA256 exists (two identical reads).
[ ! -e "$OUT/SHA256" ] || { echo "Backup already validated ($OUT/SHA256), kept."; exit 0; }
# --before no-reset / --after no-reset: the DevKit reset lines do not reach the litter box.
# 115200 everywhere: with --after no-reset, the stub keeps the baud rate changed by the previous
# command, and the next one (which always syncs at 115200) can't find it anymore.
# Seen in practice: chip-id switched to 460800, then flash-id "No serial data received".
ARGS=(--chip esp32 --port "$PORT" --baud "${BAUD:-115200}" --before no-reset --after no-reset)
echo "== Port $PORT"
ID=$("$ESPTOOL" "${ARGS[@]}" chip-id 2>&1) || { echo "$ID"; exit 1; }
echo "$ID"
MAC=$(grep -oiE 'MAC: *([0-9a-f]{2}:){5}[0-9a-f]{2}' <<<"$ID" | head -1 | awk '{print tolower($2)}' || true)
[ -n "$MAC" ] || { echo "No MAC in chip-id output."; exit 1; }
if [ -e "$OUT/MAC" ] && [ "$(cat "$OUT/MAC")" != "$MAC" ]; then
  echo "Chip $MAC is not the one in $OUT/MAC ($(cat "$OUT/MAC")). Abort."; exit 1
fi
echo "$MAC" > "$OUT/MAC"
"$ESPTOOL" "${ARGS[@]}" flash-id

# Read in 16 KB chunks: through the Flipper USB-UART bridge, a long read gets cut off
# ("No more data to read from the serial port"). With 64 KB, ~1 chunk in 5 failed and one
# failed 4 times in a row. Up to 8 tries, with a pause.
CHUNK=0x4000
read_all() {  # read_all <file> <label>
  local dest=$1 tmp; tmp=$(mktemp -d); : > "$dest"
  for ((off = 0; off < 0x800000; off += CHUNK)); do
    local ok=
    for try in 1 2 3 4 5 6 7 8; do
      if "$ESPTOOL" "${ARGS[@]}" read-flash "$off" "$CHUNK" "$tmp/t.bin" > "$tmp/log" 2>&1; then ok=1; break; fi
      echo "retry $(printf '0x%06x' "$off") ($try)"; sleep 1
    done
    [ -n "$ok" ] || { tail -3 "$tmp/log"; echo "FAILED: $(printf '0x%06x' "$off") unreadable."; rm -rf "$tmp" "$dest"; exit 4; }
    cat "$tmp/t.bin" >> "$dest"
    echo "$2: $(( (off + CHUNK) * 100 / 0x800000 )) %"
  done
  rm -rf "$tmp"
}
# A complete first read already done (but not compared yet) is reused.
if [ "$(wc -c < "$OUT/flash-8MB.bin" 2>/dev/null | tr -d ' ' || echo 0)" = 8388608 ]; then
  echo "Read 1/2 already done."
else
  read_all "$OUT/flash-8MB.bin" "read 1/2"
fi
read_all "$OUT/flash-8MB.reread.bin" "read 2/2"
A=$(shasum -a 256 "$OUT/flash-8MB.bin" | cut -d' ' -f1); B=$(shasum -a 256 "$OUT/flash-8MB.reread.bin" | cut -d' ' -f1)
if [ "$A" = "$B" ]; then echo "$A  flash-8MB.bin" > "$OUT/SHA256"; rm "$OUT/flash-8MB.reread.bin"; echo "OK: both reads match ($A)"
else echo "FAILED: reads differ. Do not flash."; exit 2; fi
