"""Linux PTY smoke tests for VCLI's legacy interactive key-reader.

Not a replacement for the planned full keyboard regression matrix.
"""
from __future__ import annotations

import os
import sys
import fcntl
import termios
import tty
from pathlib import Path
import pty
import signal
import select
import subprocess
import tempfile
import time
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


def _vcli_tty_preexec():
    """Attach the child session to its PTY, as a real interactive terminal.

    Darwin requires TIOCSCTTY for the newly created session; merely passing
    the slave as stdio and calling setsid is insufficient. Without this,
    Bash read -s/-N may fail tcsetattr and leak control keys as text.
    """
    if sys.platform == "darwin":
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)



def _vcli_prepare_pty(slave):
    """Deliver control bytes to Bash's own key reader, not Darwin's line editor.

    In canonical mode macOS intercepts Ctrl+D as an EOF rather than sending
    0x04. The CLI's read -N1 must receive that byte, as on Linux. Preserve
    the existing controlling-terminal setup and all behavioral assertions.
    """
    if sys.platform == "darwin":
        tty.setraw(slave, when=termios.TCSANOW)


def read_until(fd, marker: bytes, timeout=8) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], min(0.2, deadline-time.monotonic()))
        if not readable:
            continue
        try:
            chunk = os.read(fd, 8192)
        except OSError:
            break
        if not chunk:
            break
        data.extend(chunk)
        if marker in data:
            return bytes(data)
    raise AssertionError(f"Expected {marker!r} in PTY output: {bytes(data)[-1500:]!r}")


def wait_pty_exit(fd, child, timeout=8):
    """Consume pending PTY output while waiting for the CLI to exit.

    On Darwin the PTY output buffer can fill after an early output marker,
    blocking vcli's writer before it gets back to read Ctrl+D. This still
    requires a clean exit with status 0; it does not weaken the assertion.
    """
    deadline = time.monotonic() + timeout
    output = bytearray()
    while child.poll() is None and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([fd], [], [], min(0.15, remaining))
        if readable:
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
    if child.poll() is None:
        raise AssertionError(
            f"vcli did not exit after Ctrl+D; PTY tail={bytes(output)[-1000:]!r}"
        )
    return child.wait(timeout=1)


class InteractiveShellTests(unittest.TestCase):
    def test_interactive_help_and_ctrl_d(self):
        with tempfile.TemporaryDirectory(prefix="vcli-ci-repl-") as home:
            env = os.environ.copy()
            env.update({
                "HOME": home,
                "TERM": "xterm",
                "TMPDIR": home,
                "VAULT_ADDR": os.environ["VAULT_ADDR"],
                "VAULT_CACERT": os.environ["VAULT_CACERT"],
                "VAULT_TOKEN": os.environ.get("VCLI_TEST_READONLY_TOKEN", ""),
            })
            master, slave = pty.openpty()
            _vcli_prepare_pty(slave)
            child = subprocess.Popen([str(VCLI)], stdin=slave, stdout=slave,
                                     stderr=slave, env=env, start_new_session=True,
                                     preexec_fn=_vcli_tty_preexec)
            os.close(slave)
            try:
                read_until(master, b" >", timeout=10)
                for key in b"help\n":
                    os.write(master, bytes([key]))
                    time.sleep(0.015)
                self.expect_help(master)
                # Wait for the REPL to finish rendering help before Ctrl+D.
                time.sleep(0.5)
                os.write(master, b"\x04")  # Ctrl+D
                self.assertEqual(wait_pty_exit(master, child, timeout=8), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                os.close(master)


    def session(self, history: str | None = None, files: dict[str, str] | None = None,
                term: str | None = "xterm", extra_env: dict[str, str] | None = None):
        """Return PTY process with its own HOME and real Vault TLS credentials."""
        home = tempfile.TemporaryDirectory(prefix="vcli-repl-matrix-")
        env = os.environ.copy()
        env.update({
            "HOME": home.name, "TMPDIR": home.name,
            "VAULT_ADDR": os.environ["VAULT_ADDR"],
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
            "VAULT_TOKEN": os.environ.get("VCLI_TEST_READONLY_TOKEN", ""),
        })
        if term is None:
            env.pop("TERM", None)
        else:
            env["TERM"] = term
        if extra_env:
            env.update(extra_env)
        if history is not None:
            (Path(home.name) / ".vclistory").write_text(history, encoding="utf-8")
        for name, content in (files or {}).items():
            (Path(home.name) / name).write_text(content, encoding="utf-8")
        master, slave = pty.openpty()
        _vcli_prepare_pty(slave)
        child = subprocess.Popen([str(VCLI)], stdin=slave, stdout=slave,
                                 stderr=slave, env=env, cwd=home.name,
                                 start_new_session=True,
                                     preexec_fn=_vcli_tty_preexec)
        os.close(slave)
        read_until(master, b" >", timeout=12)
        return home, master, child

    def send(self, fd, data, pace=0.012):
        for b in data:
            os.write(fd, bytes([b]))
            if pace:
                time.sleep(pace)

    def end(self, home, fd, child):
        """Reap a dedicated PTY session even if its group signal races.

        Kill the private process group, then signal the direct child as a
        fallback. Closing the master also releases blocked terminal I/O.
        """
        try:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if child.poll() is None:
                    child.kill()
        finally:
            os.close(fd)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            finally:
                home.cleanup()

    def expect_help(self, fd, timeout=8):
        """Require a successful 'help', not the unknown-command error header."""
        out = read_until(fd, b"Available commands:", timeout=timeout)
        self.assertIn(
            b"Available commands:", out.splitlines(),
            f"REPL did not execute help successfully: {out[-900:]!r}",
        )
        return out

    def test_02_tab_completes_unique_command(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"hel\t\n")
            result = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", result)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_03_up_arrow_replays_history_command(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"help\n")
            self.expect_help(fd, timeout=8)
            time.sleep(0.35)
            self.send(fd, b"\x1b[A\n", pace=0.04)
            again = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", again)
        finally:
            self.end(home, fd, child)

    def test_04_cursor_home_right_and_insert(self):
        home, fd, child = self.session()
        try:
            # "hlp" + Home + Right + "e" -> "help"
            self.send(fd, b"hlp\x1b[H\x1b[Ce\n", pace=0.045)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_05_ctrl_c_discards_partial_input_and_keeps_session(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"not-a-valid-command!")
            # The editor inserts a character at a time and emits cursor
            # controls between them; a contiguous echoed command is not valid.
            # The unique final '!' proves the full input has been consumed.
            read_until(fd, b"!", timeout=8)
            # The echo precedes the editor's final bookkeeping. On macOS,
            # signaling only the Bash PID at this point can race a foreground
            # tput subprocess rather than emulate a terminal Ctrl+C.
            # Let the editor finish this key, then signal its PTY process
            # group as a real terminal would (not merely the Bash PID).
            time.sleep(0.15)
            self.assertIsNone(child.poll(), "REPL exited before Ctrl+C")
            os.killpg(child.pid, signal.SIGINT)
            read_until(fd, b" >", timeout=8)
            self.assertIsNone(child.poll(), "Ctrl+C must not terminate the session")
            self.send(fd, b"help\n")
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_06_ctrl_r_no_match_does_not_kill_repl(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"zzzz-no-match\x12\x12", pace=0.03)
            time.sleep(0.25)
            self.assertIsNone(child.poll(), "Empty reverse-search must not terminate REPL")
            self.send(fd, b"\x15help\n")
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_07_down_restores_unsent_draft_without_persisting_it(self):
        home, fd, child = self.session(history="help\n")
        try:
            self.send(fd, b"his\x1b[A\x1b[Btory\n", pace=0.04)
            out = read_until(fd, b"#1: help", timeout=8)
            self.assertIn(b"#1: help", out)
            self.assertIsNone(child.poll())
            self.send(fd, b"\x04")
            self.assertEqual(wait_pty_exit(fd, child, timeout=8), 0)
            self.assertNotIn("his\n", (Path(home.name) / ".vclistory").read_text())
        finally:
            self.end(home, fd, child)

    def test_08_literal_tilde_is_inserted_not_forward_delete(self):
        home, fd, child = self.session()
        try:
            # "help" + Left + "~" + Backspace -> "help", not "he"
            self.send(fd, b"help\x1b[D~\x7f\n", pace=0.04)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_09_csi_delete_removes_character_at_cursor(self):
        home, fd, child = self.session()
        try:
            # "helxp" + Home + Right*3 + Delete -> "help"
            self.send(fd, b"helxp\x1b[H\x1b[C\x1b[C\x1b[C\x1b[3~\n",
                      pace=0.04)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_10_command_name_colliding_with_file_does_not_load_it_as_history(self):
        home, fd, child = self.session(files={"help": "untrusted-line\n"})
        try:
            self.send(fd, b"help\n")
            self.expect_help(fd, timeout=8)
            self.send(fd, b"history\n")
            output = read_until(fd, b"#1: help", timeout=8)
            self.assertIn(b"#1: help", output)
            self.assertNotIn(b"untrusted-line", output)
        finally:
            self.end(home, fd, child)

    def test_11_repeated_help_deduplicates_persistent_history(self):
        home, fd, child = self.session()
        try:
            for _ in range(2):
                self.send(fd, b"help\n")
                self.expect_help(fd, timeout=8)
                # The help header precedes several screens of text. Do not
                # send Ctrl+D until the REPL has printed its next prompt.
                read_until(fd, b" >", timeout=8)
            # The prompt is printed just before Bash enters its one-byte read.
            # On a canonical PTY, an early Ctrl+D can be consumed as EOF.
            time.sleep(0.35)
            self.send(fd, b"\x04")
            self.assertEqual(wait_pty_exit(fd, child, timeout=8), 0)
            saved = (Path(home.name) / ".vclistory").read_text(encoding="utf-8")
            self.assertEqual(saved.splitlines().count("help"), 1, saved)
        finally:
            self.end(home, fd, child)

    def test_12_ctrl_r_matches_literal_brackets_and_cycles_through_history(self):
        home, fd, child = self.session(history="help [one]\nhelp [two]\n")
        try:
            self.send(fd, b"help [\x12", pace=0.04)
            first = read_until(fd, b"help [two]", timeout=8)
            self.assertIn(b"help [two]", first)
            self.send(fd, b"\x12")
            second = read_until(fd, b"help [one]", timeout=8)
            self.assertIn(b"help [one]", second)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_13_history_symlink_does_not_overwrite_target(self):
        with tempfile.TemporaryDirectory(prefix="vcli-history-target-") as outside:
            protected = Path(outside) / "important.txt"
            protected.write_text("do-not-touch\n", encoding="utf-8")
            home, fd, child = self.session()
            try:
                history = Path(home.name) / ".vclistory"
                history.symlink_to(protected)
                self.send(fd, b"help\n", pace=0.03)
                self.expect_help(fd, timeout=8)
                # Help renders multiple lines; give the REPL time to read Ctrl+D.
                time.sleep(0.5)
                self.send(fd, b"\x04")
                self.assertEqual(wait_pty_exit(fd, child, timeout=8), 0)
                self.assertEqual(protected.read_text(), "do-not-touch\n")
                self.assertTrue(history.is_symlink())
            finally:
                self.end(home, fd, child)

    def test_14_history_rewrite_is_atomic_and_private(self):
        home, fd, child = self.session(history="help\n")
        try:
            self.send(fd, b"history\n", pace=0.03)
            read_until(fd, b"#1: help", timeout=8)
            self.send(fd, b"\x04")
            self.assertEqual(wait_pty_exit(fd, child, timeout=8), 0)
            history_file = Path(home.name) / ".vclistory"
            self.assertEqual(history_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(history_file.read_text().splitlines(), ["help", "history"])
            self.assertFalse(list(Path(home.name).glob(".vclistory.*")))
        finally:
            self.end(home, fd, child)

    def test_15_single_write_paste_is_not_truncated(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"help\n", pace=0)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
            self.send(fd, b"history\n", pace=0)
            out = read_until(fd, b"#1: help", timeout=8)
            self.assertIn(b"#1: help", out)
        finally:
            self.end(home, fd, child)

    def test_16_escape_before_normal_key_preserves_typed_character(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"\x1bhelp\n", pace=0)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_17_incomplete_csi_times_out_without_breaking_next_command(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"\x1b[", pace=0)
            time.sleep(0.35)
            self.assertIsNone(child.poll())
            self.send(fd, b"help\n", pace=0)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_18_modified_cursor_sequence_does_not_pollute_input(self):
        home, fd, child = self.session()
        try:
            # Ctrl+Left commonly emits CSI 1;5D. It must not type ";5D".
            self.send(fd, b"help\x1b[1;5D\n", pace=0.03)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_19_bracketed_paste_markers_do_not_pollute_command(self):
        home, fd, child = self.session()
        try:
            # A single-line bracketed paste sends CSI 200~ and CSI 201~.
            self.send(fd, b"\x1b[200~help\x1b[201~\n", pace=0.03)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_20_page_up_csi_is_not_interpreted_as_literal_tilde(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"help\x1b[5~\n", pace=0.03)
            out = self.expect_help(fd, timeout=8)
            self.assertIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_21_unknown_command_is_not_confused_with_successful_help(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"definitely-no-such-command\n", pace=0.02)
            out = read_until(fd, b"Available commands:", timeout=8)
            self.assertNotIn(b"Available commands:", out.splitlines(),
                             "Unknown command must not satisfy help contract")
            self.assertIn(b"vcli command. Available commands:", out)
        finally:
            self.end(home, fd, child)

    def test_22_ctrl_d_on_nonempty_buffer_does_not_exit(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"help\x04\n", pace=0.03)
            self.expect_help(fd, timeout=8)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_23_ctrl_d_deletes_character_under_cursor(self):
        home, fd, child = self.session()
        try:
            # "helxp" + Home + Right*3 + Ctrl+D -> "help".
            self.send(fd, b"helxp\x1b[H\x1b[C\x1b[C\x1b[C\x04\n", pace=0.04)
            self.expect_help(fd, timeout=8)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)


    def test_24_ss3_home_right_insert(self):
        home, fd, child = self.session()
        try:
            # Application cursor mode: "hlp" + SS3 Home + Right + "e" -> "help".
            self.send(fd, b"hlp\x1bOH\x1bOCe\n", pace=0.035)
            self.expect_help(fd)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_25_ss3_home_end_append(self):
        home, fd, child = self.session()
        try:
            # Moving Home and then End must leave insertion at the end.
            self.send(fd, b"hel\x1bOH\x1bOFp\n", pace=0.035)
            self.expect_help(fd)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_26_ss3_left_edits_in_middle(self):
        home, fd, child = self.session()
        try:
            # "hep" + SS3 Left + "l" inserts before p -> "help".
            self.send(fd, b"hep\x1bODl\n", pace=0.035)
            self.expect_help(fd)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_27_ss3_function_key_does_not_pollute_command(self):
        home, fd, child = self.session()
        try:
            # Application-mode F1 is ESC O P, not literal "OP".
            self.send(fd, b"help\x1bOP\n", pace=0.035)
            self.expect_help(fd)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_28_ss3_history_navigation_restores_draft(self):
        home, fd, child = self.session(history="help\n")
        try:
            self.send(fd, b"his\x1bOA\x1bOBtory\n", pace=0.04)
            out = read_until(fd, b"#1: help", timeout=8)
            self.assertIn(b"#1: help", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_29_partial_ss3_times_out_without_sticking(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"\x1bO", pace=0)
            time.sleep(0.35)
            self.assertIsNone(child.poll())
            self.send(fd, b"help\n", pace=0)
            self.expect_help(fd)
        finally:
            self.end(home, fd, child)


    def test_30_ctrl_u_discards_history_navigation_state(self):
        home, fd, child = self.session(history="help\nhistory\n")
        try:
            # Two Up keys -> older entry; Ctrl+U must reset history_index.
            # Otherwise Down restores "history" and prefixes "help" to it.
            self.send(fd, b"\x1b[A\x1b[A\x15\x1b[Bhelp\n", pace=0.04)
            self.expect_help(fd)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_31_ctrl_u_restarts_reverse_history_search(self):
        home, fd, child = self.session(history="help [one]\nhelp [two]\n")
        try:
            self.send(fd, b"help [\x12", pace=0.035)
            read_until(fd, b"help [two]", timeout=8)
            self.send(fd, b"\x15help [\x12", pace=0.035)
            # A fresh search selects the newest entry again, not the next hit.
            out = read_until(fd, b"help [two]", timeout=8)
            self.assertIn(b"help [two]", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_32_ctrl_c_restarts_reverse_history_search(self):
        home, fd, child = self.session(history="help [one]\nhelp [two]\n")
        try:
            self.send(fd, b"help [\x12", pace=0.035)
            read_until(fd, b"help [two]", timeout=8)
            os.kill(child.pid, signal.SIGINT)
            time.sleep(0.25)
            self.assertIsNone(child.poll())
            self.send(fd, b"help [\x12", pace=0.035)
            out = read_until(fd, b"help [two]", timeout=8)
            self.assertIn(b"help [two]", out)
        finally:
            self.end(home, fd, child)


    def test_33_multiline_bracketed_paste_does_not_execute_any_command(self):
        home, fd, child = self.session()
        try:
            # The paste contains a would-be Enter after "help". Neither command
            # may execute automatically while the terminal is still pasting.
            self.send(fd, b"\x1b[200~help\nhistory\x1b[201~", pace=0.02)
            out = read_until(fd, b"bracketed paste discarded", timeout=8)
            self.assertNotIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
            self.send(fd, b"help\n", pace=0.035)
            self.expect_help(fd)
        finally:
            self.end(home, fd, child)

    def test_34_ctrl_d_inside_bracketed_paste_cannot_exit_session(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"\x1b[200~\x04\x1b[201~", pace=0.035)
            out = read_until(fd, b"bracketed paste discarded", timeout=8)
            self.assertIn(b"bracketed paste discarded", out)
            self.assertIsNone(child.poll())
            self.send(fd, b"help\n", pace=0.035)
            self.expect_help(fd)
        finally:
            self.end(home, fd, child)


    def test_35_sigint_during_bracketed_paste_does_not_expose_commands(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"\x1b[200~help", pace=0.025)
            os.kill(child.pid, signal.SIGINT)
            time.sleep(0.2)
            self.assertIsNone(child.poll())
            # These bytes are still part of the paste: neither the newline
            # nor the remaining "history" may execute before CSI 201~.
            self.send(fd, b"help\nhistory\x1b[201~", pace=0.025)
            out = read_until(fd, b"bracketed paste discarded", timeout=8)
            self.assertNotIn(b"Available commands:", out)
            self.assertIsNone(child.poll())
            self.send(fd, b"help\n", pace=0.03)
            self.expect_help(fd)
        finally:
            self.end(home, fd, child)


    def _exercise_degraded_terminal(self, term):
        with tempfile.TemporaryDirectory(prefix="vcli-terminfo-hook-") as hook_dir:
            sentinel = Path(hook_dir) / "tput-called"
            shim = Path(hook_dir) / "tput"
            shim.write_text(
                "#!/bin/sh\nprintf called >> \"$VCLI_TPUT_SENTINEL\"\nexit 89\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
            env = {
                "PATH": hook_dir + os.pathsep + os.environ["PATH"],
                "VCLI_TPUT_SENTINEL": str(sentinel),
            }
            home, fd, child = self.session(term=term, extra_env=env)
            try:
                self.send(fd, b"help\n", pace=0.03)
                self.expect_help(fd)
                self.assertFalse(sentinel.exists(),
                                 "Basic input must not depend on tput")
                self.assertIsNone(child.poll())
            finally:
                self.end(home, fd, child)

    def test_36_dumb_term_basic_input_does_not_call_tput(self):
        self._exercise_degraded_terminal("dumb")

    def test_37_missing_term_basic_input_does_not_call_tput(self):
        self._exercise_degraded_terminal(None)

    def test_38_unknown_terminfo_entry_still_accepts_basic_input(self):
        home, fd, child = self.session(term="vcli-nonexistent-terminal")
        try:
            self.send(fd, b"help\n", pace=0.03)
            out = self.expect_help(fd)
            self.assertNotIn(b"tput:", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_39_ctrl_word_navigation_then_ctrl_w_deletes_only_last_word(self):
        home, fd, child = self.session()
        try:
            # Ctrl+Left, Ctrl+Right, Ctrl+W must restore a valid help command.
            self.send(fd, b"help garbage\x1b[1;5D\x1b[1;5C\x17\n", pace=0.03)
            self.expect_help(fd, timeout=8)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_40_alt_backspace_removes_word_without_exiting(self):
        home, fd, child = self.session()
        try:
            self.send(fd, b"help garbage\x1b\x7f\n", pace=0.03)
            self.expect_help(fd, timeout=8)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)


    def test_41_tab_path_completion_uses_one_separator(self):
        """ACC-03: type TAB on a PTY against Vault LIST, then execute it."""
        from uuid import uuid4
        from .test_vault import api
        prefix = "vcli-tab-" + uuid4().hex[:12]
        api("POST", "kv1/" + prefix + "/cubbyhole/leaf", {"fixture": True})
        home, fd, child = self.session()
        try:
            self.send(fd, ("ls kv1/" + prefix + "/cub").encode() + b"\t",
                      pace=0.025)
            completed = ("ls kv1/" + prefix + "/cubbyhole/").encode()
            out = read_until(fd, completed, timeout=10)
            self.assertIn(completed, out, "TAB did not complete the Vault directory")
            self.assertNotIn(b"ls  ", out, "TAB inserted an extra separator")
            self.send(fd, b"\n", pace=0.03)
            result = read_until(fd, b"leaf", timeout=10)
            self.assertIn(b"leaf", result, "Completed LIST did not execute")
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_42_help_completes_command_name_on_pty(self):
        """ACC-04: the completed help topic must actually render its help."""
        home, fd, child = self.session()
        try:
            self.send(fd, b"help tr\t\n", pace=0.035)
            out = read_until(fd, b"prints vault's secrets tree", timeout=10)
            self.assertIn(b"tree path/to/secret", out)
            self.assertNotIn(b"unkown 'tr'", out)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_43_history_bang_dollar_expands_preceding_vault_command(self):
        """ACC-05: real PTY + Vault LIST, and exact expanded history line."""
        from uuid import uuid4
        from .test_vault import api
        prefix = "vcli-lastarg-" + uuid4().hex[:12]
        api("POST", "kv1/" + prefix + "/branch/leaf", {"fixture": True})
        home, fd, child = self.session()
        try:
            path = "kv1/" + prefix + "/branch"
            self.send(fd, ("ls " + path + "\n").encode(), pace=0.025)
            self.assertIn(b"leaf", read_until(fd, b"leaf", timeout=10))
            time.sleep(0.15)
            self.send(fd, b"tree !$\n", pace=0.035)
            tree = read_until(fd, b"leaf", timeout=10)
            self.assertIn(b"leaf", tree)
            time.sleep(0.15)
            self.send(fd, b"history\n", pace=0.035)
            recorded = read_until(fd, ("#2: tree " + path).encode(), timeout=10)
            self.assertIn(("#2: tree " + path).encode(), recorded)
            self.assertNotIn(b"#2: tree !$", recorded)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_44_verbose_exposes_method_status_only_after_toggle(self):
        """ACC-07: observe real TLS requests, never tokens or secret values."""
        from uuid import uuid4
        from .test_vault import api
        prefix = "vcli-verbose-" + uuid4().hex[:12]
        api("POST", "kv1/" + prefix + "/leaf", {"canary": "private-canary"})
        home, fd, child = self.session()
        try:
            self.send(fd, b"verbose\n", pace=0.03)
            read_until(fd, b"verbose mode ON", timeout=8)
            self.send(fd, ("ls kv1/" + prefix + "\n").encode(), pace=0.025)
            result = read_until(fd, b"vcli: HTTP LIST -> 200", timeout=10)
            self.assertNotIn(b"private-canary", result)
            self.assertNotIn(os.environ["VCLI_TEST_READONLY_TOKEN"].encode(), result)
            self.send(fd, b"verbose\n", pace=0.03)
            read_until(fd, b"verbose mode OFF", timeout=8)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_45_explicit_api_path_read_and_list_in_real_pty(self):
        """The option works through the actual interactive Bash parser."""
        from uuid import uuid4
        from .test_vault import api
        prefix = "vcli-api-pty-" + uuid4().hex[:12]
        marker = "pty-raw-api-marker"
        api("POST", f"kv2/data/data/{prefix}/item", {"data": {"marker": marker}})
        home, fd, child = self.session(extra_env={
            "VAULT_KV_VERSION": "2", "VAULT_KV_MOUNTS": "kv2,team/config"
        })
        try:
            self.send(fd, (f"cat --api-path kv2/data/data/{prefix}/item\n").encode(),
                      pace=0.025)
            result = read_until(fd, marker.encode(), timeout=12)
            self.assertIn(marker.encode(), result)
            time.sleep(0.2)
            self.send(fd, (f"ls --api-path kv2/metadata/data/{prefix}\n").encode(),
                      pace=0.025)
            listing = read_until(fd, b"item", timeout=12)
            self.assertIn(b"item", listing)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_46_version_history_read_uses_interactive_parser(self):
        """Read an older version over PTY with the same logical KV2 path."""
        from uuid import uuid4
        from .test_vault import api
        prefix = "vcli-version-pty-" + uuid4().hex[:12]
        path = f"kv2/data/{prefix}/item"
        api("POST", f"kv2/data/data/{prefix}/item", {"data": {"fixture": "old-pty-snapshot"}})
        api("POST", f"kv2/data/data/{prefix}/item", {"data": {"fixture": "new-pty-snapshot"}})
        home, fd, child = self.session(extra_env={
            "VAULT_KV_VERSION": "2", "VAULT_KV_MOUNTS": "kv2,team/config"
        })
        try:
            self.send(fd, (f"cat --version 1 {path}\n").encode(), pace=0.025)
            result = read_until(fd, b"old-pty-snapshot", timeout=12)
            self.assertIn(b"old-pty-snapshot", result)
            self.assertNotIn(b"new-pty-snapshot", result)
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)

    def test_47_destroy_four_argument_command_in_real_pty(self):
        """Interactive IFS= must not collapse --versions N --yes PATH into one token."""
        from uuid import uuid4
        from .test_vault import api, ROOT_TOKEN
        name = "vcli-destroy-pty-" + uuid4().hex[:12]
        path = f"kv2/data/{name}/item"
        for generation in (1, 2):
            api("POST", f"kv2/data/data/{name}/item",
                {"data": {"generation": generation}})
        before = api("GET", f"kv2/metadata/data/{name}/item")["data"]
        self.assertEqual(before["current_version"], 2)
        home, fd, child = self.session(extra_env={
            "VAULT_TOKEN": ROOT_TOKEN,
            "VAULT_KV_VERSION": "2",
            "VAULT_KV_MOUNTS": "kv2,team/config",
        })
        try:
            self.send(fd, (f"destroy --versions 1 --yes {path}\n").encode(),
                      pace=0.015)
            rendered = read_until(fd, b"Permanently destroyed KV v2 version(s) 1",
                                  timeout=15)
            self.assertIn(path.encode(), rendered)
            self.assertNotIn(b"Usage: destroy", rendered)
            after = api("GET", f"kv2/metadata/data/{name}/item")["data"]
            self.assertEqual(after["current_version"], 2)
            self.assertTrue(after["versions"]["1"]["destroyed"])
            self.assertFalse(after["versions"]["2"]["destroyed"])
            self.assertEqual(api("GET", f"kv2/data/data/{name}/item?version=2")
                             ["data"]["data"], {"generation": 2})
            self.assertIsNone(child.poll())
        finally:
            self.end(home, fd, child)


    def test_48_acc09_certificate_scan_notice_with_real_vault_and_timing(self):
        """ACC-09: real TLS requests, real X509, PTY and a deliberately slow LIST.

        A PATH wrapper delays only one request, then execs the actual curl.
        It neither fabricates Vault responses nor changes the Bash CLI.
        """
        import shutil
        from uuid import uuid4
        from .test_vault import api, ROOT_TOKEN
        prefix = "vcli-acc09-" + uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix="vcli-acc09-fixtures-") as root:
            base = Path(root)
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048",
                 "-nodes", "-days", "2", "-sha256",
                 "-subj", "/CN=acc09.example.test",
                 "-keyout", str(base / "test.key"),
                 "-out", str(base / "test.crt")],
                check=True, capture_output=True, timeout=25,
            )
            payload = {"scan_crt": (base / "test.crt").read_text(),
                       "scan_key": (base / "test.key").read_text()}
            for leaf in ("fast", "slow"):
                api("POST", f"kv1/{prefix}-{leaf}/service", payload)
                self.assertEqual(api("LIST", f"kv1/{prefix}-{leaf}/")["data"]["keys"],
                                 ["service"])
            original_curl = shutil.which("curl")
            self.assertIsNotNone(original_curl)
            wrapper = base / "curl"
            wrapper.write_text(
                '#!/bin/sh\n'
                'for arg in "$@"; do\n'
                '  case "$arg" in\n'
                '    *"/v1/kv1/$VCLI_ACC09_SLOW_ROOT/")\n'
                '      sleep 6\n'
                '      break;;\n'
                '  esac\n'
                'done\n'
                'exec "$VCLI_ACC09_REAL_CURL" "$@"\n', encoding="utf-8",
            )
            wrapper.chmod(0o755)
            home, fd, child = self.session(extra_env={
                "VAULT_TOKEN": ROOT_TOKEN,
                "PATH": str(base) + os.pathsep + os.environ["PATH"],
                "VCLI_ACC09_REAL_CURL": original_curl,
                "VCLI_ACC09_SLOW_ROOT": prefix + "-slow",
            })
            try:
                fast_command = f"getallcertexpiration kv1/{prefix}-fast\n"
                started = time.monotonic()
                self.send(fd, fast_command.encode(), pace=0.003)
                fast = read_until(fd, b" >", timeout=12)
                fast_seconds = time.monotonic() - started
                self.assertIn(b"[CRITICAL]", fast)
                self.assertNotIn(b"large trees may take time", fast)
                self.assertLess(fast_seconds, 4.5, f"Fast scan delayed: {fast_seconds:.2f}s")
                slow_command = f"getallcertexpiration kv1/{prefix}-slow\n"
                self.send(fd, slow_command.encode(), pace=0.003)
                started = time.monotonic()
                notified = read_until(fd, b"large trees may take time...", timeout=9)
                seconds = time.monotonic() - started
                self.assertGreaterEqual(seconds, 4.3, f"Early notification: {seconds:.2f}s")
                self.assertLess(seconds, 7.5, f"Late notification: {seconds:.2f}s")
                self.assertIn(f"/kv1/{prefix}-slow".encode(), notified)
                self.assertNotIn(b"[CRITICAL]", notified)
                finished = read_until(fd, b" >", timeout=8)
                self.assertIn(b"[CRITICAL]", finished, finished[-1000:])
                self.assertNotIn(b"large trees may take time", finished,
                                 "Notification must appear only once per slow scan")
                self.assertIsNone(child.poll())
                # The fast scan's cancelled timer must not emit a delayed warning.
                self.assertEqual((fast + notified + finished).count(b"large trees may take time"), 1)
            finally:
                self.end(home, fd, child)

    def test_49_acc09_interrupt_cancels_delayed_notice(self):
        """ACC-09: Ctrl+C during a slow real-Vault LIST leaves no late output."""
        import shutil
        from uuid import uuid4
        from .test_vault import api, ROOT_TOKEN
        # Keep the line short: macOS PTY editing processes every character.
        prefix = "acc09-" + uuid4().hex[:8]
        api("POST", f"kv1/{prefix}/service", {"noncertificate": "sentinel"})
        with tempfile.TemporaryDirectory(prefix="vcli-acc09-slow-") as root:
            base = Path(root)
            original_curl = shutil.which("curl")
            self.assertIsNotNone(original_curl)
            marker = base / "list-started"
            wrapper = base / "curl"
            wrapper.write_text(
                '#!/bin/sh\n'
                'for arg in "$@"; do\n'
                '  case "$arg" in\n'
                '    *"/v1/kv1/$VCLI_ACC09_SLOW_ROOT/")\n'
                '      : > "$VCLI_ACC09_LIST_MARKER"\n'
                '      sleep 12\n'
                '      break;;\n'
                '  esac\n'
                'done\n'
                'exec "$VCLI_ACC09_REAL_CURL" "$@"\n', encoding="utf-8",
            )
            wrapper.chmod(0o755)
            home, fd, child = self.session(extra_env={
                "VAULT_TOKEN": ROOT_TOKEN,
                "PATH": str(base) + os.pathsep + os.environ["PATH"],
                "VCLI_ACC09_REAL_CURL": original_curl,
                "VCLI_ACC09_SLOW_ROOT": prefix,
                "VCLI_ACC09_LIST_MARKER": str(marker),
            })
            try:
                # On macOS the editor invokes terminfo during each keypress;
                # bursty input can race its raw-mode key reader and lose ENTER.
                # Use the same deliberate rate as the proven editor PTY tests.
                self.send(fd, f"getallcertexpiration kv1/{prefix}\n".encode(), pace=0.035)
                # The macOS byte-at-a-time editor needs more time to process
                # the long command than Linux. The five-second notice timer
                # starts only after parsing, not while the user types.
                # Synchronise with the real curl LIST entry, never a fixed
                # delay after sending keystrokes.
                deadline = time.monotonic() + 18
                while not marker.exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        break
                    time.sleep(0.05)
                if not marker.exists():
                    pending = bytearray()
                    # Never hide a broken interactive command behind a wait.
                    while True:
                        ready, _, _ = select.select([fd], [], [], 0)
                        if not ready:
                            break
                        try:
                            pending.extend(os.read(fd, 8192))
                        except OSError:
                            break
                    self.fail(
                        "The slow LIST was not reached; "
                        f"vcli status={child.poll()}; PTY tail={bytes(pending)[-1200:]!r}"
                    )
                # Signal the PTY's foreground process group as in the editor tests.
                os.killpg(child.pid, signal.SIGINT)
                interrupted = read_until(fd, b" >", timeout=10)
                self.assertNotIn(b"large trees may take time", interrupted)
                self.assertIsNone(child.poll(), "Ctrl+C must keep the shell usable")
                self.send(fd, b"help\n", pace=0.01)
                help_output = self.expect_help(fd, timeout=8)
                self.assertNotIn(b"large trees may take time", help_output)
                # Past the original 5-second threshold, there must be no orphaned notice.
                timeout = time.monotonic() + 5.4
                late = bytearray()
                while time.monotonic() < timeout:
                    readable, _, _ = select.select([fd], [], [], 0.2)
                    if readable:
                        try:
                            late.extend(os.read(fd, 8192))
                        except OSError:
                            break
                self.assertNotIn(b"large trees may take time", late)
                self.assertIsNone(child.poll())
            finally:
                self.end(home, fd, child)

if __name__ == "__main__":
    unittest.main()
