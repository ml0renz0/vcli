"""Black-box CLI acceptance contracts, no Vault server required."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BIN = Path(__file__).resolve().parents[2] / "vcli.sh"


def run_cli(*args: str, stdin: str = "", env_extra=None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="vcli-cli-") as temp:
        env = os.environ.copy()
        for name in ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_HOST", "VAULT_PORT",
                     "VAULT_CACERT", "VAULT_SKIP_VERIFY", "VCLI_DEBUGFILE"):
            env.pop(name, None)
        env.update({"HOME": temp, "TMPDIR": temp, "TERM": "dumb"})
        if env_extra:
            env.update(env_extra)
        return subprocess.run([str(BIN), *args], env=env, input=stdin,
                              text=True, capture_output=True, timeout=10)


class CliContract(unittest.TestCase):
    def test_version_works_without_connection_and_credentials(self):
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"vcli v[0-9]+\.[0-9]+")
        self.assertNotIn("Vault token", result.stderr)

    def test_help_is_success_without_connection(self):
        result = run_cli("-h")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((result.stdout + result.stderr).strip())

    def test_missing_credentials_fails_without_leaking(self):
        result = run_cli("-n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Vault", result.stdout + result.stderr)

    def test_unknown_option_is_rejected(self):
        result = run_cli("-Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((result.stdout + result.stderr).strip())

    def test_invalid_vault_address_fails_before_network(self):
        token = "temporary-fixture-not-secret"
        result = run_cli("-n", env_extra={
            "VAULT_ADDR": "not-a-url", "VAULT_TOKEN": token
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid VAULT_ADDR", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_version_ignores_invalid_vault_address(self):
        result = run_cli("--version", env_extra={"VAULT_ADDR": "not-a-url"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("vcli", result.stdout)


if __name__ == "__main__":
    unittest.main()
