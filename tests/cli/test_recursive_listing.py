"""Isolated contracts for the production recursive KV listing helper.

Tests the exact Bash function, with only _curl replaced by a deterministic
response. Real Vault integration suites remain the behavioral authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


def production_helper():
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^function _vcli_children\(\)\{.*?^\}", source,
                      re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("Missing production _vcli_children helper")
    return match.group(0)


class RecursiveListingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = production_helper()

    def call(self, payload, failure=False):
        stub = """
_curl() {
    [[ "$1" == /kv1/test/ && "$3" == LIST ]] || return 99
    [[ ${VCLI_LIST_FAIL:-0} != 1 ]] || return 7
    printf '%s\\n' "$VCLI_LIST_RESPONSE"
}
_vcli_children /kv1/test
"""
        env = os.environ.copy()
        env["VCLI_LIST_RESPONSE"] = payload
        env["VCLI_LIST_FAIL"] = "1" if failure else "0"
        return subprocess.run(
            ["bash", "-c", self.helper + stub],
            env=env, text=True, capture_output=True, timeout=5,
        )

    def test_lists_files_and_directories_in_order(self):
        result = self.call(json.dumps({"data": {"keys": ["alpha", "nested/", "z"]}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["alpha", "nested/", "z"])

    def test_empty_directory_is_success(self):
        result = self.call('{"data":{"keys":[]}}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_listing_fails_closed(self):
        for invalid in ('{"data":{"keys":"not-an-array"}}',
                        '{"data":{"keys":null}}', '{"data":{}}',
                        '{"data":{"keys":42}}', 'NOT JSON'):
            with self.subTest(invalid=invalid):
                result = self.call(invalid)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, "")

    def test_curl_failure_is_not_treated_as_empty_directory(self):
        result = self.call('{"data":{"keys":[]}}', failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
