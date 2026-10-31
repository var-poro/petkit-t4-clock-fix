# Firmware: how the box keeps time, why a dead RTC hides every event, and the patch

Analysed on the ESP32 application of firmware **1.652** (project `PK_T4`, app version
`V1.431-80-g5532d38`, compiled Jan 4 2026, ESP-IDF `V1.192-10-g7b48934b`), disassembled with
`xtensa-esp32-elf-objdump` (the one shipped with ESPHome's toolchain). An earlier pass on the
public Pura Max v1 dump from earlynerd showed the same logic at different addresses.

All addresses below are **runtime addresses** (IROM). In the image file, an IROM address maps
to file offset `vaddr − 0x40080000` for this build.

> **Pitfall.** If you dump the IROM segment to a raw file and disassemble it, remember the
> segment still carries its 8-byte header: addresses come out shifted by +8. Use
> `--adjust-vma` with the segment load address minus the header length.

## How the box keeps time

The box never sets the ESP32 system clock. It keeps its own time in a structure at
`0x3ffca7fc`:

| Offset | Content |
|---|---|
| +0 | date read from the RTC chip (10 bytes) |
| +10 | copy |
| +20, +24 | timezone |
| +28 | second of the day |
| **+32** | **current UNIX time** — used to date visits and cleanings |
| +36 | seconds since boot |

| Address | Role |
|---|---|
| `0x40115600` | every second: `+32 += 1`; past midnight (`+28 > 86399`) requests a recalibration |
| `0x40115b9c` | time getter (key 5 = `+32`, used to timestamp events) |
| `0x40115a94` | I²C read of the RTC; **on failure, zeroes the date** |
| `0x40115c1c` | server time received (key 0): writes the RTC, reads it back, compares within 2 s; stores `+32` **only if all of that succeeds** |
| `0x40115edc` | recalibration (midnight, then hourly): reads the RTC and **overwrites `+32`** with it; more than 10 failures → error 11 |
| `0x40125b6c` | error 11 → `send rtc cali err evt` → the app's **"Clock chip error"** |
| `0x40125e29` | RTC task: more than 50 failed writes → `send rtc write err evt` |

## The bug

When the server sends the time, `0x40115c1c` writes it to the RTC (BM8563 at `U101`, I²C on
GPIO15/GPIO4), reads it back and compares. Only if write **and** read **and** comparison
succeed is the new time stored in `+32`. Otherwise it logs `set rtc fail when write` /
`when read` / `fail,write %d,read %d` and returns −1, leaving the time unchanged.

With a dead RTC the box therefore runs on its boot default (2015 or 2019) plus uptime.
**Every visit and every cleaning is dated 2015/2019.** The cloud stores them under that date,
and the app and Home Assistant (which request today's records) never show them. On top of
that, the hourly recalibration keeps failing and raises error 11.

## The patch (4 changes, 11 bytes)

| Address | Before | After | Effect |
|---|---|---|---|
| `0x40115c55` | `e5e1ff` `call8` RTC write | `c62200` `j 0x40115ce4` | server time is stored directly, skipping RTC write and read-back |
| `0x40115d1c` | `86c1ff` `j 0x40115c26` | `06c2ff` `j 0x40115c28` | returns 0: no more "rtc write err" event |
| `0x40115efc` | `318bcf` `l32r` | `22a000` `movi a2, 0` | recalibration no longer reads the RTC… |
| `0x40115eff` | `0c1b` `movi.n` | `1df0` `retw.n` | …and returns 0: no more error 11 |

Checks done on the patched image (independently reviewed):

- every jump lands on an instruction boundary; `0x40115ce4` was already a branch target;
- at `0x40115c55`, `a4`/`a5` are already loaded and `a6` is unused between `ce4` and `d1c`;
- the "return 0" path goes through `c28` then `bgeui a4` (a pointer, always ≥ 8) to `retw`;
- the recalibration keeps its `entry`/`retw` window and returns 0; its caller (`0x40125b70`)
  treats 0 as "cali normal" and clears the error; the RTC task (`0x40125e31`) resets its
  50-failure counter on 0;
- the only remaining direct RTC reads are factory tests (`pcba rtc item`), not run in normal use.

The image checksum byte and the appended SHA-256 are recomputed; `esptool image-info` reports
both valid. The image carries no signature block (secure boot is off on this product).

An earlier, smaller patch that only covered the write-failure path was **not enough**: a failed
read-back still zeroed the date before storing it, and the hourly recalibration still overwrote
the time and raised error 11.

## Known limitations

- After a reboot, until the box gets the server time (on Wi-Fi connection), events are still
  misdated. This is normally a few seconds to minutes.
- The **date fields** (day, month, weekday) are now refreshed only by the server time, no longer
  by the hourly recalibration. Event timestamps are unaffected (they use `+32`, which advances
  every second), but the scheduler (`0x40135415`) reads a weekday that may lag between two server
  time updates. Watch whether a scheduled cleaning ever skips a day.
- A PetKit OTA update installs into the other partition and the patch is gone (no risk of
  bricking; the box just goes back to the error).

## Flash layout seen on this product (8 MB)

`nvs` 0x9000, `otadata` 0xd000, `phy_init` 0xf000, **`ota_0` 0x10000 (2 MB)**, many `user_*` NVS
partitions (serial, ID, calibration, binding, server, config, schedule, messages, pictures,
languages), **`ota_1` 0x3d0000 (2 MB)**, then a second set of pictures/languages. The boot slot is
`(seq − 1) % 2` of the valid otadata entry with the highest sequence number. On our box `ota_0`
held 1.652 and `ota_1` an older build (Nov 2024).

The application image embeds a **per-device certificate** (base64, around file offset
`0x37789–0x37c3b` in 1.652). Two boxes on the same version differ there (≈1.2 KB) and in the
checksum/hash. Always patch your own dump.
