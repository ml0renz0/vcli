"""AppRole security: secrets absent from process argv, no stranded files."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"

def functions(*names):
    source=VCLI.read_text(encoding="utf-8")
    found=[]
    for name in names:
        match=re.search(r"^function "+re.escape(name)+r"\(\)\{.*?^\}",source,re.M|re.S)
        if match is None:
            raise AssertionError(f"Missing {name}")
        found.append(match.group(0))
    return "\n".join(found)

class AppRoleSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell=functions("vcli_session_cleanup","vcli_session_open","_vcli_login_approle")

    def run_auth(self,mode,role="role-test",secret="secret-test"):
        shell=r'''
TMP="$TEST_TMP"
VCLI_SESSION_TMP=""
VCLI_SESSION_PID=""
protocol="https"
vault_host="127.0.0.1"
vault_port="8200"
umask 077
jq(){ printf '%s\n' "$@" >> "$JQ_ARGV"; command jq "$@"; }
curl(){
  local output="" payload=""
  while (( $# )); do
    case "$1" in
      --output) output="$2"; shift 2 ;;
      --data-binary) payload="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -n $output && -n $payload ]] || return 30
  cat -- "@{payload#@}" > "$BODY_CAPTURE" || return 31
  if [[ $MODE == signaled ]]; then kill -s TERM "$OWNER"; return 1; fi
  printf '{"auth":{"client_token":"synthetic-issued-token"}}\n' > "$output"
  printf 200
}
vcli_session_open || exit 20
OWNER="$BASHPID"
if [[ $MODE == incomplete ]]; then
  if _vcli_login_approle; then exit 90; fi
  find "$VCLI_SESSION_TMP" -type f -print > "$LEFTOVERS"
  exit 0
fi
_vcli_login_approle
code=$?
if [[ $MODE == success ]]; then
  [[ $code == 0 && $vault_token == synthetic-issued-token ]] || exit 34
  find "$VCLI_SESSION_TMP" -type f -print > "$LEFTOVERS"
fi
exit "$code"
'''.replace("@{",chr(36)+"{")
        with tempfile.TemporaryDirectory(prefix="vcli-approle-security-") as tmp:
            home=Path(tmp)
            sentinel=home/"backup_vault_preserve.tgz"
            sentinel.write_bytes(b"user-owned")
            env=os.environ.copy()
            env.update({"TEST_TMP":tmp,"VAULT_ROLE_ID":role,"VAULT_SECRET_ID":secret,"MODE":mode,
                        "BODY_CAPTURE":str(home/"body.json"),"JQ_ARGV":str(home/"jq-argv.txt"),
                        "LEFTOVERS":str(home/"leftovers.txt")})
            for key in ("VAULT_CLIENT_CERT","VAULT_CLIENT_KEY"):
                env.pop(key,None)
            if mode=="incomplete":
                env["VAULT_CLIENT_CERT"]="only-cert"
            result=subprocess.run(["bash","-c",self.shell+shell],env=env,
                                  text=True,capture_output=True,timeout=10)
            body=json.loads((home/"body.json").read_text()) if (home/"body.json").exists() else None
            argv=(home/"jq-argv.txt").read_text() if (home/"jq-argv.txt").exists() else ""
            leftovers=(home/"leftovers.txt").read_text().splitlines() if (home/"leftovers.txt").exists() else []
            self.assertEqual(sentinel.read_bytes(),b"user-owned")
            self.assertEqual(list(home.glob(".vcli-session.*")),[])
            self.assertNotIn(secret,result.stdout+result.stderr)
            return result,body,argv,leftovers

    def test_secret_not_present_in_jq_argv(self):
        role='role with spaces and quote "'
        secret="synthetic secret-id with newline\nand a quote '"
        result,body,argv,leftovers=self.run_auth("success",role,secret)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(body,{"role_id":role,"secret_id":secret})
        self.assertNotIn(role,argv)
        self.assertNotIn(secret,argv)
        self.assertEqual(leftovers,[])

    def test_incomplete_mtls_fails_before_creating_files(self):
        result,body,_,leftovers=self.run_auth("incomplete")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIsNone(body)
        self.assertEqual(leftovers,[])
        self.assertIn("both VAULT_CLIENT_CERT and VAULT_CLIENT_KEY",result.stderr)

    def test_termination_during_auth_cleans_files(self):
        result,body,_,_=self.run_auth("signaled")
        self.assertEqual(result.returncode,143,result.stderr)
        self.assertEqual(body["secret_id"],"secret-test")

if __name__=="__main__":
    unittest.main()
