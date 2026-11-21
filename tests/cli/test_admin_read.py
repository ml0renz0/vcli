"""Isolated ADMIN-01 contracts for the production Bash commands."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


class AdminReadContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = VCLI.read_text(encoding="utf-8")
        parts = []
        for name in ("_lsrole", "_showrole", "_lspol", "_showpol"):
            match = re.search(r"^function " + name + r"\(\)\{.*?^\}", source, re.M | re.S)
            if not match:
                raise AssertionError("Missing function " + name)
            parts.append(match.group(0))
        cls.commands = "\n".join(parts)

    def call(self, command, response, fail=False):
        with tempfile.TemporaryDirectory(prefix="vcli-admin-") as tmp:
            env = os.environ.copy()
            log = Path(tmp) / "requests"
            env.update({"BODY": response, "LOG": str(log),
                        "FAIL": "yes" if fail else "no"})
            stub = r'''
_curl() {
    printf '%s|%s\n' "$1" "${3:-GET}" >> "$LOG"
    [[ "$FAIL" != yes ]] || { echo 'partial'; return 22; }
    printf '%s\n' "$BODY"
}
mktemp() { echo 'unexpected tempfile' >&2; return 99; }
_cat() { echo 'unexpected second wrapper' >&2; return 98; }
'''
            result = subprocess.run(["bash", "-c", self.commands + "\n" + stub + "\n" + command],
                                    env=env, capture_output=True, text=True, timeout=8)
            requests = log.read_text().splitlines() if log.exists() else []
            return result, requests

    def test_valid_admin_reads_have_one_request_and_legacy_output(self):
        cases = (
            ("_lsrole", '{"data":{"keys":["ci-role","dev"]}}',
             "ci-role\ndev\n", "/auth/approle/role?list=true|GET"),
            ("_showrole ci-role", '{"data":{"token_policies":["default","read-only"]}}',
             "policies: default\nread-only\n", "/auth/approle/role/ci-role|GET"),
            ("_showrole ci-role", '{"data":{"policies":["read-only"]}}',
             "policies: read-only\n", "/auth/approle/role/ci-role|GET"),
            ("_showrole ci-role", '{"data":{"token_policies":[]}}',
             "policies: ", "/auth/approle/role/ci-role|GET"),
            ("_lspol", '{"data":{"keys":["default","ci-policy"]}}',
             "default\nci-policy\n", "/sys/policies/acl|LIST"),
            ("_showpol ci-policy", '{"data":{"policy":"path \\"kv1/*\\" {}"}}',
             'path "kv1/*" {}\n', "/sys/policies/acl/ci-policy|GET"),
        )
        for command, body, expected, endpoint in cases:
            with self.subTest(command=command, body=body):
                result, requests = self.call(command, body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(requests, [endpoint])

    def test_failed_http_has_no_partial_output(self):
        for command in ("_lsrole", "_showrole ci-role", "_lspol", "_showpol ci-policy"):
            with self.subTest(command=command):
                result, requests = self.call(command, '{}', fail=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_invalid_json_and_fields_fail_without_partial_output(self):
        cases = (
            ("_lsrole", '{"data":{"keys":["first",3]}}'),
            ("_showrole ci-role", '{"data":{"token_policies":["first",3]}}'),
            ("_lspol", '{"data":{"keys":["first",3]}}'),
            ("_showpol ci-policy", '{"data":{"policy":{}}}'),
            ("_lsrole", 'NOT JSON'),
            ("_showrole ci-role", 'NOT JSON'),
            ("_lspol", 'NOT JSON'),
            ("_showpol ci-policy", 'NOT JSON'),
        )
        for command, body in cases:
            with self.subTest(command=command, body=body):
                result, requests = self.call(command, body)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_missing_role_and_policy_fail_without_requests(self):
        for command in ("_showrole", "_showpol"):
            with self.subTest(command=command):
                result, requests = self.call(command, '{}')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("missing", result.stderr)
                self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
