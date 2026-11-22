"""04-Q: recursive PKI/Kerberos commands read each Vault secret only once.

The actual production Bash functions run in an isolated synthetic harness.
The real Vault TLS and cryptographic validation remain covered by E2E.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"
FUNCTIONS = (
    "_getkrblist", "vcli_krb_list_from_data",
    "_getcertlist", "vcli_cert_list_from_data",
    "_getcertexpiration", "vcli_cert_expiration_from_data",
    "_getallkrblist", "_getallcertlist", "_getallcertexpiration",
)


def production_functions():
    src = SOURCE.read_text(encoding="utf-8")
    found = []
    for name in FUNCTIONS:
        match = re.search(r"^function " + name + r"\(\)\{.*?^\}", src, re.M | re.S)
        if match is None:
            raise AssertionError(f"Missing production function: {name}")
        found.append(match.group(0))
    return "\n".join(found)


HARNESS = r'''
_vcli_absolute_path(){ printf '/kv1/test\n'; }
_vcli_leaves(){
    printf '/kv1/test/cert\n'
    if [[ "$TEST_FAIL" == yes ]]; then
        printf '/kv1/test/fail\n'
        return 0
    fi
    printf '/kv1/test/krb\n/kv1/test/other\n'
}
_vcli_pki_json(){
    printf '%s\n' "$1" >> "$READ_LOG"
    case "$1" in
        /kv1/test/cert) printf '%s\n' '{"web_crt":"synthetic-crt","web_key":"synthetic-key"}' ;;
        /kv1/test/krb) printf '%s\n' '{"http_principal":"HTTP/service@EXAMPLE.TEST","http_keytab":"AA=="}' ;;
        /kv1/test/other) printf '%s\n' '{"plain":"value"}' ;;
        /kv1/test/fail) return 43 ;;
        *) return 44 ;;
    esac
}
# Mock only independent validation boundaries; E2E exercises the real tools.
_vcli_cert_pairs(){ [[ $1 == *'"web_crt"'* ]]; }
_vcli_krb_pairs(){ [[ $1 == *'"http_principal"'* ]]; }
openssl(){
    case "$*" in
        *subjectAltName*) printf '%s\n' 'X509v3 Subject Alternative Name: DNS:example.test' ;;
        *-enddate*) printf '%s\n' 'notAfter=Jan  1 00:00:00 2030 GMT' ;;
        *) return 45 ;;
    esac
}
date(){ [[ "$*" == '+%s' ]] && printf '%s\n' 1760000000 || command date "$@"; }
vcli_cert_expiry_epoch(){ printf '%s\n' 1893456000; }
cert_critical_threshold=15
cert_warning_threshold=30
"$@"
'''


class RecursivePkiReadContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = production_functions()

    def invoke(self, function, *arguments, fail=False):
        with tempfile.TemporaryDirectory(prefix="vcli-recursive-pki-") as tmp:
            read_log = Path(tmp) / "reads"
            env = os.environ.copy()
            env["READ_LOG"] = str(read_log)
            env["TEST_FAIL"] = "yes" if fail else "no"
            code = self.functions + "\n" + HARNESS
            result = subprocess.run(
                ["bash", "-c", code, "vcli-recursive-test", function, *arguments],
                env=env, capture_output=True, text=True, timeout=12)
            reads = read_log.read_text().splitlines() if read_log.exists() else []
            return result, reads

    def test_recursive_outputs_equal_single_secret_commands(self):
        cases = (
            ("_getallcertlist", "_getcertlist", "/kv1/test/cert"),
            ("_getallkrblist", "_getkrblist", "/kv1/test/krb"),
            ("_getallcertexpiration", "_getcertexpiration", "/kv1/test/cert"),
        )
        for recursive, direct, path in cases:
            with self.subTest(recursive=recursive):
                once, single_reads = self.invoke(direct, path)
                many, all_reads = self.invoke(recursive, "/kv1/test")
                self.assertEqual(once.returncode, 0, once.stderr)
                self.assertEqual(many.returncode, 0, many.stderr)
                self.assertEqual(many.stdout, once.stdout)
                self.assertEqual(single_reads, [path])
                self.assertEqual(all_reads, [
                    "/kv1/test/cert", "/kv1/test/krb", "/kv1/test/other"
                ], "Every visited secret, including nonmatching leaves, is read once")

    def test_expiration_filter_still_matches_direct_command(self):
        direct, direct_reads = self.invoke("_getcertexpiration", "/kv1/test/cert", "-oo")
        recursive, all_reads = self.invoke("_getallcertexpiration", "/kv1/test", "-oo")
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(recursive.returncode, 0, recursive.stderr)
        self.assertEqual(recursive.stdout, direct.stdout)
        self.assertEqual(len(direct_reads), 1)
        self.assertEqual(len(all_reads), 3)

    def test_recursive_read_error_stops_without_refetch(self):
        for command in ("_getallcertlist", "_getallkrblist", "_getallcertexpiration"):
            with self.subTest(command=command):
                out, reads = self.invoke(command, "/kv1/test", fail=True)
                self.assertNotEqual(out.returncode, 0)
                self.assertEqual(reads, ["/kv1/test/cert", "/kv1/test/fail"])

    def test_no_recursive_entrypoint_invokes_single_secret_getter(self):
        src = SOURCE.read_text(encoding="utf-8")
        for name in ("_getallcertlist", "_getallkrblist", "_getallcertexpiration"):
            with self.subTest(name=name):
                body = re.search(
                    r"^function " + name + r"\(\)\{.*?^\}", src, re.M | re.S
                ).group(0)
                self.assertNotIn("_getcertlist ", body)
                self.assertNotIn("_getkrblist ", body)
                self.assertNotIn("_getcertexpiration ", body)


if __name__ == "__main__":
    unittest.main()
