"""PKI and Kerberos black-box tests against throwaway HTTPS Vault.

Production CLI stays Bash. Python constructs independent X.509/keytab fixtures,
launches the actual CLI, and inspects exported artifacts without trusting output.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from uuid import uuid4

from .test_vault import ROOT_TOKEN, VAULT, VCLI, api


class PkiKerberosContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.environ.get("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("Only run against the disposable TLS Vault")
        cls.prefix = "vcli-pki-" + uuid4().hex[:12]
        cls.fixtures = tempfile.TemporaryDirectory(prefix="vcli-pki-fixture-")
        cls.pem = Path(cls.fixtures.name)
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-nodes", "-days", "2", "-sha256",
            "-subj", "/CN=cert.example.test",
            "-addext", "subjectAltName=DNS:cert.example.test,DNS:alt.example.test",
            "-keyout", str(cls.pem / "service.key"),
            "-out", str(cls.pem / "service.crt"),
        ], check=True, capture_output=True, timeout=25)
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-nodes", "-days", "2", "-sha256",
            "-subj", "/CN=unrelated.example.test",
            "-keyout", str(cls.pem / "other.key"),
            "-out", str(cls.pem / "other.crt"),
        ], check=True, capture_output=True, timeout=25)
        cls.crt = (cls.pem / "service.crt").read_text()
        cls.key = (cls.pem / "service.key").read_text()
        cls.other_key = (cls.pem / "other.key").read_text()
        cls.cert_path = f"kv1/{cls.prefix}/cert"
        cls.mismatch_path = f"kv1/{cls.prefix}/mismatch"
        cls.malformed_path = f"kv1/{cls.prefix}/malformed"
        cls.missing_path = f"kv1/{cls.prefix}/missingpair"
        api("POST", cls.cert_path, {"web_crt": cls.crt, "web_key": cls.key})
        api("POST", cls.mismatch_path, {"web_crt": cls.crt, "web_key": cls.other_key})
        api("POST", cls.malformed_path, {"web_crt": "NOT A CERT",
                                        "web_key": "NOT A KEY"})
        api("POST", cls.missing_path, {"web_crt": cls.crt})
        cls.principal = "HTTP/service.example.test@EXAMPLE.TEST"
        cls.kt = cls._make_keytab()
        cls.krb_path = f"kv1/{cls.prefix}/keytab"
        api("POST", cls.krb_path, {
            "http_principal": cls.principal,
            "http_keytab": base64.b64encode(cls.kt).decode("ascii"),
        })
        cls.traversal_path = f"kv1/{cls.prefix}/dangerous-name"
        api("POST", cls.traversal_path, {
            "bad_principal": "../../escape",
            "bad_keytab": base64.b64encode(cls.kt).decode("ascii"),
        })
        cls.invalid_krb_path = f"kv1/{cls.prefix}/invalid-keytab"
        api("POST", cls.invalid_krb_path, {
            "http_principal": cls.principal,
            "http_keytab": "@@invalid-base64@@",
        })
        cls.inject_path = f"kv1/{cls.prefix}/shell-metachar"
        api("POST", cls.inject_path, {
            "http_principal": "HTTP/service.example.test@EXAMPLE.TEST",
            "http_keytab": base64.b64encode(cls.kt).decode("ascii"),
            "inject_principal": "$(touch /tmp/SHOULD-NOT-EXIST)",
            "inject_keytab": base64.b64encode(cls.kt).decode("ascii"),
        })

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    @staticmethod
    def _make_keytab():
        # MIT keytab format v2; valid AES256 entry, no KDC required for klist.
        def counted(value):
            binary = value.encode("utf-8")
            return struct.pack(">H", len(binary)) + binary
        data = (struct.pack(">H", 2) + counted("EXAMPLE.TEST")
                + counted("HTTP") + counted("service.example.test")
                + struct.pack(">I", 1)      # KRB5_NT_PRINCIPAL
                + struct.pack(">I", 1700000000)
                + struct.pack(">B", 1)      # kvno
                + struct.pack(">H", 18)     # AES256-CTS
                + struct.pack(">H", 32) + bytes(range(32)))
        return b"\x05\x02" + struct.pack(">i", len(data)) + data

    def cli(self, directory, command):
        env = os.environ.copy()
        env.update({
            "HOME": str(directory), "TMPDIR": str(directory), "TERM": "dumb",
            "VAULT_ADDR": VAULT, "VAULT_TOKEN": ROOT_TOKEN,
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
            "VAULT_KV_VERSION": "1", "VAULT_KV_MOUNTS": "kv2,team/config",
        })
        out = subprocess.run([str(VCLI), "-n"], input=command + "\n",
                             text=True, capture_output=True, env=env, timeout=20)
        self.assertNotIn(ROOT_TOKEN, out.stdout + out.stderr)
        return out

    def test_01_getcert_exports_exact_matching_pem_with_private_permissions(self):
        with tempfile.TemporaryDirectory(prefix="vcli-export-cert-") as root:
            out = self.cli(root, f"getcert {self.cert_path}")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            cert, key = Path(root) / "web.crt", Path(root) / "web.key"
            self.assertEqual(cert.read_text(), self.crt)
            self.assertEqual(key.read_text(), self.key)
            self.assertEqual(cert.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(self.key, out.stdout + out.stderr)

    def test_02_getcert_rejects_mismatched_private_key_without_exports(self):
        with tempfile.TemporaryDirectory(prefix="vcli-mismatch-cert-") as root:
            out = self.cli(root, f"getcert {self.mismatch_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.key")))
            self.assertFalse(list(Path(root).glob("*.crt")))

    def test_03_getcert_rejects_invalid_pem_without_exports(self):
        with tempfile.TemporaryDirectory(prefix="vcli-invalid-pem-") as root:
            out = self.cli(root, f"getcert {self.malformed_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.key")))
            self.assertFalse(list(Path(root).glob("*.crt")))

    def test_04_getcert_rejects_missing_pair_without_partial_export(self):
        with tempfile.TemporaryDirectory(prefix="vcli-missing-pair-") as root:
            out = self.cli(root, f"getcert {self.missing_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.key")))
            self.assertFalse(list(Path(root).glob("*.crt")))

    def test_05_getsan_uses_actual_certificate_extensions(self):
        with tempfile.TemporaryDirectory(prefix="vcli-cert-san-") as root:
            out = self.cli(root, f"getsan {self.cert_path}")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("cert.example.test", out.stdout)
            self.assertIn("alt.example.test", out.stdout)
            self.assertNotIn(self.key, out.stdout + out.stderr)

    def test_06_certinfo_rejects_key_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="vcli-certinfo-") as root:
            good = self.cli(root, f"certinfo {self.cert_path}")
            wrong = self.cli(root, f"certinfo {self.mismatch_path}")
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("cert.example.test", good.stdout)
            self.assertNotEqual(wrong.returncode, 0, wrong.stdout + wrong.stderr)
            self.assertNotIn(self.key, good.stdout + good.stderr)

    def test_07_getkrb_exports_exact_keytab_with_private_permissions(self):
        with tempfile.TemporaryDirectory(prefix="vcli-export-krb-") as root:
            out = self.cli(root, f"getkrb {self.krb_path}")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            keytab = Path(root) / "HTTP_service.example.test@EXAMPLE.TEST.keytab"
            self.assertEqual(keytab.read_bytes(), self.kt)
            self.assertEqual(keytab.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(base64.b64encode(self.kt).decode(), out.stdout + out.stderr)

    def test_08_getprinc_validates_and_displays_real_keytab_principal(self):
        with tempfile.TemporaryDirectory(prefix="vcli-getprinc-") as root:
            out = self.cli(root, f"getprinc {self.krb_path}")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn(self.principal, out.stdout)
            self.assertNotIn("NOT FOUND", out.stdout)

    def test_09_getkrb_rejects_invalid_base64_without_file(self):
        with tempfile.TemporaryDirectory(prefix="vcli-invalid-krb-") as root:
            out = self.cli(root, f"getkrb {self.invalid_krb_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.keytab")))

    def test_10_getkrb_rejects_pathlike_principal(self):
        with tempfile.TemporaryDirectory(prefix="vcli-traversal-krb-") as root:
            out = self.cli(root, f"getkrb {self.traversal_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.keytab")))
            self.assertFalse((Path(root).parent / "escape.keytab").exists())

    def test_11_getprinc_never_evaluates_secret_fields_as_shell(self):
        # A principal is DATA, not Bash. No eval or command substitution.
        with tempfile.TemporaryDirectory(prefix="vcli-injection-krb-") as root:
            out = self.cli(root, f"getprinc {self.inject_path}")
            self.assertNotEqual(out.returncode, 0)
            self.assertNotIn(self.key, out.stdout + out.stderr)
            self.assertFalse(Path("/tmp/SHOULD-NOT-EXIST").exists())

    def test_12_missing_pki_secret_returns_error_without_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="vcli-missing-cert-") as root:
            out = self.cli(root, f"getcert kv1/{self.prefix}/does-not-exist")
            self.assertNotEqual(out.returncode, 0)
            self.assertFalse(list(Path(root).glob("*.key")))
            self.assertFalse(list(Path(root).glob("*.crt")))


if __name__ == "__main__":
    unittest.main()
