"""Black-box contracts for session and diagnostic CLI commands over real TLS Vault."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib import error
from uuid import uuid4

from .test_vault import VAULT, api, invoke


class SessionCommandContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.environ.get("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("Run only against disposable HTTPS Vault")

    def test_01_exit_stops_execution_before_following_write(self):
        path = f"kv1/vcli-exit-{uuid4().hex[:12]}/must-not-exist"
        result, history = invoke(f"help\nexit\nput {path} {{\"unsafe\":true}}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Available commands:", result.stdout)
        self.assertNotIn("put ", history)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", path)
        self.assertEqual(ctx.exception.code, 404)

    def test_02_verbose_toggles_on_and_off(self):
        result, _ = invoke("verbose\nverbose\nhelp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("verbose mode ON"), 1)
        self.assertEqual(result.stdout.count("verbose mode OFF"), 1)
        self.assertIn("Available commands:", result.stdout)

    def test_03_enable_debug_records_following_commands(self):
        with TemporaryDirectory(prefix="vcli-debug-") as directory:
            logfile = Path(directory) / "trace.log"
            result, _ = invoke("enable-debug\nhelp",
                               env_extra={"VCLI_DEBUGFILE": str(logfile)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("debug enabled", result.stdout)
            self.assertTrue(logfile.is_file(), "Debug must create its configured log")
            self.assertIn("run_cmd: help", logfile.read_text())
            self.assertEqual(logfile.stat().st_mode & 0o777, 0o600)

    def test_04_disable_debug_stops_writing_diagnostics(self):
        with TemporaryDirectory(prefix="vcli-debug-") as directory:
            logfile = Path(directory) / "trace.log"
            result, _ = invoke("enable-debug\nhelp\ndisable-debug\nhistory",
                               env_extra={"VCLI_DEBUGFILE": str(logfile)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("debug disabled", result.stdout)
            log = logfile.read_text()
            self.assertIn("run_cmd: help", log)
            self.assertNotIn("run_cmd: history", log)

    def test_05_enable_hexdump_debug_reports_activation(self):
        result, _ = invoke("enable-hexdump-debug\nhelp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hexdump debug enabled", result.stdout)
        self.assertIn("Available commands:", result.stdout)

    def test_06_disable_hexdump_debug_reports_deactivation(self):
        result, _ = invoke("enable-hexdump-debug\ndisable-hexdump-debug\nhelp")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hexdump debug enabled", result.stdout)
        self.assertIn("hexdump debug disabled", result.stdout)
        self.assertIn("Available commands:", result.stdout)


if __name__ == "__main__":
    unittest.main()
