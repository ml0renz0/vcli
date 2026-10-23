"""Linux PTY smoke tests for VCLI's legacy interactive key-reader.

Not a replacement for the planned full keyboard regression matrix.
"""
from __future__ import annotations

import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli-revival.sh"


def read_until(fd, marker: bytes, timeout=8) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], min(0.2, deadline-time.monotonic()))
        if not readable:
            continue
        try:
            chunk = os.read(fd, 8192)
        except OSError:
            break
        if not chunk:
            break
        data.extend(chunk)
        if marker in data:
            return bytes(data)
    raise AssertionError(f"Expected {marker!r} in PTY output: {bytes(data)[-1500:]!r}")


class InteractiveShellTests(unittest.TestCase):
    def test_interactive_help_and_ctrl_d(self):
        with tempfile.TemporaryDirectory(prefix="vcli-ci-repl-") as home:
            env = os.environ.copy()
            env.update({
                "HOME": home,
                "TERM": "xterm",
                "TMPDIR": home,
                "VAULT_ADDR": os.environ["VAULT_ADDR"],
                "VAULT_CACERT": os.environ["VAULT_CACERT"],
                "VAULT_TOKEN": os.environ.get("VCLI_TEST_READONLY_TOKEN", ""),
            })
            master, slave = pty.openpty()
            child = subprocess.Popen([str(VCLI)], stdin=slave, stdout=slave,
                                     stderr=slave, env=env, start_new_session=True)
            os.close(slave)
            try:
                read_until(master, b" >", timeout=10)
                for key in b"help\n":
                    os.write(master, bytes([key]))
                    time.sleep(0.015)
                read_until(master, b"Available commands:")
                # Wait for the REPL to finish rendering help before Ctrl+D.
                time.sleep(0.5)
                os.write(master, b"\x04")  # Ctrl+D
                self.assertEqual(child.wait(timeout=8), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                os.close(master)


if __name__ == "__main__":
    unittest.main()
