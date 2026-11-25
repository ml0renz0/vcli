"""Native GNU/BSD stat parity for sensitive file identity checks."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

def stat_helper():
    match = re.search(r"^function vcli_stat_identity\(\)\{.*?^\}",
                      SOURCE.read_text(), re.M | re.S)
    if not match:
        raise AssertionError("Missing vcli_stat_identity")
    return match.group(0)

class StatPortability(unittest.TestCase):
    def test_fd_and_path_have_matching_inode_owner_mode_links(self):
        with tempfile.TemporaryDirectory(prefix="vcli-stat-portability-") as home:
            file = Path(home) / "log with spaces"
            file.write_bytes(b"synthetic")
            file.chmod(0o600)
            code = r'''
exec {fd}>>"$TARGET"
vcli_stat_identity "$TARGET"
vcli_stat_identity "/dev/fd/$fd"
exec {fd}>&-
'''
            env = os.environ.copy()
            env["TARGET"] = str(file)
            result = subprocess.run(["bash", "-c", stat_helper() + code],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            path, fd = result.stdout.splitlines()
            self.assertEqual(path, fd)
            inode, uid, mode, nlink = path.split(":")
            self.assertEqual(int(inode), file.stat().st_ino)
            self.assertEqual(int(uid), os.getuid())
            self.assertEqual(int(mode, 8), 0o600)
            self.assertEqual(int(nlink), 1)

    @unittest.skipUnless(sys.platform == "darwin", "Native BSD stat requires macOS")
    def test_bsd_stat_executable_produces_private_file_metadata(self):
        with tempfile.TemporaryDirectory(prefix="vcli-bsd-stat-") as home:
            file = Path(home) / "private"
            file.write_bytes(b"synthetic")
            file.chmod(0o600)
            result = subprocess.run(
                ["/usr/bin/stat", "-L", "-f", "%i:%u:%OLp:%l", str(file)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             f"{file.stat().st_ino}:{os.getuid()}:600:1")
            self.assertIn("/usr/bin/stat -L -f", stat_helper())

if __name__ == "__main__":
    unittest.main()
