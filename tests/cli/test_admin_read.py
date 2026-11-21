"""Isolated ADMIN-01 contracts for the production Bash commands."""
from __future__ import annotations
import json
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
        for name in ("vcli_admin_name_valid", "vcli_admin_read_response", "vcli_admin_extract", "_lsrole", "_showrole", "_lspol", "_showpol"):
            match = re.search(r"^function " + name + r"\(\)\{.*?^\}", source, re.M | re.S)
            if not match:
                raise AssertionError("Missing function " + name)
            parts.append(match.group(0))
        cls.commands = "\n".join(parts)

    def call(self, command, response, fail=False, count_jq=False):
        with tempfile.TemporaryDirectory(prefix="vcli-admin-") as tmp:
            env = os.environ.copy()
            log = Path(tmp) / "requests"
            jq_trace = Path(tmp) / "jq-trace"
            env.update({"BODY": response, "LOG": str(log), "JQ_TRACE": str(jq_trace),
                        "FAIL": "yes" if fail else "no"})
            stub = r'''
_curl() {
    printf '%s|%s\n' "$1" "${3:-GET}" >> "$LOG"
    [[ "$FAIL" != yes ]] || { echo 'partial'; return 22; }
    printf '%s\n' "$BODY"
}
jq() { printf 'jq\n' >> "$JQ_TRACE"; command jq "$@"; }
mktemp() { echo 'unexpected tempfile' >&2; return 99; }
_cat() { echo 'unexpected second wrapper' >&2; return 98; }
'''
            result = subprocess.run(["bash", "-c", self.commands + "\n" + stub + "\n" + command],
                                    env=env, capture_output=True, text=True, timeout=8)
            requests = log.read_text().splitlines() if log.exists() else []
            if count_jq:
                jq_calls = jq_trace.read_text().splitlines() if jq_trace.exists() else []
                self.assertEqual(jq_calls, ["jq"], f"{command}: unexpected jq count")
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
                self.assertIn("vcli: failed to read", result.stderr)

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


    def test_reject_unsafe_role_and_policy_names_before_network(self):
        unsafe = (
            ".", "..", "../admin", "team/admin", "/admin", "foo?list=true",
            "foo#fragment", "foo\\bar", "foo bar", "foo\tbar",
            "foo\nbar", "foo\rbar",
        )
        for command in ("_showrole", "_showpol"):
            for name in unsafe:
                with self.subTest(command=command, name=repr(name)):
                    # Quote the argument for bash: the test controls the fixture.
                    # Names cannot become Bash code, including embedded quotes.
                    env_command = command + ' "$ADMIN_NAME"'
                    with tempfile.TemporaryDirectory(prefix="admin-invalid-") as tmp:
                        log = Path(tmp) / "calls"
                        env = os.environ.copy()
                        env.update({"ADMIN_NAME": name, "LOG": str(log)})
                        stub = r'''
_curl() { printf 'network\n' >> "$LOG"; return 0; }
'''
                        result = subprocess.run(
                            ["bash", "-c", self.commands + "\n" + stub + "\n" + env_command],
                            env=env, text=True, capture_output=True, timeout=8,
                        )
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertEqual(result.stdout, "")
                        self.assertIn("invalid", result.stderr)
                        self.assertFalse(log.exists(), "unsafe name triggered transport")

    def test_safe_single_segment_names_keep_existing_endpoint_encoding_path(self):
        cases = (
            ("_showrole ci-role_9", "/auth/approle/role/ci-role_9|GET"),
            ("_showpol ops.v2", "/sys/policies/acl/ops.v2|GET"),
            ("_showpol a+b", "/sys/policies/acl/a+b|GET"),
            ("_showrole test%25", "/auth/approle/role/test%25|GET"),
        )
        for command, path in cases:
            with self.subTest(command=command):
                response = ('{"data":{"token_policies":["default"]}}'
                            if command.startswith("_showrole") else
                            '{"data":{"policy":"path \\"kv1/*\\" {}"}}')
                result, requests = self.call(command, response)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(requests, [path])


    def test_empty_administrative_lists_are_valid(self):
        cases = (
            ("_lsrole", '{"data":{"keys":[]}}'),
            ("_lspol", '{"data":{"keys":[]}}'),
            ("_lspol", '{"keys":[]}'),
        )
        for command, body in cases:
            with self.subTest(command=command, body=body):
                result, requests = self.call(command, body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_multiple_json_documents_are_rejected_atomically(self):
        cases = (
            ("_lsrole", '{"data":{"keys":["first"]}}'),
            ("_lspol", '{"data":{"keys":["first"]}}'),
            ("_showrole ci-role", '{"data":{"token_policies":["first"]}}'),
            ("_showpol ci-policy", '{"data":{"policy":"path \\"kv1/*\\" {}"}}'),
        )
        for command, body in cases:
            with self.subTest(command=command):
                result, requests = self.call(command, body + "\n" + body)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("vcli: invalid", result.stderr)
                self.assertEqual(len(requests), 1)

    def test_empty_policy_document_fails_closed(self):
        for body in ('{"data":{"policy":""}}', '{"policy":""}'):
            with self.subTest(body=body):
                result, requests = self.call("_showpol ci-policy", body)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("vcli: invalid policy response", result.stderr)
                self.assertEqual(len(requests), 1)



    def test_one_json_parse_per_admin_read(self):
        cases = (
            ("_lsrole", '{"data":{"keys":["role-a"]}}'),
            ("_lspol", '{"data":{"keys":["policy-a"]}}'),
            ("_showrole role-a", '{"data":{"token_policies":["default"]}}'),
            ("_showpol policy-a", '{"data":{"policy":"path \\"kv1/*\\" {}"}}'),
        )
        for command, body in cases:
            with self.subTest(command=command):
                result, requests = self.call(command, body, count_jq=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(requests), 1)

    def test_admin_invalid_root_type_and_field_schema_are_rejected(self):
        bad = (
            ("_lsrole", "[]"),
            ("_lspol", '{"data":{"keys":"not-a-list"}}'),
            ("_showrole role-a", '{"data":{"token_policies":{}}}'),
            ("_showpol policy-a", '{"data":{"policy":null}}'),
            ("_showpol policy-a", '{"policy":42}'),
        )
        for command, body in bad:
            with self.subTest(command=command, body=body):
                result, requests = self.call(command, body, count_jq=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_legacy_policy_root_fallback_and_multiline_acl_preserved(self):
        acl = 'path "kv1/*" {\n  capabilities = ["read"]\n}'
        body = json.dumps({"policy": acl})
        result, requests = self.call("_showpol policy-a", body, count_jq=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, acl + "\n")
        self.assertEqual(requests, ["/sys/policies/acl/policy-a|GET"])


if __name__ == "__main__":
    unittest.main()
