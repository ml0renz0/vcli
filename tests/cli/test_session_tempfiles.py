"""Lifecycle contracts for private CLI scratch files and catchable signals.

Only VCLI-owned session directories may be removed. User artifacts in TMPDIR
must survive normal exit and termination. SIGKILL is not recoverable.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

def shell_functions(*names):
    text = SOURCE.read_text(encoding="utf-8")
    source = []
    for name in names:
        match = re.search(
            r"^function " + re.escape(name) + r"\(\)\{.*?^\}",
            text, re.MULTILINE | re.DOTALL,
        )
        if not match:
            raise AssertionError(f"Missing Bash helper {name}")
        source.append(match.group(0))
    return "\n".join(source)

class SessionTempLifecycleTests(unittest.TestCase):
    def test_owner_cleanup_on_exit_and_catchable_signals(self):
        helpers = shell_functions("vcli_session_cleanup", "vcli_session_open")
        for mode,code in [("exit",0), ("TERM",143), ("INT",130)]:
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(prefix="vcli-session-cleanup-") as home:
                    root = Path(home)
                    sentinel = root / "backup_vault_user-artifact.tgz"
                    sentinel.write_bytes(b"preserve")
                    script = helpers + r'''
TMP="$TEST_TMP"
VCLI_SESSION_TMP=""
VCLI_SESSION_PID=""
umask 077
vcli_session_open || exit 9
[[ -d "$VCLI_SESSION_TMP" ]] || exit 10
[[ $(stat -c '%a' "$VCLI_SESSION_TMP") == 700 ]] || exit 11
printf 'synthetic-secret\n' > "$VCLI_SESSION_TMP/.put.fixture"
printf 'synthetic-auth\n' > "$VCLI_SESSION_TMP/.vcli.auth.fixture"
if [[ "$MODE" == exit ]]; then
  exit 0
else
  kill -s "$MODE" "$BASHPID"
  exit 99
fi
'''
                    env = os.environ.copy()
                    env.update({"TEST_TMP":home, "MODE":mode})
                    result = subprocess.run(
                        ["bash","-c",script], env=env,
                        text=True, capture_output=True, timeout=10,
                    )
                    self.assertEqual(result.returncode,code,result.stderr)
                    self.assertEqual(sentinel.read_bytes(),b"preserve")
                    self.assertEqual(list(root.glob(".vcli-session.*")),[])

    def test_subshell_cannot_clean_parent_session_directory(self):
        helpers = shell_functions("vcli_session_cleanup", "vcli_session_open")
        script = helpers + r'''
TMP="$TEST_TMP"
VCLI_SESSION_TMP=""
VCLI_SESSION_PID=""
umask 077
vcli_session_open || exit 2
( vcli_session_cleanup )
[[ -d "$VCLI_SESSION_TMP" ]] || exit 3
exit 0
'''
        with tempfile.TemporaryDirectory(prefix="vcli-session-owner-") as home:
            env = os.environ.copy()
            env["TEST_TMP"] = home
            result = subprocess.run(
                ["bash","-c",script], env=env,
                capture_output=True,text=True,timeout=10,
            )
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(list(Path(home).glob(".vcli-session.*")))

    def test_request_payload_is_placed_in_private_session(self):
        helpers = shell_functions("vcli_session_cleanup","vcli_session_open","_put")
        script = helpers + r'''
TMP="$TEST_TMP"
VCLI_SESSION_TMP=""
VCLI_SESSION_PID=""
umask 077
current_pwd="/"
debug(){ :; }
vcli_is_kv2_path(){ return 1; }
_curl(){
  printf '%s\n' "$2" > "$TEST_TMP/created-path.txt"
  [[ "$2" == "$VCLI_SESSION_TMP/"* ]] || return 88
  kill -s TERM "$BASHPID"
}
vcli_session_open || exit 2
_put /kv1/test '{"secret":"synthetic"}'
exit 99
'''
        with tempfile.TemporaryDirectory(prefix="vcli-session-put-term-") as home:
            env = os.environ.copy()
            env["TEST_TMP"] = home
            result = subprocess.run(["bash","-c",script],env=env,
                                    capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,143,result.stderr)
            self.assertEqual(list(Path(home).glob(".vcli-session.*")),[])
            path = (Path(home)/"created-path.txt").read_text().strip()
            self.assertIn(".vcli-session.",path)
            self.assertFalse(Path(path).exists())

if __name__ == "__main__":
    unittest.main()
