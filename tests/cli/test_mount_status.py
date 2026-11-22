"""Montage (mount) reports occupancy without leaking any secret payloads."""
from pathlib import Path
import re
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

class MountStatusContracts(unittest.TestCase):
    def test_mount_listing_reports_empty_populated_and_non_kv(self):
        text = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _mount\(\)\{.*?^\}", text, re.M | re.S)
        self.assertIsNotNone(match)
        shell = match.group(0) + r"""
current_pwd=""
http_code=200
_curl(){
  if [[ $1 == /sys/mounts ]]; then
    printf '{"data":{"kv1/":{"type":"kv","options":{"version":"1"}},"kv2/":{"type":"kv","options":{"version":"2"}},"cubbyhole/":{"type":"cubbyhole"},"sys/":{"type":"system"}}}\n'
    return 0
  fi
  # The raw endpoint flag ensures KV1 also works if the client map is stale.
  [[ $1 == -_api_path_- ]] || return 97
  shift
  if [[ $1 == /kv1/ && $3 == LIST ]]; then http_code=200; return 0; fi
  if [[ $1 == /kv2/metadata/ && $3 == LIST ]]; then http_code=200; return 0; fi
  if [[ $1 == /cubbyhole/ && $3 == LIST ]]; then http_code=404; return 1; fi
  return 19
}
_mount
"""
        out = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, timeout=8)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("cubbyhole/", out.stdout)
        self.assertIn("empty", out.stdout)
        self.assertIn("kv1/", out.stdout)
        self.assertRegex(out.stdout, r"(?m)^kv2/\s+kv\s+contains entries$")
        self.assertIn("contains entries", out.stdout)
        self.assertIn("not a KV secret mount", out.stdout)
        self.assertNotIn("secret payload", out.stdout)

    def test_long_pki_scan_has_killable_delayed_notice(self):
        src = SOURCE.read_text(encoding="utf-8")
        fn = re.search(r"^function _getallcertexpiration\(\)\{.*?^\}",src,re.M|re.S)
        self.assertIsNotNone(fn)
        self.assertIn("sleep 5",fn.group(0))
        self.assertIn('kill "$notice_pid"',fn.group(0))
        self.assertIn('wait "$notice_pid"',fn.group(0))

if __name__ == "__main__": unittest.main()
