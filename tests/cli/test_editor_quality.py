"""Production Bash regressions for TAB, history, colors and word editing."""
from pathlib import Path
import re
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"
def fn(name):
    src = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}", src, re.M | re.S)
    if not match: raise AssertionError("Missing " + name)
    return match.group(0)
def exercise(code):
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=8)

class EditorQuality(unittest.TestCase):
    def test_completion_spacing_and_help(self):
        script = fn("completion") + """
declare -A vcli_commands=([ls]=arg [tree]=arg [help]=arg [history]=noarg)
debug(){ :; }
prompt(){ :; }
_ls(){ printf "cubbyhole/\\nkv1/\\n"; }
CYAN=""; NONE=""
cmd="ls cub"; completion "$cmd"; printf "ONE=%s\\n" "$cmd"
cmd="help tr"; completion "$cmd"; printf "HELP=%s\\n" "$cmd"
"""
        result = exercise(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ONE=ls cubbyhole/", result.stdout)
        self.assertNotIn("ls  cubbyhole/", result.stdout)
        self.assertIn("HELP=help tree", result.stdout)

    def test_editor_word_offsets(self):
        helpers = "\n".join(fn(name) for name in (
            "vcli_word_left_offset", "vcli_word_right_offset",
            "vcli_move_word_left", "vcli_move_word_right",
            "vcli_delete_previous_word"))
        script = helpers + """
vcli_tput(){ :; }
cmd="getallcertexpiration kv1/saas/tenant-001 -oo"
len=${#cmd}; pos=$((len+1))
vcli_move_word_left
printf "LEFT=%s\\n" "${cmd:pos-1}"
vcli_move_word_right
vcli_delete_previous_word
printf "AFTER=%s\\n" "$cmd"
"""
        result = exercise(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEFT=-oo", result.stdout)
        self.assertIn("AFTER=getallcertexpiration kv1/saas/tenant-001 ", result.stdout)

    def test_last_argument_from_most_recent_history(self):
        script = fn("get_lastcmd_arg") + """
cmd_history=("getallcertlist kv1/saas/tenant-001" "ls cubbyhole")
get_lastcmd_arg
"""
        result = exercise(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "kv1/saas/tenant-001\n")

    def test_colors_and_verbose_are_enabled(self):
        render = fn("vcli_cert_expiration_from_data")
        for color in ("tone=$RED", "tone=$GREEN", "tone=$YELLOW"):
            self.assertIn(color, render)
        self.assertIn("if [[ $VERBOSE == yes ]]; then", fn("_curl"))
        self.assertIn("add_cmd \"$cmd $arg1 $arg2 $arg3\"", fn("run_cmd"))

if __name__ == "__main__": unittest.main()
