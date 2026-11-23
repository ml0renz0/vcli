"""Isolated Bash contracts for shared KV2 version selection and logical paths."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


def production(name):
    text = SOURCE.read_text(encoding="utf-8")
    block = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}",
                      text, re.M | re.S)
    if block is None:
        raise AssertionError(f"Missing Bash function {name}")
    return block.group(0)


class Kv2VersionValidationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = "\n".join((
            production("_vcli_resolve_path"),
            production("vcli_kv2_prepare_versions"),
            r"""
current_pwd="/"
vcli_is_kv2_path(){
    [[ $1 == /kv2/* || $1 == /team/config/* ]]
}
vcli_kv2_metadata_path(){
    [[ ${BLOCK_METADATA:-no} != yes ]] || return 19
    case $1 in
      /kv2/*) printf '/kv2/metadata/%s\n' "${1#/kv2/}" ;;
      /team/config/*) printf '/team/config/metadata/%s\n' "${1#/team/config/}" ;;
      *) return 19 ;;
    esac
}
selected="" path="" metadata=""
vcli_kv2_prepare_versions "$OP" "$CSV" "$TARGET" selected path metadata || exit $?
printf 'JSON=%s\nPATH=%s\nMETA=%s\n' "$selected" "$path" "$metadata"
""",
        ))

    def run_case(self, op="undelete", csv="1,3", target="kv2/data/item",
                 block_metadata=False):
        env = os.environ.copy()
        env.update({"OP": op, "CSV": csv, "TARGET": target,
                    "BLOCK_METADATA": "yes" if block_metadata else "no"})
        return subprocess.run(["bash", "-c", self.script], env=env,
                              text=True, capture_output=True, timeout=8)

    def test_both_commands_resolve_literal_data_and_nested_mount(self):
        for operation in ("undelete", "destroy"):
            for path, meta in (
                ("kv2/data/item", "/kv2/metadata/data/item"),
                ("team/config/metadata/secret", "/team/config/metadata/metadata/secret"),
            ):
                with self.subTest(operation=operation, path=path):
                    result = self.run_case(operation, "1,3", path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, (
                        f"JSON=[1,3]\nPATH=/{path}\nMETA={meta}\n"
                    ))

    def test_exact_json_number_limit_and_repeated_versions(self):
        result = self.run_case(csv="9007199254740991")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("JSON=[9007199254740991]", result.stdout)
        invalid = ("9007199254740992", "999999999999999999999",
                   "1,1", "0", "-1", "01", "1,01", "1,", ",1", "1,,2",
                   "1.0", "1,2;echo", "1 2")
        for operation in ("undelete", "destroy"):
            for csv in invalid:
                with self.subTest(operation=operation, csv=csv):
                    rejected = self.run_case(operation, csv)
                    self.assertEqual(rejected.returncode, 2, rejected.stderr)
                    self.assertEqual(rejected.stdout, "")

    def test_bad_logical_path_and_metadata_failure(self):
        for target in ("kv1/s", "kv2", "kv2/data/../x", "kv2/a/./b",
                       "kv2/a?version=1", "kv2/a#fragment", ""):
            with self.subTest(target=target):
                result = self.run_case(target=target)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        result = self.run_case(block_metadata=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_lifecycle_commands_share_validation_without_dropping_guards(self):
        undelete = production("_undelete")
        destroy = production("_destroy")
        self.assertIn('vcli_kv2_prepare_versions undelete "$2" "$3" wanted path metadata',
                      undelete)
        self.assertIn('vcli_kv2_prepare_versions destroy "$csv" "$requested" wanted path metadata',
                      destroy)
        self.assertIn("each version must exist", undelete)
        self.assertIn("every version must exist", destroy)
        self.assertIn("Type DESTROY", destroy)
        self.assertIn("verification failed", undelete)
        self.assertIn("verification failed", destroy)


if __name__ == "__main__":
    unittest.main()
