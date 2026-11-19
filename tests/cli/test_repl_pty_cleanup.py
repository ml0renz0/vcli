"""Ensure isolated PTY sessions are reaped without leaking test fixtures."""
from __future__ import annotations
import os
import pty
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.integration import test_repl as repl


class PtyCleanupContract(unittest.TestCase):
    def test_cleanup_kills_dedicated_process_group_and_removes_home(self):
        home = tempfile.TemporaryDirectory(prefix="vcli-pty-cleanup-")
        path = Path(home.name)
        master, slave = pty.openpty()
        try:
            child = subprocess.Popen(
                ["bash", "-c", "sleep 60"], stdin=slave, stdout=slave,
                stderr=slave, start_new_session=True,
            )
            os.close(slave)
            slave = -1
            repl.InteractiveShellTests().end(home, master, child)
            master = -1
            self.assertIsNotNone(child.poll())
            self.assertFalse(path.exists())
        finally:
            if slave >= 0:
                os.close(slave)
            if master >= 0:
                os.close(master)
            if "child" in locals() and child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            if path.exists():
                home.cleanup()


if __name__ == "__main__":
    unittest.main()
