# PetKit Pura Max 2 (T4) — "Clock chip error" fix

A 4-instruction firmware patch for PetKit Pura Max / Pura Max 2 litter boxes (model T4,
board `T4-MAIN-V2.0`) whose real-time-clock chip has died.

**Symptoms**

- The app shows **"Clock chip error. Please contact customer service."**
- **No visits and no cleanings ever appear** in the app or in Home Assistant
  (`times_used` stays at 0, `last_event` stays `unknown`), even though the cats use it and
  the box cleans itself.

**Cause.** The box keeps its own time and only accepts the time sent by the PetKit server
if it can write it to the RTC chip (BM8563, `U101`) *and* read it back. With a dead RTC,
the server time is never accepted, so every event is dated 2015/2019. The cloud files them
under that date, and the app and Home Assistant, which ask for *today's* records, never see
them. A full write-up is in [docs/firmware.md](docs/firmware.md).

**Fix.** Four small changes in the ESP32 application make the box ignore the RTC
completely: it takes the server time directly and keeps counting seconds itself, as it
already did. Everything else (app, cloud, Home Assistant integration, Smart Spray) works
as before.

**Result** on a real box, right after flashing: the error turned into "No error" and the
first visit ever recorded appeared, dated correctly.

> Tested on firmware **1.652** (app version `V1.431-80-g5532d38`, compiled Jan 4 2026).
> The patch tool checks the original bytes at every patch site and refuses any other build.

## ⚠️ Read this first

- **Never flash a firmware file you downloaded or got from someone else.** Each box's
  application image embeds **its own device certificate**. Flashing another box's image
  would replace your box's identity. The tools here always build the patch from **your
  own** flash dump.
- **A PetKit OTA update erases the fix.** Decline firmware updates in the app (or re-apply
  the patch to the new version if the addresses still match — the tool will tell you).
- **Back up the full 8 MB flash before anything else**, and keep it. It holds your
  calibration, Wi-Fi settings and device keys. The tools refuse to write without a
  validated backup.
- You open the box and touch its mainboard at your own risk. No warranty.

## What you need

- A Mac or Linux machine with Python 3 and [esptool](https://github.com/espressif/esptool)
  (v5 tested), plus `pyserial`.
- A way to talk to the ESP32's UART0 **that can overpower the motor MCU** on the RX line
  (see below). Either:
  - a **bare 3.3 V USB-serial adapter** (CP2102 / FT232 / CH340 module) with no series
    resistor on its TX line — what the community uses; or
  - what we had: a **Flipper Zero** (GPIO → USB-UART Bridge) to send **plus an ESP32 DevKit**
    held in reset to receive, joined by `tools/hybrid_bridge.py`.
- 3 Dupont wires. No soldering is required if the pads are clean (see
  [docs/hardware.md](docs/hardware.md)).

### Why a plain ESP32 DevKit is not enough

The second chip on the board (an ISD91230 MCU that drives motors and the scale) talks to the
ESP32 on the **same UART0 pins** used for flashing, and keeps the ESP32's RX line (`WRXD`)
busy. On an ESP32 DevKit V1, the USB-serial chip's TX goes through a **1 kΩ resistor** before
reaching the header pin, so it loses that fight: you receive the boot log perfectly but
esptool never gets an answer ("No serial data received"). A Flipper Zero (51 Ω) wins, but its
USB-UART bridge stalls mid-packet on long reads. Using the Flipper to send and the DevKit to
receive works reliably. Details and the test that proved it: [docs/hardware.md](docs/hardware.md).

## Wiring

Pads `WRXD`, `WTXD`, `GND` are at the board edge next to the ESP32-WROOM module, under a bead
of silicone and a clear conformal coating — scrape both off the pads.

| From | To |
|---|---|
| Adapter TX (Flipper pin 13) | `WRXD` |
| Adapter RX (DevKit `TX0` pin, or Flipper pin 14) | `WTXD` |
| Adapter GND (Flipper pin 18) | `GND` |
| DevKit `EN` | DevKit `GND` (holds the DevKit's own ESP32 in reset) |

Nothing on 3V3/5V: the box is powered by its own 12 V adapter. **Do not connect anything to
the `DAT CLK RESET GND` block** (the motor MCU's SWD port): holding its RESET low opens the
waste-bin door at power-up and keeps the ESP32 off.

**Download mode:** box unplugged, hold the **OK** button (GPIO0), plug the 12 V in, release.

## Procedure

Everything below can be driven from the local web panel (`python3 panel/server.py`, then
open http://localhost:8765): it runs each step, shows the log, progress, speed and ETA, beeps
when you need to act, chimes on success and sounds an alarm on failure.

1. **Test the wiring** — `tools/test_wiring.sh`. Tries to identify the chip first; if the box
   is not in download mode yet, it gives you 30 s to plug the 12 V while holding OK, listens
   to the boot log, then identifies the chip.
2. **Back up 8 MB** — `tools/backup.sh`. Reads the whole flash twice (16 KB chunks with
   retries, ~15–30 min at 115200 baud) and compares. Saves `device/flash-8MB.bin`,
   `device/SHA256` and the chip `device/MAC`. Keep a copy somewhere else.
3. **Build the patch from your own dump**
   ```bash
   python3 tools/extract_app.py device/flash-8MB.bin device/app-original.bin
   python3 tools/patch_clock.py device/app-original.bin device/app-patched.bin
   ```
4. **Flash** — `tools/flash_patch.sh`. Checks the backup hash, the patched image hash, that the
   chip answering is the one you backed up, finds the boot partition from the partition table
   and otadata, verifies the chip still holds exactly your original image there, asks for
   confirmation, then rewrites **only the two 4 KB sectors that change** (`--diff-with`) and
   verifies the whole partition.
5. **Reboot normally**: unplug the 12 V, remove the wires, cover the pads (Kapton or a dab of
   neutral silicone — urine fumes corrode boards), plug in **without** holding OK.
6. **Check**: within minutes the "clock chip error" should clear, and the next visit or
   cleaning should appear dated today in the app / Home Assistant.

**Rollback** (~10 s): write the original back over the same two sectors:

```bash
esptool --chip esp32 --port <PORT> --baud 115200 --before no-reset --after no-reset \
  write-flash 0x10000 device/app-original.bin --diff-with device/app-patched.bin
```

(Use the offset printed by `flash_patch.sh`; `0x10000` = `ota_0` on our box.) Last resort:
`write-flash 0 device/flash-8MB.bin` restores the whole chip.

## Repository

| Path | What |
|---|---|
| `tools/test_wiring.sh` | identify the chip / listen to the boot log |
| `tools/probe_wrxd.sh` | no-multimeter probe: is the wire on `WRXD`, and is the motor MCU driving it? |
| `tools/backup.sh` | full 8 MB backup, read twice, compared |
| `tools/extract_app.py` | extract the exact boot application image from the backup |
| `tools/patch_clock.py` | apply the 4 patches, fix checksum and SHA-256 |
| `tools/plan_flash.py` | find the boot partition, refuse unless it holds the exact original |
| `tools/flash_patch.sh` | guarded differential write + verification |
| `tools/hybrid_bridge.py` | one serial port for esptool: send via Flipper, receive via DevKit |
| `panel/` | local web control panel |
| `docs/firmware.md` | how the firmware keeps time, the bug, the patch |
| `docs/hardware.md` | board, pads, the UART contention and how it was diagnosed |

No PetKit firmware binaries are included.

## Credits

- [earlynerd/petkit-pura-max-serial-bus](https://github.com/earlynerd/petkit-pura-max-serial-bus)
  — board breakdown and the motor-MCU bus protocol.
- [egormanga/esphome-petkit-t4](https://github.com/egormanga/esphome-petkit-t4) and the
  [Triskae install guide](https://github.com/Triskae/esphome-petkit-t4/blob/feat/install-guide/index.html)
  — a full open-source replacement firmware, if you'd rather go that way.

## License

MIT for the code and documentation in this repository. PetKit is a trademark of its owner;
this project is not affiliated with PetKit.
