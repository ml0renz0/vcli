"""Isolated contracts for navigation and LIST failure propagation in production Bash."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


class NavigationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _cd\(\)\{.*?^\}", code, re.M | re.S)
        if not match:
            raise AssertionError("Missing production _cd")
        cls.cd = match.group(0)
        resolver = re.search(r"^function _vcli_resolve_path\(\)\{.*?^\}",
                             code, re.M | re.S)
        if not resolver:
            raise AssertionError("Missing production path resolver")
        cls.resolver = resolver.group(0)

    def run_cd(self, lines, initial="", listing="child\n", fail=False):
        with tempfile.TemporaryDirectory(prefix="vcli-nav-") as temp:
            log = Path(temp) / "lists"
            env = os.environ.copy()
            env.update({"START": initial, "LISTING": listing, "LIST_LOG": str(log),
                        "LIST_FAIL": "yes" if fail else "no"})
            shell = r'''
current_pwd="$START"
debug() { :; }
_ls() {
    printf '%s\n' "$*" >> "$LIST_LOG"
    if [[ "$LIST_FAIL" == yes ]]; then
        printf 'unexpected partial output\n'
        return 22
    fi
    printf '%s' "$LISTING"
}
''' + self.resolver + "\n" + self.cd + "\n" + lines + "\n"
            result = subprocess.run(["bash", "-c", shell], env=env,
                                    capture_output=True, text=True, timeout=5)
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_absolute_cd_normalizes_trailing_slash_for_cat_and_put(self):
        result, calls = self.run_cd('_cd /kv1/team; printf "PWD:%s\\nREAD:%s\\n" "$current_pwd" "$current_pwd""secret"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PWD:/kv1/team/\nREAD:/kv1/team/secret\n")
        self.assertEqual(calls, ["-_disable_colors_- /kv1/team/"])

    def test_relative_cd_parent_and_root_have_canonical_paths(self):
        commands = r'''
_cd kv1/team &&
_cd child &&
printf 'NESTED:%s\n' "$current_pwd" &&
_cd .. &&
printf 'PARENT:%s\n' "$current_pwd" &&
_cd .. &&
printf 'MOUNT:%s\n' "$current_pwd" &&
_cd / &&
printf 'ROOT:%s\n' "$current_pwd" &&
_cd .. &&
printf 'ROOT_AFTER_PARENT:%s\n' "$current_pwd"
'''
        result, calls = self.run_cd(commands)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NESTED:/kv1/team/child/\n", result.stdout)
        self.assertIn("PARENT:/kv1/team/\n", result.stdout)
        self.assertIn("MOUNT:/kv1/\n", result.stdout)
        self.assertIn("ROOT:\nROOT_AFTER_PARENT:\n", result.stdout)
        self.assertEqual(calls, ["-_disable_colors_- /kv1/team/",
                                 "-_disable_colors_- /kv1/team/child/"])

    def test_failed_list_fails_closed_without_changing_directory(self):
        commands = r'''
if _cd /kv1/denied; then exit 99; fi
printf 'PWD:%s\n' "$current_pwd"
'''
        result, calls = self.run_cd(commands, initial="/kv1/valid/", fail=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PWD:/kv1/valid/\n")
        self.assertIn("cannot list directory", result.stderr)
        self.assertEqual(calls, ["-_disable_colors_- /kv1/denied/"])

    def test_no_directory_entries_is_reported_as_not_found_and_fails(self):
        result, calls = self.run_cd(
            'if _cd /kv1/empty; then exit 99; fi; printf "PWD:%s\\n" "$current_pwd"',
            initial="/kv1/valid/", listing="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PWD:/kv1/valid/\n")
        self.assertIn("not found", result.stderr)
        self.assertEqual(calls, ["-_disable_colors_- /kv1/empty/"])

    def test_dot_segments_are_rejected_without_list_or_state_mutation(self):
        result, calls = self.run_cd(
            'if _cd /kv1/a/../b; then exit 99; fi; printf "PWD:%s\\n" "$current_pwd"',
            initial="/kv1/valid/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PWD:/kv1/valid/\n")
        self.assertIn("unsupported dot segment", result.stderr)
        self.assertEqual(calls, [])

    def test_empty_and_dot_are_noops_without_vault_request(self):
        result, calls = self.run_cd(
            '_cd "" && _cd . && printf "PWD:%s\\n" "$current_pwd"',
            initial="/kv1/team/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PWD:/kv1/team/\n")
        self.assertEqual(calls, [])


    def test_path_resolver_root_relative_and_unsafe_segments(self):
        commands = r'''
current_pwd="/kv1/team/"
_vcli_resolve_path result "/" && printf 'ROOT:%s\n' "$result"
_vcli_resolve_path result "nested//deep///" && printf 'NESTED:%s\n' "$result"
_vcli_resolve_path result "/kv1//other//" && printf 'ABS:%s\n' "$result"
if _vcli_resolve_path result "/kv1/team/../secret"; then exit 92; fi
if _vcli_resolve_path result "/kv1/team/./secret"; then exit 93; fi
if _vcli_resolve_path result ""; then exit 94; fi
printf 'UNCHANGED:%s\n' "$result"
'''
        result, _ = self.run_cd(commands)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "ROOT:/\nNESTED:/kv1/team/nested/deep\n"
                         "ABS:/kv1/other\nUNCHANGED:/kv1/other\n")

if __name__ == "__main__":
    unittest.main()
