"""Reproduce unsafe log destinations and history rename races in Bash."""
from __future__ import annotations
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

def functions(*names):
    source = SOURCE.read_text(encoding="utf-8")
    found = []
    for name in names:
        match = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}", source, re.M | re.S)
        if not match:
            raise AssertionError(f"Missing function {name}")
        found.append(match.group(0))
    return "\n".join(found)

class DebugLogSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = functions("vcli_stat_identity", "vcli_link_exclusive", "vcli_debug_open", "vcli_debug_close", "debug")

    def case(self, mode):
        with tempfile.TemporaryDirectory(prefix="vcli-debug-fd-") as tmp:
            root = Path(tmp)
            log = root / "log with spaces"
            victim = root / "protected"
            original = root / "original"
            victim.write_bytes(b"not overwritten")
            if mode == "symlink":
                log.symlink_to(victim)
            elif mode == "public":
                log.write_text("previous\n")
                log.chmod(0o644)
            elif mode == "hardlink":
                os.link(victim, log)
            elif mode == "existing":
                log.write_text("previous\n")
                log.chmod(0o600)
            shell = r'''
DEBUG=yes
DEBUGFILE="$LOG"
VCLI_DEBUG_FD=""
if [[ "$MODE" == swapped ]]; then
  vcli_debug_open || exit 88
  mv -- "$DEBUGFILE" "$ORIGINAL"
  ln -s -- "$VICTIM" "$DEBUGFILE"
fi
debug "new entry"
status=$?
vcli_debug_close
exit "$status"
'''
            env = os.environ.copy()
            env.update({"LOG": str(log), "VICTIM": str(victim),
                        "ORIGINAL": str(original), "MODE": mode})
            out = subprocess.run(["bash", "-c", self.code + shell], env=env,
                                 text=True, capture_output=True, timeout=10)
            body = log.read_bytes() if log.exists() and not log.is_symlink() else None
            old = original.read_bytes() if original.exists() else None
            mode_bits = stat.S_IMODE(log.stat().st_mode) if log.exists() and not log.is_symlink() else None
            return out, body, old, victim.read_bytes(), mode_bits

    def test_private_creation_and_existing_append(self):
        for mode, content in (("new", b"new entry\n"),
                              ("existing", b"previous\nnew entry\n")):
            with self.subTest(mode=mode):
                out, body, _, victim, perms = self.case(mode)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(body, content)
                self.assertEqual(perms, 0o600)
                self.assertEqual(victim, b"not overwritten")

    def test_reject_symlink_hardlink_and_public_log(self):
        for mode in ("symlink", "hardlink", "public"):
            with self.subTest(mode=mode):
                out, _, _, victim, _ = self.case(mode)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn("refusing", out.stderr)
                self.assertEqual(victim, b"not overwritten")

    def test_path_swapped_after_open_does_not_redirect_writes(self):
        out, _, original, victim, _ = self.case("swapped")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(original, b"new entry\n")
        self.assertEqual(victim, b"not overwritten")

class HistoryPathSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = functions("vcli_stat_identity", "vcli_replace_exact", "save_history", "load_history")

    def test_raced_directory_cannot_capture_history(self):
        with tempfile.TemporaryDirectory(prefix="vcli-history-race-") as tmp:
            script = r'''
VCLISTORY="$TEST_HOME/.vclistory"
cmd_history=("help" "history")
# Inject a destination-directory race just before production rename.
eval "$(declare -f vcli_replace_exact | sed '1s/vcli_replace_exact/vcli_replace_exact_original/')"
vcli_replace_exact(){
  mkdir -p -- "$VCLISTORY"
  vcli_replace_exact_original "$@"
}
save_history
'''
            env = os.environ.copy()
            env["TEST_HOME"] = tmp
            out = subprocess.run(["bash", "-c", self.code + script],
                                 env=env, text=True, capture_output=True, timeout=10)
            destination = Path(tmp) / ".vclistory"
            self.assertNotEqual(out.returncode, 0)
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])
            self.assertEqual(list(Path(tmp).glob(".vclistory.*")), [])

    def test_existing_history_symlink_is_not_loaded_or_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="vcli-history-link-") as tmp:
            root = Path(tmp)
            victim = root / "sensitive"
            victim.write_bytes(b"do not read or overwrite")
            (root / ".vclistory").symlink_to(victim)
            script = r'''
VCLISTORY="$TEST_HOME/.vclistory"
cmd_history=()
load_history
[[ @{#cmd_history[@]} == 0 ]] || exit 90
save_history
'''.replace("@{", chr(36) + "{")
            env = os.environ.copy()
            env["TEST_HOME"] = tmp
            out = subprocess.run(["bash", "-c", self.code + script],
                                 env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(out.returncode, 0)
            self.assertEqual(victim.read_bytes(), b"do not read or overwrite")
            self.assertTrue((root / ".vclistory").is_symlink())

if __name__ == "__main__":
    unittest.main()
