"""Platform-specific exact-path publishing contracts, no Vault required.

Linux uses GNU ln/mv with -T; Darwin executes link(2)/rename(2) through
the system Perl interpreter. Tests exercise both variants on Linux when
system Perl exists, and the Darwin path on macOS with GNU ln/mv forbidden.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

def helpers():
    source = SOURCE.read_text(encoding="utf-8")
    found = []
    for name in ("vcli_link_exclusive", "vcli_replace_exact"):
        match = re.search(r"^function " + name + r"\(\)\{.*?^\}", source, re.M | re.S)
        if not match:
            raise AssertionError(f"Missing production helper: {name}")
        found.append(match.group(0))
    return "\n".join(found)

class ExactPathPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = helpers()

    def invoke(self, operation, target_mode, force_native=False):
        with tempfile.TemporaryDirectory(prefix="vcli-fs-exact-") as name:
            root = Path(name)
            stage = root / "source with spaces"
            dest = root / "destination with spaces"
            victim = root / "unrelated"
            directory = root / "existing directory"
            stage.write_bytes(b"synthetic-private-file\n")
            stage.chmod(0o600)
            victim.write_bytes(b"unchanged\n")
            directory.mkdir()
            if target_mode == "file":
                dest.write_bytes(b"old content\n")
            elif target_mode == "symlink":
                dest.symlink_to(victim)
            elif target_mode == "symlink-dir":
                dest.symlink_to(directory, target_is_directory=True)
            elif target_mode == "directory":
                dest.mkdir()
            env = os.environ.copy()
            env.update({"VCLI_STAGE": str(stage), "VCLI_DEST": str(dest),
                        "VCLI_NATIVE": "yes" if force_native else "no",
                        "VCLI_OP": operation})
            script = r'''
if [[ "$VCLI_NATIVE" == yes ]]; then
  # Simulate Darwin on Linux, and prohibit GNU commands in the native path.
  OSTYPE=darwin
  ln(){ echo "unexpected GNU ln" >&2; return 79; }
  mv(){ echo "unexpected GNU mv" >&2; return 79; }
fi
if [[ "$VCLI_OP" == link ]]; then
  vcli_link_exclusive "$VCLI_STAGE" "$VCLI_DEST"
else
  vcli_replace_exact "$VCLI_STAGE" "$VCLI_DEST"
fi
'''
            out = subprocess.run(["bash", "-c", self.code + "\n" + script],
                                 env=env, capture_output=True, text=True, timeout=10)
            return {
                "result": out,
                "source_exists": stage.exists(),
                "source_bytes": stage.read_bytes() if stage.exists() else None,
                "dest_exists": dest.exists() or dest.is_symlink(),
                "dest_is_symlink": dest.is_symlink(),
                "dest_is_dir": dest.is_dir() and not dest.is_symlink(),
                "dest_bytes": dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None,
                "same_inode": (stage.exists() and dest.exists() and
                               os.stat(stage).st_ino == os.stat(dest).st_ino),
                "dest_mode": (stat.S_IMODE(dest.stat().st_mode)
                              if dest.is_file() and not dest.is_symlink() else None),
                "victim_bytes": victim.read_bytes(),
                "dir_entries": list(directory.iterdir()),
                "dest_dir_entries": list(dest.iterdir()) if dest.is_dir() and not dest.is_symlink() else None,
            }

    def backends(self):
        # The portable logic is independently exercised by the same cases.
        if sys.platform == "darwin":
            return (False, True)
        if os.access("/usr/bin/perl", os.X_OK):
            return (False, True)
        return (False,)

    def test_link_creates_private_hardlink_only_if_destination_absent(self):
        for native in self.backends():
            with self.subTest(native=native):
                result = self.invoke("link", "absent", native)
                self.assertEqual(result["result"].returncode, 0, result["result"].stderr)
                self.assertTrue(result["source_exists"])
                self.assertTrue(result["same_inode"])
                self.assertEqual(result["dest_bytes"], b"synthetic-private-file\n")
                self.assertEqual(result["dest_mode"], 0o600)

    def test_link_rejects_occupied_file_symlink_and_directory(self):
        for native in self.backends():
            for mode in ("file", "symlink", "symlink-dir", "directory"):
                with self.subTest(native=native, mode=mode):
                    result = self.invoke("link", mode, native)
                    self.assertNotEqual(result["result"].returncode, 0)
                    self.assertTrue(result["source_exists"])
                    self.assertEqual(result["victim_bytes"], b"unchanged\n")
                    self.assertEqual(result["dir_entries"], [])
                    self.assertNotIn("unexpected GNU", result["result"].stderr)
                    if mode == "file":
                        self.assertEqual(result["dest_bytes"], b"old content\n")
                    elif mode in ("symlink", "symlink-dir"):
                        self.assertTrue(result["dest_is_symlink"])
                    else:
                        self.assertTrue(result["dest_is_dir"])
                        self.assertEqual(result["dest_dir_entries"], [])

    def test_rename_exact_replaces_file_and_symlink_without_following(self):
        for native in self.backends():
            for mode in ("absent", "file", "symlink", "symlink-dir"):
                with self.subTest(native=native, mode=mode):
                    result = self.invoke("rename", mode, native)
                    self.assertEqual(result["result"].returncode, 0, result["result"].stderr)
                    self.assertFalse(result["source_exists"])
                    self.assertFalse(result["dest_is_symlink"])
                    self.assertEqual(result["dest_bytes"], b"synthetic-private-file\n")
                    self.assertEqual(result["dest_mode"], 0o600)
                    self.assertEqual(result["victim_bytes"], b"unchanged\n")
                    self.assertEqual(result["dir_entries"], [])

    def test_rename_exact_refuses_directory_without_moving_inside(self):
        for native in self.backends():
            with self.subTest(native=native):
                result = self.invoke("rename", "directory", native)
                self.assertNotEqual(result["result"].returncode, 0)
                self.assertTrue(result["source_exists"])
                self.assertTrue(result["dest_is_dir"])
                self.assertEqual(result["dest_dir_entries"], [])
                self.assertEqual(result["victim_bytes"], b"unchanged\n")
                self.assertNotIn("unexpected GNU", result["result"].stderr)

if __name__ == "__main__":
    unittest.main()
