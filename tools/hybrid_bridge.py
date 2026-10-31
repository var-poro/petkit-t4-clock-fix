#!/usr/bin/env python3
# "Hybrid" serial port for esptool: what the computer sends goes out through the Flipper Zero
# (USB-UART bridge, pin 13 -> WRXD), what the litter box answers comes back through the ESP32
# DevKit (TX0 -> WTXD, EN held to GND).
# Why: the DevKit receives perfectly but cannot transmit (its 1 kOhm series resistor loses
# against the ISD on WRXD); the Flipper transmits fine but its USB bridge drops data on receive
# during long reads (packet cut mid-way, seen with esptool --trace).
# Creates a pseudo-terminal, writes its path to panel/bridge-port.txt (env BRIDGE_PORT_FILE),
# and relays until Ctrl-C.
# Fixed 115200 baud: run esptool with --baud 115200 (no baud rate change).
# Env SEND / RECEIVE override the ports (default: first Flipper, first CP210x/CH34x).
import errno
import fcntl
import glob
import os
import pty
import select
import sys
import tty

import serial

HERE = os.path.dirname(os.path.abspath(__file__))
PORT_FILE = os.environ.get("BRIDGE_PORT_FILE") or os.path.join(HERE, "..", "panel", "bridge-port.txt")


def first(pattern):
    found = sorted(glob.glob(pattern))
    if not found:
        sys.exit(f"no port {pattern}")
    return found[0]


send = serial.Serial(os.environ.get("SEND") or first("/dev/cu.usbmodemflip*"), 115200, timeout=0)
recv = serial.Serial(os.environ.get("RECEIVE") or first("/dev/cu.usbserial-*"), 115200, timeout=0)
for p in (send, recv):
    p.dtr = p.rts = False

master, slave = pty.openpty()
tty.setraw(master)
tty.setraw(slave)
fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)
name = os.ttyname(slave)
with open(PORT_FILE, "w") as f:
    f.write(name + "\n")
print(f"bridge: send {send.port}, receive {recv.port}, port for esptool {name}", flush=True)

try:
    while True:
        ready, _, _ = select.select([master, recv.fileno(), send.fileno()], [], [])
        if master in ready:
            try:
                send.write(os.read(master, 4096))
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EIO):
                    raise
        if recv.fileno() in ready:
            data = recv.read(recv.in_waiting or 1)
            try:
                os.write(master, data)
            except OSError as e:
                if e.errno != errno.EAGAIN:
                    raise  # EAGAIN: nobody reading (noise, litter off): drop it
        if send.fileno() in ready:
            send.read(send.in_waiting or 1)  # the Flipper also receives the answer: drop it
except KeyboardInterrupt:
    pass
finally:
    if os.path.exists(PORT_FILE):
        os.remove(PORT_FILE)
