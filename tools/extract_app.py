#!/usr/bin/env python3
# Extracts the app image the bootloader starts from this device's own 8 MB backup.
#
# Why not use a firmware file downloaded elsewhere: the app image embeds a per-device
# certificate (seen at 0x37789-0x37c3b in 1.652). Two devices on the same firmware version
# run the same build but different bytes there. Flashing someone else's image would replace
# this device's identity. The patch must always be built from the device's own dump.
#
# usage: extract_app.py flash-8MB.bin app-original.bin
# Writes exactly the image length (header, segments, checksum padding, optional SHA-256),
# so that plan_flash.py and esptool verify-flash compare the image and nothing after it.
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
sys.dont_write_bytecode = True  # no __pycache__ next to the tools
from plan_flash import boot_partition  # noqa: E402

ESP_IMAGE_MAGIC = 0xE9
HEADER_LEN = 24  # 8-byte common header + 16-byte extended header
MAX_SEGMENTS = 16


def image_length(part):
    """Walks an ESP32 app image and returns its exact length in bytes."""
    if part[0] != ESP_IMAGE_MAGIC:
        sys.exit(f"no app image (magic {part[0]:#04x}): nothing extracted")
    nseg = part[1]
    if not 0 < nseg <= MAX_SEGMENTS:
        sys.exit(f"implausible segment count {nseg}: nothing extracted")
    pos = HEADER_LEN
    for _ in range(nseg):
        if pos + 8 > len(part):
            sys.exit("segment header past partition end: nothing extracted")
        _load, ln = struct.unpack_from("<II", part, pos)
        pos += 8 + ln
        if pos > len(part):
            sys.exit("segment past partition end: nothing extracted")
    # Checksum byte is the last byte of a 16-byte aligned block.
    pos += 16 - (pos % 16)
    if part[23] == 1:  # hash_appended: 32-byte SHA-256 of everything before it
        pos += 32
    if pos > len(part):
        sys.exit("image past partition end: nothing extracted")
    return pos


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: extract_app.py flash-8MB.bin app-original.bin")
    flash = open(sys.argv[1], "rb").read()
    if len(flash) != 0x800000:
        sys.exit("backup is not 8 MB")
    _parts, _apps, (label, off, size) = boot_partition(flash)
    part = flash[off:off + size]
    n = image_length(part)
    img = part[:n]
    if img[23] == 1 and hashlib.sha256(img[:-32]).digest() != img[-32:]:
        sys.exit("image SHA-256 does not match: nothing extracted")
    open(sys.argv[2], "wb").write(img)
    print(f"image    {label} at {off:#x}, {n} bytes")
    print(f"written  {sys.argv[2]}")
    print(f"md5      {hashlib.md5(img).hexdigest()}")


if __name__ == "__main__":
    main()
