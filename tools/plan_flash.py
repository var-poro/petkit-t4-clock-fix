#!/usr/bin/env python3
# Reads the litter box 8 MB backup and tells where to write the clock patch.
# Refuses unless the boot partition holds exactly the original app image.
# Output (last line, if all is well): OFFSET=0x...  to pass to esptool write-flash.
import struct
import sys
import zlib

PT_OFF = 0x8000


def partitions(flash):
    out = []
    for i in range(0, 0xC00, 32):
        e = flash[PT_OFF + i:PT_OFF + i + 32]
        magic, typ, sub, off, size = struct.unpack_from("<HBBII", e)
        if magic != 0x50AA:
            break
        label = e[12:28].split(b"\0")[0].decode(errors="replace")
        out.append((label, typ, sub, off, size))
    return out


def active_ota(flash, otadata, n_ota):
    best = None
    for k in range(2):
        e = flash[otadata + k * 0x1000:otadata + k * 0x1000 + 32]
        seq, = struct.unpack_from("<I", e)
        crc, = struct.unpack_from("<I", e, 28)
        valid = seq not in (0, 0xFFFFFFFF) and crc == zlib.crc32(e[:4], 0xFFFFFFFF)
        print(f"otadata[{k}] seq={seq:#x} crc={'ok' if valid else 'invalid'}")
        if valid and (best is None or seq > best):
            best = seq
    return None if best is None else (best - 1) % n_ota


def boot_partition(flash):
    """Returns (parts, apps, (label, off, size)) of the partition the bootloader starts."""
    parts = partitions(flash)
    if not parts:
        sys.exit("no partition table at 0x8000 (encrypted?): nothing written")
    print("Partitions:")
    for label, typ, sub, off, size in parts:
        print(f"  {label:<12} type {typ:#04x}/{sub:#04x}  {off:#08x}  {size:#08x}")

    apps = {sub: (label, off, size) for label, typ, sub, off, size in parts if typ == 0}
    otas = sorted(s for s in apps if 0x10 <= s < 0x20)
    otadata = next((off for label, typ, sub, off, size in parts if typ == 1 and sub == 0), None)

    slot = active_ota(flash, otadata, len(otas)) if otadata is not None and otas else None
    if slot is None:
        sub = 0x00 if 0x00 in apps else (otas[0] if otas else None)
    else:
        sub = otas[slot]
    if sub is None:
        sys.exit("boot partition not found: nothing written")
    label, off, size = apps[sub]
    print(f"Boot partition: {label} at {off:#x}")
    return parts, apps, (label, off, size)


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: plan_flash.py flash-8MB.bin app-original.bin app-patched.bin")
    flash = open(sys.argv[1], "rb").read()
    orig = open(sys.argv[2], "rb").read()
    patched = open(sys.argv[3], "rb").read()
    if len(flash) != 0x800000:
        sys.exit("backup is not 8 MB")
    if len(orig) != len(patched):
        sys.exit("patched image size differs from original")

    parts, apps, (label, off, size) = boot_partition(flash)
    match = [apps[s][0] for s in apps if flash[apps[s][1]:apps[s][1] + len(orig)] == orig]
    print("Original image in:", ", ".join(match) or "none")

    if len(patched) > size:
        sys.exit("image does not fit the partition")
    if flash[off:off + len(orig)] != orig:
        sys.exit(f"{label} is not the exact original image: nothing written")
    print(f"OFFSET={off:#x}")


if __name__ == "__main__":
    main()
