"""Regression contracts: never strand secret payload files on early errors."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[2]/"vcli"

class PutTempfileContracts(unittest.TestCase):
    def run_put(self,mode):
        match=re.search(r"^function _put\(\)\{.*?^\}", SOURCE.read_text(), re.M|re.S)
        self.assertIsNotNone(match)
        script=r'''
TMP="$TEST_TMP"
current_pwd="/"
debug(){ :; }
envsubst(){ cat; }
vcli_is_kv2_path(){ return 0; }
vcli_kv2_metadata_path(){
  [[ "$MODE" != bad_path ]] || return 9
  printf '/kv2/metadata/test\n'
}
_curl(){
  if [[ "@{3:-GET}" == GET ]]; then
    case "$MODE" in
      denied) http_code=403; return 1 ;;
      malformed) printf '{"data":{"current_version":"bad"}}\n'; return 0 ;;
      *) http_code=404; return 1 ;;
    esac
  fi
  printf 'POST CAS=%s\n' "$4" >> "$AUDIT"
  [[ "$MODE" != transport ]] || return 17
  return 0
}
_put /kv2/test '{"password":"synthetic-only-marker"}'
'''.replace("@{", chr(36)+"{")
        with tempfile.TemporaryDirectory(prefix="vcli-put-temp-") as tmp:
            audit=Path(tmp)/"audit"
            env=os.environ.copy()
            env.update({"TEST_TMP":tmp,"MODE":mode,"AUDIT":str(audit)})
            result=subprocess.run(["bash","-c",match.group(0)+script],
                                  env=env,capture_output=True,text=True,timeout=10)
            left=[p for p in Path(tmp).iterdir() if p.name.startswith((".put.",".vcli.metadata."))]
            return result,left,audit.read_text().splitlines() if audit.exists() else []

    def test_rejected_metadata_does_not_leak(self):
        result,left,writes=self.run_put("denied")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(left,[])
        self.assertEqual(writes,[])

    def test_bad_metadata_path_or_payload_does_not_leak(self):
        for mode in ("bad_path","malformed"):
            with self.subTest(mode=mode):
                result,left,writes=self.run_put(mode)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(left,[])
                self.assertEqual(writes,[])

    def test_transport_failure_cleans_payload(self):
        result,left,writes=self.run_put("transport")
        self.assertEqual(result.returncode,17,result.stderr)
        self.assertEqual(left,[])
        self.assertEqual(writes,["POST CAS=0"])

    def test_success_cleans_payload(self):
        result,left,writes=self.run_put("success")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(left,[])
        self.assertEqual(writes,["POST CAS=0"])

if __name__=="__main__":
    unittest.main()
