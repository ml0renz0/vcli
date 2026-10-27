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

    def cli(self, directory, command, extra_env=None):
        env = os.environ.copy()
        env.update({
            "HOME": str(directory), "TMPDIR": str(directory), "TERM": "dumb",
            "VAULT_ADDR": VAULT, "VAULT_TOKEN": ROOT_TOKEN,
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
            "VAULT_KV_VERSION": "1", "VAULT_KV_MOUNTS": "kv2,team/config",
        })
        if extra_env:
            env.update(extra_env)
        out = subprocess.run([str(VCLI), "-n"], input=command + "\n",
                             text=True, capture_output=True, env=env, timeout=20)
        self.assertNotIn(ROOT_TOKEN, out.stdout + out.stderr)
        return out

    @staticmethod
    def _ln_race_env(root, destination, action, victim=None):
        """Inject an exact race at the publication syscall, after preflight."""
        bindir = Path(root) / "hook-bin"
        bindir.mkdir()
        hook = bindir / "ln"
        hook.write_text("""#!/bin/sh
for destination in "$@"; do :; done
if [ "$destination" = "$VCLI_TEST_LN_DEST" ]; then
    case "$VCLI_TEST_LN_ACTION" in
        regular) printf 'RACE-MARKER' > "$destination" ;;
        symlink) /usr/bin/ln -s -- "$VCLI_TEST_LN_VICTIM" "$destination" ;;
        directory) /usr/bin/mkdir -- "$destination" ;;
        fail) exit 86 ;;
    esac
fi
exec /usr/bin/ln "$@"
""", encoding="utf-8")
        hook.chmod(0o755)
        return {
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "VCLI_TEST_LN_DEST": str(destination),
            "VCLI_TEST_LN_ACTION": action,
            "VCLI_TEST_LN_VICTIM": str(victim or ""),
        }

    @staticmethod
    def _assert_no_stage(root):
        assert not list(Path(root).glob(".vcli-export.*")), (
            "private staging directories must be cleaned after publication failure"
        )

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

    def test_13_getcert_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory(prefix="vcli-cert-symlink-") as root:
            marker = Path(root) / "untouched.txt"
            marker.write_text("DO NOT OVERWRITE")
            (Path(root) / "web.key").symlink_to(marker)
            out = self.cli(root, f"getcert {self.cert_path}")
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(marker.read_text(), "DO NOT OVERWRITE")
            self.assertFalse((Path(root) / "web.crt").exists())

    def test_14_getcert_refuses_existing_regular_file(self):
        with tempfile.TemporaryDirectory(prefix="vcli-cert-existing-") as root:
            existing = Path(root) / "web.crt"
            existing.write_text("KEEP ORIGINAL")
            out = self.cli(root, f"getcert {self.cert_path}")
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(existing.read_text(), "KEEP ORIGINAL")
            self.assertFalse((Path(root) / "web.key").exists())

    def test_15_getkrb_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory(prefix="vcli-krb-symlink-") as root:
            marker = Path(root) / "untouched.txt"
            marker.write_text("DO NOT OVERWRITE")
            (Path(root) / "HTTP_service.example.test@EXAMPLE.TEST.keytab").symlink_to(marker)
            out = self.cli(root, f"getkrb {self.krb_path}")
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(marker.read_text(), "DO NOT OVERWRITE")

    def test_16_getkrb_refuses_existing_regular_file(self):
        with tempfile.TemporaryDirectory(prefix="vcli-krb-existing-") as root:
            destination = Path(root) / "HTTP_service.example.test@EXAMPLE.TEST.keytab"
            destination.write_bytes(b"KEEP")
            out = self.cli(root, f"getkrb {self.krb_path}")
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(destination.read_bytes(), b"KEEP")

    def test_17_getcertlist_reports_certificate_and_san(self):
        with tempfile.TemporaryDirectory(prefix="vcli-certlist-") as root:
            out = self.cli(root, f"getcertlist {self.cert_path}")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("web", out.stdout)
            self.assertIn("cert.example.test", out.stdout)
            self.assertNotIn(self.key, out.stdout + out.stderr)

    def test_18_getcertexpiration_validates_and_reports_days(self):
        with tempfile.TemporaryDirectory(prefix="vcli-expiry-") as root:
            good = self.cli(root, f"getcertexpiration {self.cert_path}")
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("web", good.stdout)
            self.assertIn("days", good.stdout.lower())
            wrong = self.cli(root, f"getcertexpiration {self.malformed_path}")
            self.assertNotEqual(wrong.returncode, 0, wrong.stdout + wrong.stderr)

    def test_19_krbinfo_validates_and_reports_keytab(self):
        with tempfile.TemporaryDirectory(prefix="vcli-krbinfo-") as root:
            good = self.cli(root, f"krbinfo {self.krb_path}")
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn(self.principal, good.stdout)
            invalid = self.cli(root, f"krbinfo {self.invalid_krb_path}")
            self.assertNotEqual(invalid.returncode, 0, invalid.stdout + invalid.stderr)

    def test_20_getkrblist_reports_principal_without_eval(self):
        with tempfile.TemporaryDirectory(prefix="vcli-krblist-") as root:
            good = self.cli(root, f"getkrblist {self.krb_path}")
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("HTTP", good.stdout)
            self.assertIn("service.example.test", good.stdout)
            injected = self.cli(root, f"getkrblist {self.inject_path}")
            self.assertNotEqual(injected.returncode, 0, injected.stdout + injected.stderr)
            self.assertFalse(Path("/tmp/SHOULD-NOT-EXIST").exists())

    def test_21_recursive_cert_list_includes_nested_secret(self):
        deeper = f"kv1/{self.prefix}/nested/deeper/cert"
        api("POST", deeper, {"web_crt": self.crt, "web_key": self.key})
        with tempfile.TemporaryDirectory(prefix="vcli-allcertlist-") as root:
            out = self.cli(root, f"getallcertlist kv1/{self.prefix}/nested")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("web", out.stdout)
            self.assertIn("deeper/cert", out.stdout)

    def test_22_recursive_krb_list_includes_nested_secret(self):
        deeper = f"kv1/{self.prefix}/nested/deeper/keytab"
        api("POST", deeper, {
            "http_principal": self.principal,
            "http_keytab": base64.b64encode(self.kt).decode("ascii"),
        })
        with tempfile.TemporaryDirectory(prefix="vcli-allkrblist-") as root:
            out = self.cli(root, f"getallkrblist kv1/{self.prefix}/nested/deeper")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("HTTP", out.stdout)
            self.assertIn("deeper/keytab", out.stdout)

    def test_23_recursive_expiration_includes_nested_secret(self):
        deeper = f"kv1/{self.prefix}/nested/deeper/expiry"
        api("POST", deeper, {"web_crt": self.crt, "web_key": self.key})
        with tempfile.TemporaryDirectory(prefix="vcli-allcert-expiry-") as root:
            out = self.cli(root, f"getallcertexpiration kv1/{self.prefix}/nested/deeper")
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("expiry", out.stdout)
            self.assertIn("days", out.stdout.lower())



    def test_24_getcert_raced_regular_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="vcli-race-regular-") as root:
            cert = Path(root) / "web.crt"
            env = self._ln_race_env(root, cert, "regular")
            out = self.cli(root, f"getcert {self.cert_path}", env)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(cert.read_text(), "RACE-MARKER")
            self.assertFalse((Path(root) / "web.key").exists())
            self._assert_no_stage(root)

    def test_25_getcert_raced_symlink_does_not_touch_target(self):
        with tempfile.TemporaryDirectory(prefix="vcli-race-symlink-") as root:
            victim = Path(root) / "sentinel"
            victim.write_text("SAFE", encoding="utf-8")
            cert = Path(root) / "web.crt"
            env = self._ln_race_env(root, cert, "symlink", victim)
            out = self.cli(root, f"getcert {self.cert_path}", env)
            self.assertNotEqual(out.returncode, 0)
            self.assertTrue(cert.is_symlink())
            self.assertEqual(victim.read_text(), "SAFE")
            self.assertFalse((Path(root) / "web.key").exists())
            self._assert_no_stage(root)

    def test_26_getcert_raced_directory_is_not_used_as_link_destination(self):
        with tempfile.TemporaryDirectory(prefix="vcli-race-dir-") as root:
            cert = Path(root) / "web.crt"
            env = self._ln_race_env(root, cert, "directory")
            out = self.cli(root, f"getcert {self.cert_path}", env)
            self.assertNotEqual(out.returncode, 0)
            self.assertTrue(cert.is_dir())
            self.assertEqual(list(cert.iterdir()), [])
            self.assertFalse((Path(root) / "web.key").exists())
            self._assert_no_stage(root)

    def test_27_getcert_second_publish_race_rolls_back_first(self):
        with tempfile.TemporaryDirectory(prefix="vcli-race-second-") as root:
            victim = Path(root) / "sentinel"
            victim.write_text("KEEP", encoding="utf-8")
            key = Path(root) / "web.key"
            env = self._ln_race_env(root, key, "symlink", victim)
            out = self.cli(root, f"getcert {self.cert_path}", env)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertTrue(key.is_symlink())
            self.assertEqual(victim.read_text(), "KEEP")
            self.assertFalse((Path(root) / "web.crt").exists())
            self._assert_no_stage(root)

    def test_28_getkrb_race_preserves_existing_file(self):
        with tempfile.TemporaryDirectory(prefix="vcli-krb-race-") as root:
            keytab = Path(root) / "HTTP_service.example.test@EXAMPLE.TEST.keytab"
            env = self._ln_race_env(root, keytab, "regular")
            out = self.cli(root, f"getkrb {self.krb_path}", env)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(keytab.read_text(), "RACE-MARKER")
            self._assert_no_stage(root)

    def test_29_getcert_second_link_failure_rolls_back_first(self):
        with tempfile.TemporaryDirectory(prefix="vcli-link-failure-") as root:
            key = Path(root) / "web.key"
            env = self._ln_race_env(root, key, "fail")
            out = self.cli(root, f"getcert {self.cert_path}", env)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertFalse((Path(root) / "web.crt").exists())
            self.assertFalse(key.exists())
            self._assert_no_stage(root)

    def test_30_getkrb_staging_decode_failure_leaves_no_export(self):
        with tempfile.TemporaryDirectory(prefix="vcli-decode-failure-") as root:
            bindir = Path(root) / "hook-bin"
            bindir.mkdir()
            counter = Path(root) / "decode-count"
            hook = bindir / "base64"
            hook.write_text("""#!/bin/sh
count=0
if [ -f "$VCLI_TEST_BASE64_COUNTER" ]; then
    count=$(/usr/bin/cat "$VCLI_TEST_BASE64_COUNTER")
fi
count=$((count+1))
printf '%s\n' "$count" > "$VCLI_TEST_BASE64_COUNTER"
if [ "$count" -eq 2 ]; then exit 87; fi
exec /usr/bin/base64 "$@"
""", encoding="utf-8")
            hook.chmod(0o755)
            env = {
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "VCLI_TEST_BASE64_COUNTER": str(counter),
            }
            out = self.cli(root, f"getkrb {self.krb_path}", env)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(counter.read_text().strip(), "2")
            self.assertFalse(list(Path(root).glob("*.keytab")))
            self._assert_no_stage(root)


if __name__ == "__main__":
    unittest.main()
