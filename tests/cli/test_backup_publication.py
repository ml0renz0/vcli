"""Backup publication security: no overwrite, symlinks or partial archives.

This test extracts the production Bash _backup implementation. A simulated
racer creates a destination immediately before the atomic link operation.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


class BackupPublicationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _backup\(\)\{.*?^\}", code, re.M | re.S)
        if not match:
            raise AssertionError("Missing production _backup function")
        cls.backup = match.group(0)

    def invoke(self, mode="normal", calls=1):
        with tempfile.TemporaryDirectory(prefix="vcli-backup-publish-") as temp:
            root = Path(temp)
            staging = root / "private"
            staging.mkdir(mode=0o700)
            victim = root / "unrelated-sensitive-file"
            victim.write_bytes(b"do not touch")
            script = r'''
umask 077
TMP="$TEST_TMP"
VCLI_SESSION_TMP="$STAGING"
_vcli_absolute_path(){ printf '%s\n' "$1"; }
_cat(){
  [[ "$1" != "/kv1/sample" ]] || return 1
  printf '{"data":{"key":"synthetic"}}\n'
}
_vcli_leaves(){
  printf '%s\n' "/kv1/sample/a" "/kv1/sample/b"
}
ln(){
  local dest="@{@: -1}"
  case "$MODE" in
    symlink) command ln -s -- "$VICTIM" "$dest" || return $? ;;
    existing) printf 'preexisting\n' > "$dest" || return $? ;;
  esac
  command ln "$@"
}
tar(){
  [[ "$MODE" != tar_fail ]] || return 17
  command tar "$@"
}
for ((i=0;i<CALLS;i++)); do
  _backup /kv1/sample || exit $?
done
'''.replace("@{", chr(36) + "{")
            env = os.environ.copy()
            env.update({
                "TEST_TMP": temp,
                "STAGING": str(staging),
                "VICTIM": str(victim),
                "MODE": mode,
                "CALLS": str(calls),
            })
            result = subprocess.run(
                ["bash", "-c", self.backup + script],
                capture_output=True, text=True, env=env, timeout=20,
            )
            archives = list(root.glob("backup_vault*.tgz"))
            contents = {}
            for archive in archives:
                if archive.is_file() and not archive.is_symlink():
                    with tarfile.open(archive, "r:gz") as handle:
                        contents[archive.name] = {
                            name.removeprefix("./"): handle.extractfile(member).read()
                            for member in handle
                            if member.isfile()
                            for name in [member.name]
                        }
            return {
                "result": result,
                "archives": [a.name for a in archives],
                "files": contents,
                "modes": {a.name: stat.S_IMODE(a.stat().st_mode)
                          for a in archives if a.is_file() and not a.is_symlink()},
                "stages": [x.name for x in staging.iterdir()],
                "victim": victim.read_bytes(),
                "links": [a.name for a in archives if a.is_symlink()],
                "other": {a.name: a.read_text() for a in archives
                          if a.is_file() and not a.is_symlink()
                          and mode == "existing"},
            }

    def test_two_backups_in_same_second_have_distinct_private_archives(self):
        out = self.invoke(calls=2)
        self.assertEqual(out["result"].returncode, 0, out["result"].stderr)
        self.assertEqual(len(out["archives"]), 2)
        self.assertEqual(len(set(out["archives"])), 2)
        self.assertEqual(set(out["files"]), set(out["archives"]))
        self.assertEqual(set(out["modes"].values()), {0o600})
        self.assertEqual(out["stages"], [])
        for members in out["files"].values():
            self.assertEqual(members, {
                "kv1/sample/a": b'{"key":"synthetic"}\n',
                "kv1/sample/b": b'{"key":"synthetic"}\n',
            })

    def test_raced_symlink_does_not_clobber_target(self):
        out = self.invoke("symlink")
        self.assertNotEqual(out["result"].returncode, 0)
        self.assertIn("cannot be published", out["result"].stderr)
        self.assertEqual(out["victim"], b"do not touch")
        self.assertEqual(out["links"], out["archives"])
        self.assertEqual(out["files"], {})
        self.assertEqual(out["stages"], [])

    def test_raced_existing_file_is_not_overwritten(self):
        out = self.invoke("existing")
        self.assertNotEqual(out["result"].returncode, 0)
        self.assertEqual(out["other"], {out["archives"][0]: "preexisting\n"})
        self.assertEqual(out["stages"], [])

    def test_tar_failure_does_not_publish_partial_archive(self):
        out = self.invoke("tar_fail")
        self.assertNotEqual(out["result"].returncode, 0)
        self.assertEqual(out["archives"], [])
        self.assertEqual(out["stages"], [])
        self.assertEqual(out["victim"], b"do not touch")


if __name__ == "__main__":
    unittest.main()
