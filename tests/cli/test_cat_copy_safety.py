"""04-Q FINAL: protect secret reads and copies against stale flags and errors."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


def production(*names):
    text = VCLI.read_text(encoding="utf-8")
    parts = []
    for name in names:
        block = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}",
                          text, re.M | re.S)
        if not block:
            raise AssertionError(f"Missing production Bash function {name}")
        parts.append(block.group(0))
    return "\n".join(parts)


class CatFlagsContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = production("_cat")

    def run_case(self, command):
        with tempfile.TemporaryDirectory(prefix="vcli-cat-isolation-") as tmp:
            trace = Path(tmp) / "trace"
            env = os.environ.copy()
            env["TRACE"] = str(trace)
            stub = r'''
current_pwd=""
debug(){ :; }
_curl(){
    printf '<%s>' "$@" >> "$TRACE"
    printf '\n' >> "$TRACE"
    local arg
    for arg in "$@"; do
        if [[ $arg == /kv1/denied ]]; then
            [[ ${1:-} == -_disable_-f_- ]] && return 0
            return 22
        fi
    done
}
'''
            result = subprocess.run(
                ["bash", "-c", self.cat + "\n" + stub + "\n" + command],
                env=env, text=True, capture_output=True, timeout=8,
            )
            calls = trace.read_text().splitlines() if trace.exists() else []
            return result, calls

    def test_health_probe_flag_does_not_leak_into_subsequent_read(self):
        result, calls = self.run_case(r'''
_cat -_disable_-f_- sys/health || exit 90
current_pwd="/kv1/"
if _cat denied; then exit 91; fi
_cat allowed || exit 92
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            "<-_disable_-f_-><sys/health><>",
            "</kv1/denied><>",
            "</kv1/allowed><>",
        ])

    def test_missing_argument_fails_before_http_even_after_flags(self):
        for command in ("_cat", "_cat -i", "_cat -_disable_-f_-"):
            with self.subTest(command=command):
                result, calls = self.run_case(command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing secret path", result.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(result.stdout, "")

    def test_internal_flags_forwarded_only_for_their_invocation(self):
        result, calls = self.run_case(r'''
_cat -i -_disable_-f_- /sys/health || exit 90
_cat /kv1/allowed
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            "<-i><-_disable_-f_-></sys/health><>",
            "</kv1/allowed><>",
        ])


class CopyFailClosedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.copy = production("_cp")

    def run_copy(self, mode, destination="/kv1/copied"):
        with tempfile.TemporaryDirectory(prefix="vcli-cp-guard-") as tmp:
            trace = Path(tmp) / "writes"
            reads = Path(tmp) / "reads"
            env = os.environ.copy()
            env.update({"MODE": mode, "WRITES": str(trace), "READS": str(reads),
                        "DEST": destination})
            stub = r'''
current_pwd=""
debug(){ :; }
_vcli_absolute_path(){
    [[ -n ${1:-} ]] || return 1
    printf '/%s\n' "${1#/}"
}
_cat(){
    printf '%s\n' "$1" >> "$READS"
    case $MODE in
        read-failed) printf '{"data":{"partial":1}}\n'; return 22 ;;
        invalid-json) printf 'not-json\n' ;;
        wrong-schema) printf '{"data":[1]}\n' ;;
        ok) printf '{"data":{"value":1}}\n' ;;
    esac
}
vcli_is_kv2_path(){ [[ $1 == /kv2/* ]]; }
_put(){
    printf '%s|%s|%s\n' "$1" "$2" "${3:-no-cas}" >> "$WRITES"
}
_cp /kv1/source "$DEST"
'''
            result = subprocess.run(
                ["bash", "-c", self.copy + "\n" + stub],
                env=env, capture_output=True, text=True, timeout=8,
            )
            writes = trace.read_text().splitlines() if trace.exists() else []
            read_paths = reads.read_text().splitlines() if reads.exists() else []
            return result, read_paths, writes

    def test_failed_or_invalid_read_never_writes_destination(self):
        for mode in ("read-failed", "invalid-json", "wrong-schema"):
            with self.subTest(mode=mode):
                result, reads, writes = self.run_copy(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(reads, ["/kv1/source"])
                self.assertEqual(writes, [])
                self.assertIn("vcli:", result.stderr)

    def test_successful_copy_preserves_payload_and_kv2_create_only_cas(self):
        for destination, expected in (
            ("/kv1/copied", "/kv1/copied|{\"value\":1}|no-cas"),
            ("/kv2/copied", "/kv2/copied|{\"value\":1}|0"),
        ):
            with self.subTest(destination=destination):
                result, reads, writes = self.run_copy("ok", destination)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(reads, ["/kv1/source"])
                self.assertEqual(writes, [expected])


class FunctionInventoryContracts(unittest.TestCase):
    def test_named_bash_functions_have_no_duplicate_definitions(self):
        text = VCLI.read_text(encoding="utf-8")
        declared = re.findall(r"^function ([a-zA-Z_][a-zA-Z_0-9]*)\(\)\{", text, re.M)
        self.assertEqual(len(declared), len(set(declared)))


if __name__ == "__main__":
    unittest.main()
