# Hardware: board, pads, and the UART fight

## The board (`T4-MAIN-V2.0`)

- **ESP32-WROOM-32** (8 MB flash): Wi-Fi/BLE, display, RTC, cloud. Its UART0 (GPIO1 TX /
  GPIO3 RX) is brought out to three pads at the board edge, labelled **`WRXD`, `WTXD`, `GND`**,
  next to the module.
- **ISD91230** (Nuvoton Cortex-M0): motors, load cells (HX711), hall/proximity sensors. It talks
  to the ESP32 at 115200 baud **on that same UART0** (frames start with `5A A5`). Its SWD port is
  the 2×4 hole block left of the buzzer: one column `DEBUG TX RX GND`, the other
  `DAT CLK RESET GND`.
- **BM8563 RTC** at `U101` with its 32 kHz crystal, I²C on GPIO15/GPIO4. Ours was covered in a
  shiny black deposit — the litter environment (urine fumes, humidity) is hard on this board.
- The **OK button is GPIO0**: hold it while plugging in the 12 V to enter download mode.

The board is coated: a bead of white silicone over the pad row, and a clear conformal varnish over
everything. Scrape the ring of each pad you use back to bright copper and clear the hole with a
needle; a varnished pad makes no contact at all.

## Symptom: "it talks but never listens"

With any adapter wired TX→`WRXD`, RX→`WTXD`, GND→`GND`, box on its 12 V, OK held:

- the ROM boot log arrives perfectly:
  `rst:0x1 (POWERON_RESET),boot:0x7 (DOWNLOAD_BOOT(UART0/UART1/SDIO_REI_REO_V2)) waiting for download`
- but `esptool --before no-reset chip-id` fails with **"No serial data received"**.

So `WTXD` and `GND` are fine and the box is in download mode; nothing gets *into* the ESP32.

## Diagnosis without a multimeter

1. **Loopback** (box unplugged): put the adapter's TX wire on its own RX → bytes come back. The
   adapter and the wire are fine. (Doing this with the box powered proves nothing: the box's own
   TX holds the RX node high.)
2. **WRXD probe** (`tools/probe_wrxd.sh`): move the adapter's **RX** wire onto `WRXD`, disconnect
   its TX, power the box normally and listen. We got ~1.3 KB/s of traffic with `5A A5` headers:
   the wire **does** touch `WRXD`, and the **ISD is actively driving that line**.

On an **ESP32 DevKit V1** used as an adapter (its own ESP32 held in reset with `EN`→`GND`), the
CP2102's TXD reaches the `RX0` pin through a **1 kΩ resistor** (R3 in the DOIT schematic). Against
the ISD's push-pull output, it cannot pull the line low enough: the ESP32 never sees the sync.

Do **not** try to silence the ISD by holding its `RESET` low: at power-up the waste-bin door motor
ran and the ESP32 never booted (no boot log at all).

## What worked

- **Flipper Zero** as adapter (GPIO → USB-UART Bridge, pins 13 TX / 14 RX, GND on 18; its I/O pins
  have only 51 Ω in series): the ESP32 answered immediately. But the Flipper's USB-UART bridge
  **stalls in the receive direction** on long transfers — with `esptool --trace` the stub's data
  packet simply stops after ~70 bytes, and esptool times out ("No more data to read from the serial
  port"). Small packets / fewer packets in flight did not help.
- **Hybrid**: Flipper pin 13 → `WRXD` to send, DevKit `TX0` → `WTXD` to receive (DevKit's own
  ESP32 held in reset), joined into one pseudo-terminal by `tools/hybrid_bridge.py`. esptool talks
  to the pty. The whole 8 MB backup and the write went through without a single retry.
- A **bare 3.3 V USB-serial module** with no series resistor on TX (what the community guides use)
  should work directly without the bridge.

## esptool notes

- Always `--before no-reset --after no-reset`: the adapter's DTR/RTS lines are not wired to the box.
- Stay at **115200** for every command. With `--after no-reset` the flasher stub keeps running at
  whatever baud the previous command switched to, and the next command (which always syncs at
  115200) cannot find it.
- `write-flash ... --diff-with <file>`: `--diff-with` accepts several files, so it must come
  **after** the address/file pair or it swallows the address.
- `get-security-info` is not implemented on this chip revision; the readable partition table and
  the unsigned stock image show that flash encryption and secure boot are off.
- A full 8 MB read at 115200 takes ~13 minutes per pass; reading in 16 KB chunks costs a bit more
  but lets a failed chunk be retried.
