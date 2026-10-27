"""Linux PTY smoke tests for VCLI's legacy interactive key-reader.

Not a replacement for the planned full keyboard regression matrix.
"""
from __future__ import annotations

import os
from pathlib import Path
import pty
import signal
import select
import subprocess
import tempfile
import time
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


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
            child = subprocess.Popen([str(VCLI)], stdin=slave, stdout=slave,
                                     stderr=slave, env=env, start_new_session=True)
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
                self.assertEqual(child.wait(timeout=8), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                os.close(master)


    def session(self, history: str | None = None, files: dict[str, str] | None = None):
        """Return PTY process with its own HOME and real Vault TLS credentials."""
        home = tempfile.TemporaryDirectory(prefix="vcli-repl-matrix-")
        env = os.environ.copy()
        env.update({
            "HOME": home.name, "TMPDIR": home.name, "TERM": "xterm",
            "VAULT_ADDR": os.environ["VAULT_ADDR"],
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
            "VAULT_TOKEN": os.environ.get("VCLI_TEST_READONLY_TOKEN", ""),
        })
        if history is not None:
            (Path(home.name) / ".vclistory").write_text(history, encoding="utf-8")
        for name, content in (files or {}).items():
            (Path(home.name) / name).write_text(content, encoding="utf-8")
        master, slave = pty.openpty()
        child = subprocess.Popen([str(VCLI)], stdin=slave, stdout=slave,
                                 stderr=slave, env=env, cwd=home.name,
                                 start_new_session=True)
        os.close(slave)
        read_until(master, b" >", timeout=12)
        return home, master, child

    def send(self, fd, data, pace=0.012):
        for b in data:
            os.write(fd, bytes([b]))
            if pace:
                time.sleep(pace)

    def end(self, home, fd, child):
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        os.close(fd)
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
            self.send(fd, b"not-a-valid-command")
            os.kill(child.pid, signal.SIGINT)
            time.sleep(0.2)
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
            self.assertEqual(child.wait(timeout=8), 0)
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
                time.sleep(0.15)
            self.send(fd, b"\x04")
            self.assertEqual(child.wait(timeout=8), 0)
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
                self.assertEqual(child.wait(timeout=8), 0)
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
            self.assertEqual(child.wait(timeout=8), 0)
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


if __name__ == "__main__":
    unittest.main()
