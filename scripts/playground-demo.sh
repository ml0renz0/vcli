#!/usr/bin/env bash
# Drive a real interactive Bash terminal; the only displayed bytes come from its PTY.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -f .playground/env.sh && -f .playground/ready ]] || {
    echo 'Start playground: scripts/playground.sh up' >&2; exit 1
}
exec python3 - <<'PY'
import errno
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
from pathlib import Path

# This is a real Bash prompt, not a simulated VCLI prompt.
SHELL = rb"playground\$ $"
VCLI = rb"\[([0-9]{3})\] [^\r\n]* > $"
ANSI = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\([A-Za-z])")
size = struct.pack("HHHH", 30, 100, 0, 0)
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, size)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TERM") if k in os.environ}
    env.update(PS1="playground$ ", HISTFILE="/dev/null", TERM="xterm-256color")
    os.execvpe("bash", ["bash", "--noprofile", "--norc", "-i"], env)

def output(timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return None
    try:
        data = os.read(fd, 65536)
    except OSError as exc:
        if exc.errno != errno.EIO:
            raise
        return b""
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return data

def wait_prompt(pattern):
    data = bytearray()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        chunk = output(0.1)
        if chunk == b"":
            raise RuntimeError("terminal exited before the expected prompt")
        if chunk:
            data.extend(chunk)
            plain = ANSI.sub(b"", data)
            match = re.search(pattern, plain)
            if match:
                return plain, match
    raise RuntimeError("timed out waiting for the real terminal prompt")

def type_line(command):
    # Write real keystrokes, allowing the CLI's own editor to echo them.
    time.sleep(0.35)
    for key in command.encode() + b"\n":
        os.write(fd, bytes([key]))
        time.sleep(0.035)

def run(command, prompt=VCLI, expected=b"", code=None):
    type_line(command)
    data, match = wait_prompt(prompt)
    if expected and expected not in data:
        raise RuntimeError("expected command output missing: " + command)
    if code is not None and match.group(1) != code:
        raise RuntimeError("unexpected HTTP status: " + command)

saved = termios.tcgetattr(1) if os.isatty(1) else None
reaped = False
try:
    if saved:
        tty.setraw(1)
    wait_prompt(SHELL)
    run("scripts/playground.sh status", SHELL, b"verified TLS")
    run("scripts/playground.sh shell readonly")
    run("ls kv1/demo/")
    run("tree kv1/demo/nested")
    run("cat kv1/demo/nested/config")
    run("cat kv2/demo/payments/database")
    run("cat team/config/demo/payments")
    run("getsan kv1/demo/pki/web", expected=b"alt.playground.example.test")
    run("getprinc kv1/demo/kerberos/http", expected=b"HTTP/service.example.test@EXAMPLE.TEST")
    run('put kv1/demo/app {"x":"DENIED"}', code=b"403")
    run("cat kv1/demo/app", expected=b"FAKE-VCLI-PASSWORD", code=b"200")
    run("exit", SHELL)
    run("scripts/playground.sh shell admin")
    run("lsrole", expected=b"vcli-playground-reader")
    run("showrole vcli-playground-reader")
    run("showpol vcli-playground-read")
    run("exit", SHELL)
    type_line("exit 0")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if output(0.1) == b"":
            break
    else:
        raise RuntimeError("shell did not exit")
    _, status = os.waitpid(pid, 0)
    reaped = True
    if status != 0:
        raise RuntimeError("interactive shell failed")
    # Recorder v2 does not propagate the command exit status.
    if os.environ.get("VCLI_DEMO_DONE"):
        Path(os.environ["VCLI_DEMO_DONE"]).touch(exist_ok=False)
finally:
    os.close(fd)
    if not reaped:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    if saved:
        termios.tcsetattr(1, termios.TCSANOW, saved)
PY
