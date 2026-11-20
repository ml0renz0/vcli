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
import tempfile
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

    def test_tree_avoids_basename_subprocess_and_handles_trailing_slashes(self):
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _tree\(\)\{.*?^\}", source,
                          re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match)
        stub = r'''
current_pwd="/"
debug(){ :; }
padecho(){ printf "%s\n" "$1"; }
basename(){ echo "external basename called" >&2; return 91; }
_vcli_children(){
  case "$1" in
    /kv1/branch) printf "child/\n" ;;
    /kv1/branch/child) printf "leaf\n" ;;
    /) return 0 ;;
    *) echo "Unexpected path: $1" >&2; return 9 ;;
  esac
}
_tree "/kv1/branch///" && _tree "/"
'''
        result = subprocess.run(["bash", "-c", match.group(0) + stub],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["branch/", "child/", "leaf", "//"])
    def test_backup_avoids_repeated_mkdir_and_preserves_nested_files(self):
        """Six nested leaves share two secret directories."""
        import tarfile
        import tempfile
        match = re.search(r"^function _backup\(\)\{.*?^\}",
                          SOURCE.read_text(encoding="utf-8"),
                          re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match)
        shell = r'''
_vcli_absolute_path(){ printf "%s\n" "$1"; }
_cat(){
  [[ "$1" != "/kv1/sample" ]] || return 1
  printf '{"data":{"key":"value"}}\n'
}
_vcli_leaves(){
  printf "%s\n" \
    "/kv1/sample/one/a" "/kv1/sample/one/b" \
    "/kv1/sample/two words/a" "/kv1/sample/two words/b" \
    "/kv1/sample/two words/c" "/kv1/sample/one/c"
}
dirname(){ echo "external dirname unexpectedly called" >&2; return 91; }
mkdir(){ printf "mkdir\n" >> "$MKDIR_LOG"; command mkdir "$@"; }
_backup /kv1/sample
'''
        with tempfile.TemporaryDirectory(prefix="vcli-backup-contract-") as home:
            env = os.environ.copy()
            env.update({"HOME": home, "TMP": home, "TMPDIR": home,
                        "MKDIR_LOG": str(Path(home) / "mkdir.log")})
            link_match = re.search(r"^function vcli_link_exclusive\(\)\{.*?^\}",
                                   SOURCE.read_text(encoding="utf-8"),
                                   re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(link_match)
            result = subprocess.run(["bash", "-c", link_match.group(0) + "\n" + match.group(0) + shell],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(home) / "mkdir.log").read_text().splitlines(),
                             ["mkdir", "mkdir"])
            archives = list(Path(home).glob("backup_vault*.tgz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0], "r:gz") as tgz:
                members = {m.name.removeprefix("./"): m for m in tgz if m.isfile()}
                self.assertEqual(set(members), {
                    "kv1/sample/one/a", "kv1/sample/one/b",
                    "kv1/sample/two words/a", "kv1/sample/two words/b",
                    "kv1/sample/two words/c", "kv1/sample/one/c",
                })
                for name, member in members.items():
                    self.assertEqual(tgz.extractfile(member).read(),
                                     b'{"key":"value"}\n', name)

    def test_curl_failure_is_not_treated_as_empty_directory(self):
        result = self.call('{"data":{"keys":[]}}', failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")



class LsListingContracts(unittest.TestCase):
    """Regression contracts for the exact production ls function."""

    @classmethod
    def setUpClass(cls):
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _ls\(\)\{.*?^\}", source, re.M | re.S)
        if not match:
            raise AssertionError("Missing production _ls")
        cls.ls = match.group(0)

    def call_ls(self, command, body='{"data":{"keys":["team/","secret"]}}',
                fail_http=False, fail_temp=False):
        with tempfile.TemporaryDirectory(prefix="vcli-ls-test-") as tmp:
            trace = Path(tmp) / "trace"
            env = os.environ.copy()
            env.update({
                "TMP": tmp, "VCLI_SESSION_TMP": tmp, "TRACE": str(trace),
                "BODY": body, "FAIL_HTTP": "yes" if fail_http else "no",
            })
            stub = r'''
CYAN="<cyan>"
NONE="</cyan>"
current_pwd="/kv1"
_curl() {
    printf 'curl\n' >> "$TRACE"
    [[ "$FAIL_HTTP" != yes ]] || return 22
    printf '%s\n' "$BODY"
}
jq() {
    printf 'jq\n' >> "$TRACE"
    command jq "$@"
}
_cat() { printf '"kv1/"\n"kv2/"\n'; }
'''
            if fail_temp:
                stub += '\nmktemp() { return 17; }\n'
            result = subprocess.run(["bash", "-c", self.ls + stub + "\n" + command],
                                    env=env, capture_output=True, text=True, timeout=8)
            calls = trace.read_text().splitlines() if trace.exists() else []
            leftovers = list(Path(tmp).glob("ls.*"))
            return result, calls, leftovers

    def test_single_http_and_jq_call_with_and_without_color(self):
        for command, expected in (
            ('_ls "/kv1/test/"', '<cyan>team</cyan>/\nsecret\n'),
            ('_ls "-_disable_colors_-" "/kv1/test/"', 'team/\nsecret\n'),
        ):
            with self.subTest(command=command):
                result, calls, leftovers = self.call_ls(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(calls, ["curl", "jq"])
                self.assertEqual(leftovers, [])

    def test_http_failure_never_reaches_json_parser_and_cleans_temp(self):
        for command in ('_ls "/kv1/test/"',
                        '_ls "-_disable_colors_-" "/kv1/test/"'):
            with self.subTest(command=command):
                result, calls, leftovers = self.call_ls(command, fail_http=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(calls, ["curl"])
                self.assertEqual(leftovers, [])

    def test_invalid_json_and_temp_creation_failure_are_not_success(self):
        result, calls, leftovers = self.call_ls('_ls "/kv1/test/"',
                                                  body="not JSON")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(calls, ["curl", "jq"])
        self.assertEqual(leftovers, [])
        result, calls, leftovers = self.call_ls('_ls "/kv1/test/"', fail_temp=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(leftovers, [])

    def test_root_listing_preserves_root_display_without_http(self):
        result, calls, leftovers = self.call_ls('_ls "-_disable_colors_-" "/"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "kv1/\nkv2/\n")
        self.assertEqual(calls, [])
        self.assertEqual(leftovers, [])

if __name__ == "__main__":
    unittest.main()
