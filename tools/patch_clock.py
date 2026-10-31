#!/usr/bin/env python3
# "Clock chip" patch for PetKit T4 firmware 1.652 (ESP32 app image).
# The litter box dates everything with the server time and ignores the RTC (BM8563, U101).
# Addresses are the real runtime addresses of firmware 1.652 (an irom.asm disassembly is off by +8).
# The original bytes at each patch site are checked: any other firmware is refused.
# usage: patch_clock.py app-original.bin app-patched.bin   (also writes app-patched.bin.md5)
import argparse
import hashlib
import os
import struct
import sys

IROM_BASE = 0x40080000

PATCHES = [
    (0x40115C55, "e5e1ff", "c62200",
     "server time: skip RTC write/readback, store the received time"),
    (0x40115D1C, "86c1ff", "06c2ff",
     "server time: return 0 (no more 'rtc write err' event)"),
    (0x40115EFC, "318bcf", "22a000",
     "hourly resync: a2 = 0 instead of reading the RTC"),
    (0x40115EFF, "0c1b", "1df0",
     "hourly resync: return at once (no more clock chip error 11)"),
]


def file_off(vaddr):
    return vaddr - IROM_BASE


def fix_footer(img):
    nseg = img[1]
    pos = 24
    data = bytearray()
    for _ in range(nseg):
        _load, ln = struct.unpack_from("<II", img, pos)
        pos += 8
        data += img[pos:pos + ln]
        pos += ln

    checksum = 0xEF
    for b in data:
        checksum ^= b

    hash_appended = img[23]
    if hash_appended:
        ck_pos = len(img) - 33
        img[ck_pos] = checksum
        digest = hashlib.sha256(img[:ck_pos + 1]).digest()
        img[ck_pos + 1:ck_pos + 33] = digest
    else:
        ck_pos = pos
        img[ck_pos] = checksum
    return checksum, hash_appended, ck_pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("destination")
    args = ap.parse_args()

    orig = open(args.source, "rb").read()
    print(f"source   {args.source}")
    print(f"size     {len(orig)} bytes")
    print(f"md5      {hashlib.md5(orig).hexdigest()}")

    img = bytearray(orig)
    for vaddr, old, new, desc in PATCHES:
        off = file_off(vaddr)
        old_b, new_b = bytes.fromhex(old), bytes.fromhex(new)
        if img[off:off + len(old_b)] != old_b:
            sys.exit(f"unexpected bytes at {vaddr:#x}: {img[off:off + len(old_b)].hex()} "
                     f"(expected {old}): not firmware 1.652, nothing written")
        img[off:off + len(new_b)] = new_b
        print(f"patch    {vaddr:#x} {old} -> {new}  {desc}")

    checksum, hash_appended, ck_pos = fix_footer(img)
    print(f"checksum {checksum:#04x} at {ck_pos:#x}")
    print(f"sha256   {'yes' if hash_appended else 'no'}")

    md5 = hashlib.md5(img).hexdigest()
    open(args.destination, "wb").write(img)
    with open(args.destination + ".md5", "w") as f:
        f.write(f"{md5}  {os.path.basename(args.destination)}\n")
    print(f"written  {args.destination}")
    print(f"md5      {md5}")


if __name__ == "__main__":
    main()
