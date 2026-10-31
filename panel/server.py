#!/usr/bin/env python3
# Small control panel: runs the steps (wiring, backup, patch) and shows them live
# on http://localhost:8765. One step at a time.
# Env: WORKDIR (default ../device), PORT, ESPTOOL, PYTHON (python with pyserial, for the bridge).
import glob
import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
WORKDIR = os.path.abspath(os.environ.get("WORKDIR") or os.path.join(ROOT, "device"))
BRIDGE_FILE = os.path.join(HERE, "bridge-port.txt")
DEFAULT_PY = os.path.expanduser("~/.local/share/uv/tools/esphome/bin/python")
PYTHON = os.environ.get("PYTHON") or (DEFAULT_PY if os.access(DEFAULT_PY, os.X_OK) else "python3")


def current_port():
    # Display only: does not start the bridge.
    if os.path.exists(BRIDGE_FILE) and bridge is not None and bridge.poll() is None:
        return "hybrid bridge " + open(BRIDGE_FILE).read().strip()
    flipper, devkit = glob.glob("/dev/cu.usbmodemflip*"), glob.glob("/dev/cu.usbserial-*")
    if flipper and devkit:
        return "hybrid bridge ready (Flipper + DevKit)"
    return "Flipper only" if flipper else "DevKit only" if devkit else "no adapter"


bridge = None  # hybrid_bridge.py: send through the Flipper, receive through the DevKit


def serial_port():
    # Flipper + DevKit plugged in: hybrid bridge (the Flipper drops data on receive, the DevKit
    # loses on transmit against the ISD). Otherwise Flipper only, otherwise DevKit only.
    global bridge
    if os.environ.get("PORT"):
        return os.environ["PORT"]
    flipper = sorted(glob.glob("/dev/cu.usbmodemflip*"))
    devkit = sorted(glob.glob("/dev/cu.usbserial-*"))
    if flipper and devkit:
        if bridge is None or bridge.poll() is not None:
            if os.path.exists(BRIDGE_FILE):
                os.remove(BRIDGE_FILE)  # path of a previous bridge, stopped without cleanup
            bridge = subprocess.Popen([PYTHON, "-I", os.path.join(TOOLS, "hybrid_bridge.py")],
                                      env=dict(os.environ, BRIDGE_PORT_FILE=BRIDGE_FILE),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                time.sleep(0.1)
                if os.path.exists(BRIDGE_FILE):
                    break
        if os.path.exists(BRIDGE_FILE):
            return open(BRIDGE_FILE).read().strip()
    return (flipper or devkit or [""])[0]  # empty: the script reports "No USB-serial adapter."


# Patch step: build the patched image from this device's own backup if needed, then flash.
# Python steps get </dev/null so the "yes" sent on stdin reaches flash_patch.sh.
PATCH_SCRIPT = r'''
set -euo pipefail
W=$WORKDIR; T=$TOOLS
if [ ! -f "$W/app-patched.bin" ] || [ ! -f "$W/app-patched.bin.md5" ]; then
  [ -f "$W/SHA256" ] || { echo "No validated backup: run the backup step first."; exit 1; }
  echo "== Extract boot app image"
  python3 -I "$T/extract_app.py" "$W/flash-8MB.bin" "$W/app-original.bin" </dev/null
  echo "== Patch"
  python3 -I "$T/patch_clock.py" "$W/app-original.bin" "$W/app-patched.bin" </dev/null
fi
exec /bin/bash "$T/flash_patch.sh"
'''
STEPS = {
    "wiring": ["/bin/bash", os.path.join(TOOLS, "test_wiring.sh")],
    "backup": ["/bin/bash", os.path.join(TOOLS, "backup.sh")],
    "patch": ["/bin/bash", "-c", PATCH_SCRIPT],
    "probe": ["/bin/bash", os.path.join(TOOLS, "probe_wrxd.sh")],
}
state = {"step": None, "status": "idle", "start": None, "end": None,
         "countdown": None, "percent": None, "log": [], "results": {}}
lock = threading.Lock()
process = None  # running step, so it can be cancelled

# State of each wire, kept across runs (a rerun no longer hears the boot).
WIRES_FILE = os.path.join(HERE, "wires.json")
state["wires"] = json.load(open(WIRES_FILE)) if os.path.exists(WIRES_FILE) else {}
HINTS = [  # (log text, wires concerned, verdict)
    ("litter talks", ("tx", "gnd", "en"), True),
    ("Litter -> computer OK", ("tx", "gnd"), True),
    ("line floating", ("tx", "gnd"), False),
    ("OK: litter chip", ("tx", "rx", "gnd", "en"), True),
    ("OK: chip", ("tx", "rx", "gnd"), True),
    ("No answer", ("rx",), False),
    ("Wrong chip", ("en",), False),
]


def note_wires(line):
    for text, wires, verdict in HINTS:
        if text in line:
            state["wires"].update(dict.fromkeys(wires, verdict))
            json.dump(state["wires"], open(WIRES_FILE, "w"))


def run(name):
    env = dict(os.environ, PORT=serial_port(), BAUD="115200", WORKDIR=WORKDIR, TOOLS=TOOLS)
    with lock:
        state.update(step=name, status="running", start=time.time(), end=None,
                     countdown=None, percent=None, log=[])
    global process
    # Separate process group: cancelling also kills esptool and python started by the script.
    p = process = subprocess.Popen(STEPS[name], env=env, cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    if name == "patch":
        p.stdin.write(b"yes\n")  # confirmation was given in the page
    p.stdin.close()
    rest = b""
    for block in iter(lambda: p.stdout.read1(1024), b""):
        rest += block
        *lines, rest = re.split(rb"[\r\n]", rest)
        for l in lines:
            l = l.decode(errors="replace").strip()
            if not l:
                continue
            with lock:
                m = re.search(r"(\d+(?:\.\d+)?) ?%", l)
                if m:
                    state["percent"] = float(m.group(1))
                    continue
                if l.startswith("== Listening"):
                    state["countdown"] = time.time() + 30
                if l.startswith("== Identify"):
                    state["countdown"] = None
                note_wires(l)
                state["log"] = (state["log"] + [l])[-300:]
    rc = p.wait()
    with lock:
        status = "cancelled" if state["status"] == "cancelled" else "ok" if rc == 0 else "failed"
        state.update(status=status, end=time.time(), countdown=None)
        state["results"][name] = state["status"]


class Panel(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, type_="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", type_ + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path == "/state":
            with lock:
                return self.reply(200, json.dumps(dict(state, now=time.time(), port=current_port())))
        with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
            self.reply(200, f.read(), "text/html")

    def do_POST(self):
        # Panel-specific header: another page in the browser cannot send it without a CORS preflight.
        if self.headers.get("X-Panel") != "1":
            return self.reply(403, '{"error":"refused"}')
        if self.path == "/clear":
            with lock:
                if state["status"] in ("running", "cancelled"):
                    return self.reply(409, '{"error":"step running"}')
                state["log"] = []
            return self.reply(200, "{}")
        if self.path == "/cancel":
            with lock:
                # Never during the patch write: a half-written image no longer boots.
                if state["status"] != "running" or state["step"] == "patch" or process is None:
                    return self.reply(409, '{"error":"nothing to cancel"}')
                state["status"] = "cancelled"
                state["log"].append("Cancelled.")
            os.killpg(process.pid, signal.SIGTERM)
            return self.reply(200, "{}")
        name = self.path.removeprefix("/run/")
        with lock:
            busy = state["status"] in ("running", "cancelled") and process is not None and process.poll() is None
        if name not in STEPS or busy:
            return self.reply(409, '{"error":"refused"}')
        threading.Thread(target=run, args=(name,), daemon=True).start()
        self.reply(200, "{}")


if __name__ == "__main__":
    # A bridge left by a previous server would hold the serial ports: stop it.
    subprocess.run(["pkill", "-f", "hybrid_bridge.py"])
    # Backup already validated: resume at the write (the patch step re-checks the chip itself).
    if os.path.exists(os.path.join(WORKDIR, "SHA256")):
        state["results"].update(wiring="ok", backup="ok")
    print(f"Panel: http://localhost:8765  (device dir {WORKDIR})")
    ThreadingHTTPServer(("127.0.0.1", 8765), Panel).serve_forever()
