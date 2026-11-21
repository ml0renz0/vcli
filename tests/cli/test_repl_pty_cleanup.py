"""Ensure isolated PTY sessions are reaped without leaking test fixtures."""
from __future__ import annotations
import os
import pty
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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


    def test_cleanup_direct_kill_when_group_signal_does_not_terminate_child(self):
        home = tempfile.TemporaryDirectory(prefix="vcli-pty-race-")
        path = Path(home.name)
        master, slave = pty.openpty()
        child = None
        try:
            child = subprocess.Popen(
                ["bash", "-c", "sleep 60"], stdin=slave, stdout=slave,
                stderr=slave, start_new_session=True,
            )
            os.close(slave)
            slave = -1
            with patch.object(repl.os, "killpg", return_value=None) as killpg:
                repl.InteractiveShellTests().end(home, master, child)
                killpg.assert_called_once_with(child.pid, signal.SIGKILL)
            master = -1
            self.assertIsNotNone(child.poll())
            self.assertFalse(path.exists())
        finally:
            if slave >= 0:
                os.close(slave)
            if master >= 0:
                os.close(master)
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if path.exists():
                home.cleanup()


if __name__ == "__main__":
    unittest.main()
