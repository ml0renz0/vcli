"""Isolated Bash contracts: KV2 longest mount, preflight and CAS."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

class RecursiveCopyContracts(unittest.TestCase):
    def run_case(self, status="404"):
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _cptree\(\)\{.*?^\}", source, re.M | re.S)
        self.assertIsNotNone(match)
        script = r'''
_vcli_absolute_path(){ printf '/%s\n' "@{1#/}"; }
_vcli_leaves(){ printf '%s\n' '/kv1/src/config/a' '/kv1/src/other name'; }
vcli_is_kv2_path(){
  printf 'MATCH %s\n' "$1" >> "$AUDIT"
  case "$1" in
    /team/config/*) VCLI_KV_MOUNT='team/config'; return 0 ;;
    /team/*) VCLI_KV_MOUNT='team'; return 0 ;;
    *) VCLI_KV_MOUNT=''; return 1 ;;
  esac
}
vcli_kv2_metadata_path(){ echo 'redundant lookup' >&2; return 90; }
_curl(){
  printf 'PROBE %s\n' "$1" >> "$AUDIT"
  http_code="$PREFLIGHT_STATUS"
  return 1
}
_cat(){ printf 'READ %s\n' "$1" >> "$AUDIT"; printf '{"data":{"payload":"ok"}}\n'; }
_put(){ printf 'WRITE %s CAS=%s DATA=%s\n' "$1" "@{3:-none}" "$2" >> "$AUDIT"; }
_vcli_partial_failure(){ echo 'unexpected failure' >&2; return 90; }
_cptree /kv1/src /team
'''.replace("@{", chr(36) + "{")
        with tempfile.TemporaryDirectory(prefix="vcli-copy-test-") as tmp:
            audit = Path(tmp) / "audit"
            env = os.environ.copy()
            env.update({"AUDIT": str(audit), "PREFLIGHT_STATUS": status})
            result = subprocess.run(["bash", "-c", match.group(0) + script],
                                    capture_output=True, text=True, env=env, timeout=10)
            return result, audit.read_text().splitlines() if audit.exists() else []

    def test_nested_mount_and_all_preflights_before_writes(self):
        result, trace = self.run_case()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace, [
            "MATCH /team/config/a",
            "PROBE /team/config/metadata/a",
            "MATCH /team/other name",
            "PROBE /team/metadata/other name",
            "READ /kv1/src/config/a",
            'WRITE /team/config/a CAS=0 DATA={"payload":"ok"}',
            "READ /kv1/src/other name",
            'WRITE /team/other name CAS=0 DATA={"payload":"ok"}',
        ])

    def test_403_fails_before_any_write(self):
        result, trace = self.run_case("403")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP 403", result.stderr)
        self.assertEqual(trace, [
            "MATCH /team/config/a", "PROBE /team/config/metadata/a",
        ])

if __name__ == "__main__":
    unittest.main()
