"""Black-box startup contracts for minimal Linux containers.

These tests require no Vault service and run with stdout/stderr redirected.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"
SCRUB = (
    "VAULT_ADDR", "VAULT_TOKEN", "VAULT_HOST", "VAULT_PORT",
    "VAULT_CACERT", "VAULT_SKIP_VERIFY", "VAULT_ROLE_ID",
    "VAULT_SECRET_ID", "VCLI_DEBUGFILE",
)


class MinimalEnvironmentContract(unittest.TestCase):
    def invoke(self, *args: str, term: str | None = "dumb", extra_env=None):
        with tempfile.TemporaryDirectory(prefix="vcli-minimal-") as directory:
            env = os.environ.copy()
            for name in SCRUB:
                env.pop(name, None)
            env.update({"HOME": directory, "TMPDIR": directory})
            if extra_env:
                env.update(extra_env)
            if term is None:
                env.pop("TERM", None)
            else:
                env["TERM"] = term
            result = subprocess.run(
                [str(VCLI), *args],
                env=env,
                cwd=directory,
                input="",
                capture_output=True,
                text=True,
                timeout=8,
            )
            files = sorted(p.name for p in Path(directory).iterdir())
            return result, files

    def test_version_without_term(self):
        result, files = self.invoke("--version", term=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"vcli v\d+\.\d+")
        self.assertEqual(files, [], "Version query must not create local files")

    def test_help_with_dumb_terminal(self):
        result, files = self.invoke("-h", term="dumb")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertNotIn("\x1b[", result.stdout + result.stderr)
        self.assertEqual(files, [], "Help must not create local files")

    def test_help_with_unknown_terminal(self):
        result, files = self.invoke("-h", term="vcli-no-terminfo-entry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertNotIn("\x1b[", result.stdout + result.stderr)
        self.assertEqual(files, [])

    def test_help_redirected_with_xterm_does_not_emit_ansi(self):
        result, files = self.invoke("-h", term="xterm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertNotIn("\x1b[", result.stdout + result.stderr)
        self.assertEqual(files, [])

    def test_invalid_address_fails_without_network_with_no_term(self):
        # Malformed addresses must be rejected before attempting to connect.
        token = "portability-test-only-token"
        result, files = self.invoke(
            "-n", term=None, extra_env={
                "VAULT_ADDR": "not-a-url", "VAULT_TOKEN": token,
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid VAULT_ADDR", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
