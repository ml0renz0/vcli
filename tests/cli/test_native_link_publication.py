"""Characterize native link(1) as an exact-target, exclusive publisher.

These are security prerequisites for eventually replacing GNU 'ln -T'.
The tests use the real system utility, including BSD /usr/bin/link on macOS.
No production implementation is changed by this contract.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


def native_link():
    if sys.platform == "darwin":
        binary = Path("/usr/bin/link")
        if not binary.is_file():
            raise AssertionError("macOS /usr/bin/link is missing")
        return str(binary)
    binary = shutil.which("link")
    if not binary:
        raise AssertionError("No link(1) executable available")
    return binary


class NativeLinkPublicationContracts(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix="vcli-native-link-")
        self.addCleanup(self.home.cleanup)
        self.root = Path(self.home.name)
        self.staging = self.root / "staging"
        self.staging.write_bytes(b"synthetic private bytes")
        self.staging.chmod(0o600)
        self.dest = self.root / "published"

    def publish(self):
        return subprocess.run([native_link(), str(self.staging), str(self.dest)],
                              capture_output=True, text=True, timeout=5)

    def test_exclusive_publication_preserves_private_inode(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dest.read_bytes(), self.staging.read_bytes())
        self.assertEqual(self.dest.stat().st_ino, self.staging.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o600)
        self.assertEqual(self.dest.stat().st_nlink, 2)
        self.staging.unlink()
        self.assertEqual(self.dest.read_bytes(), b"synthetic private bytes")

    def test_existing_regular_target_never_overwritten(self):
        self.dest.write_bytes(b"preexisting")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.dest.read_bytes(), b"preexisting")
        self.assertEqual(self.staging.read_bytes(), b"synthetic private bytes")

    def test_existing_symlink_never_followed_or_replaced(self):
        victim = self.root / "victim"
        victim.write_bytes(b"protected")
        self.dest.symlink_to(victim)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.dest.is_symlink())
        self.assertEqual(victim.read_bytes(), b"protected")
        self.assertEqual(self.staging.read_bytes(), b"synthetic private bytes")

    def test_existing_directory_never_receives_a_nested_link(self):
        self.dest.mkdir()
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(self.staging.read_bytes(), b"synthetic private bytes")

    def test_repeated_publication_is_exclusive(self):
        self.assertEqual(self.publish().returncode, 0)
        original = self.dest.stat()
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.dest.stat().st_ino, original.st_ino)
        self.assertEqual(self.dest.read_bytes(), b"synthetic private bytes")


if __name__ == "__main__":
    unittest.main()
