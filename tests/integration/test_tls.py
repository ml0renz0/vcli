"""Integration TLS checks against the actual Vault server's HTTPS listener.

The ephemeral Vault in run.sh generates its own private CA and serves TLS.
There is deliberately no Python TLS proxy or mocked Vault response here.
"""
from __future__ import annotations

import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from urllib import request
from uuid import uuid4

from .test_vault import VAULT, api, invoke


class VaultTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert = Path(os.environ["VAULT_CACERT"])
        if not cls.cert.is_file() or not VAULT.startswith("https://"):
            raise RuntimeError("Live TLS fixture required")
        cls.path = f"kv1/tls-{uuid4().hex[:12]}"
        api("POST", cls.path, {"through": "valid-tls"})

    def test_valid_certificate_and_actual_vault_https(self):
        ctx = ssl.create_default_context(cafile=str(self.cert))
        with request.urlopen(f"{VAULT}/v1/sys/health", context=ctx, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        result, _ = invoke(f"cat {self.path}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("valid-tls", result.stdout)

    def test_startup_uses_curl_not_external_gnu_timeout(self):
        # Exercise the real TLS health probe with an adversarial timeout
        # executable in PATH. The CLI must never invoke it.
        with tempfile.TemporaryDirectory(prefix="vcli-no-gnu-timeout-") as tmp:
            marker = Path(tmp) / "invoked-timeout"
            shim = Path(tmp) / "timeout"
            shim.write_text(
                '#!/bin/sh\nprintf "used\\n" > "$VCLI_TEST_TIMEOUT_MARKER"\nexit 91\n',
                encoding="utf-8",
            )
            shim.chmod(0o755)
            result, _ = invoke("", env_extra={
                "PATH": str(Path(tmp)) + os.pathsep + os.environ["PATH"],
                "VCLI_TEST_TIMEOUT_MARKER": str(marker),
            })
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(marker.exists(), "CLI invoked external GNU timeout")

    def test_empty_batch_startup_rejects_untrusted_vault_tls(self):
        # A TCP handshake alone is insufficient to trust the Vault endpoint.
        result, _ = invoke("", env_extra={"VAULT_CACERT": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot establish connection", result.stderr)

    def test_unknown_ca_must_fail_closed(self):
        result, _ = invoke(f"cat {self.path}", env_extra={"VAULT_CACERT": ""})
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("valid-tls", result.stdout + result.stderr)

    def test_unrelated_ca_must_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="vcli-wrong-ca-") as tmp:
            wrong_ca = Path(tmp) / "unrelated-ca.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-nodes", "-days", "1", "-sha256", "-subj", "/CN=unrelated",
                "-keyout", str(Path(tmp) / "unrelated.key"), "-out", str(wrong_ca),
            ], capture_output=True, check=True, timeout=20)
            result, _ = invoke(f"cat {self.path}",
                               env_extra={"VAULT_CACERT": str(wrong_ca)})
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("valid-tls", result.stdout + result.stderr)

    def test_insecure_override_must_be_explicit(self):
        result, _ = invoke(f"cat {self.path}",
                           env_extra={"VAULT_CACERT": "", "VAULT_SKIP_VERIFY": "true"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("valid-tls", result.stdout)


if __name__ == "__main__":
    unittest.main()
