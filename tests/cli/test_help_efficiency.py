"""P-05h: help enumeration preserves CLI contract without needless subprocesses."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


def production_help():
    source = VCLI.read_text(encoding="utf-8")
    match = re.search(r"^function commands_help\(\)\{.*?^\}", source, re.M | re.S)
    if not match:
        raise AssertionError("Production commands_help not found")
    return match.group(0)


class HelpEfficiencyContracts(unittest.TestCase):
    def exercise(self, argument):
        with tempfile.TemporaryDirectory(prefix="vcli-help-contract-") as folder:
            orderlog = Path(folder) / "sort.count"
            script = production_help() + r'''
declare -A vcli_commands=([zeta]=arg [cat]=arg [help]=arg [exit]=noarg)
declare -A vcli_commands_help=(
    [zeta]='zeta \n  last'
    [cat]='cat path \n  read'
    [help]='help \n  show'
    [exit]='exit \n  quit'
)
IFS=';:'
tr(){ echo 'unexpected tr' >&2; return 89; }
sort(){
    printf 'call\n' >> "$ORDERLOG"
    command sort "$@"
}
commands_help "$ARG"
result=$?
[[ $IFS == ';:' ]] || exit 92
exit "$result"
'''
            env = os.environ.copy()
            env.update({"ARG": argument, "ORDERLOG": str(orderlog)})
            out = subprocess.run(["bash", "-c", script], capture_output=True,
                                 text=True, timeout=8, env=env)
            count = orderlog.read_text().count("call\n") if orderlog.exists() else 0
            return out, count

    def test_full_help_sorted_once_without_tr_or_ifs_changes(self):
        result, sorts = self.exercise("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorts, 1, "list sorting must happen exactly once")
        self.assertNotIn("unexpected tr", result.stderr)
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith(("cat:", "exit:", "help:", "zeta:"))]
        self.assertEqual(lines, ["cat: cat path ", "exit: exit ",
                                 "help: help ", "zeta: zeta "])
        self.assertIn("  read", result.stdout)
        self.assertIn("  quit", result.stdout)

    def test_known_help_is_direct_lookup_without_sort(self):
        result, sorts = self.exercise("cat")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorts, 0)
        self.assertEqual(result.stdout, "cat path \n  read\n")

    def test_unknown_help_has_sorted_suggestions_and_failure_code(self):
        result, sorts = self.exercise("missing")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(sorts, 1)
        self.assertEqual(result.stdout.splitlines()[1:],
                         ["   cat", "   exit", "   help", "   zeta"])
        self.assertIn("unkown 'missing' vcli command", result.stdout)

    def test_help_does_not_override_global_ifs(self):
        self.assertNotIn("IFS=", production_help())
        self.assertNotIn("backIFS", production_help())


if __name__ == "__main__":
    unittest.main()
