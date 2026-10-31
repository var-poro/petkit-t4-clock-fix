#!/bin/bash
# Writes the clock patch ($WORKDIR/app-patched.bin) into the partition the bootloader starts.
# Prerequisites: tools/backup.sh succeeded ($WORKDIR/flash-8MB.bin + SHA256 + MAC), then
#   tools/extract_app.py $WORKDIR/flash-8MB.bin $WORKDIR/app-original.bin
#   tools/patch_clock.py $WORKDIR/app-original.bin $WORKDIR/app-patched.bin
# The litter box must be in download mode (hold OK while plugging in 12 V).
# Rollback: write $WORKDIR/app-original.bin at the same OFFSET, same options.
set -euo pipefail
cd "$(dirname "$0")/.."
DEFAULT_ESPTOOL=~/.local/share/uv/tools/esphome/bin/esptool
ESPTOOL=${ESPTOOL:-$( [ -x "$DEFAULT_ESPTOOL" ] && echo "$DEFAULT_ESPTOOL" || echo esptool )}
PORT=${PORT:-$(ls /dev/cu.usbserial-* /dev/cu.SLAB_USBtoUART* /dev/cu.wchusbserial* 2>/dev/null | head -1 || true)}
[ -n "$PORT" ] || { echo "No USB-serial adapter."; exit 1; }
WD=${WORKDIR:-device}
BACKUP=$WD/flash-8MB.bin
# Image specific to THIS device (extracted from its own backup by extract_app.py): a firmware
# file downloaded elsewhere has the same build but another embedded certificate. Writing it
# would replace the device identity. plan_flash.py refuses it (boot partition mismatch).
ORIG=$WD/app-original.bin
PATCH=$WD/app-patched.bin
# 115200 everywhere: with --after no-reset the stub keeps a changed baud rate and the next
# command (which always syncs at 115200) can't sync.
ARGS=(--chip esp32 --port "$PORT" --baud "${BAUD:-115200}" --before no-reset --after no-reset)
md5of() { md5 -q "$1" 2>/dev/null || md5sum "$1" | cut -d' ' -f1; }

[ -f "$BACKUP" ] && [ -f "$WD/SHA256" ] || { echo "No validated backup: run backup.sh first."; exit 1; }
echo "$(cut -d' ' -f1 "$WD/SHA256")  $BACKUP" | shasum -a 256 -c -
[ -f "$WD/MAC" ] || { echo "No $WD/MAC: run backup.sh first."; exit 1; }
[ -f "$ORIG" ] && [ -f "$PATCH" ] && [ -f "$PATCH.md5" ] || { echo "Missing $ORIG, $PATCH or $PATCH.md5: run extract_app.py and patch_clock.py."; exit 1; }
[ "$(md5of "$PATCH")" = "$(cut -d' ' -f1 "$PATCH.md5")" ] || { echo "$PATCH: unexpected md5. Abort."; exit 1; }
EXPECTED_MAC=$(tr -d ' \n' < "$WD/MAC")

echo "== Chip"
MAC=$("$ESPTOOL" "${ARGS[@]}" chip-id 2>&1 | grep -oiE 'MAC: *([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1 | awk '{print tolower($2)}' || true)
[ "$MAC" = "$EXPECTED_MAC" ] || { echo "Not the litter (${MAC:-no answer}, expected $EXPECTED_MAC). Abort."; exit 4; }
echo "Litter $MAC"

echo "== Security"
# On first-generation ESP32 the command may not exist: an encrypted flash would be
# refused anyway by plan_flash.py (unreadable partition table).
SECU=$("$ESPTOOL" "${ARGS[@]}" get-security-info 2>&1 || true); echo "$SECU"
if grep -qiE 'Flash Encryption: Enabled|Secure Boot: Enabled' <<<"$SECU"; then
  echo "Flash encryption or secure boot enabled. Abort."; exit 2
fi

echo "== Plan"
OFFSET=$(python3 -I tools/plan_flash.py "$BACKUP" "$ORIG" "$PATCH" | tee /dev/stderr | sed -n 's/^OFFSET=//p')
[ -n "$OFFSET" ] || { echo "Plan refused, nothing written."; exit 3; }

echo "== Original image still at $OFFSET?"
"$ESPTOOL" "${ARGS[@]}" verify-flash "$OFFSET" "$ORIG" || { echo "No: changed since backup. Nothing written."; exit 5; }

read -r -p "Write $PATCH at $OFFSET? (yes/no) " R
[ "$R" = "yes" ] || { echo "Cancelled."; exit 0; }
# --diff-with: only the 4 KB sectors that change are erased and rewritten (2 of 367 on 1.652).
# --diff-with MUST come after address+file: it takes several files and would swallow the address.
# Safe because the chip was just verified identical to $ORIG above.
"$ESPTOOL" "${ARGS[@]}" write-flash --flash-mode keep --flash-freq keep --flash-size keep "$OFFSET" "$PATCH" --diff-with "$ORIG"
"$ESPTOOL" "${ARGS[@]}" verify-flash "$OFFSET" "$PATCH"
echo "Done. Unplug 12 V, remove wires, replug without OK."
