"""04-Q HTTP-03: request payload ownership and cleanup, using production Bash."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"

def functions(*names):
    source = VCLI.read_text(encoding="utf-8")
    blocks = []
    for name in names:
        match = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}", source, re.M | re.S)
        if not match:
            raise AssertionError("Missing production Bash function " + name)
        blocks.append(match.group(0))
    return "\n".join(blocks)

class PayloadOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = functions("vcli_curl_transport_args", "vcli_emit_http_response", "_curl")

    def invoke_http(self, mode, kv2=False):
        with tempfile.TemporaryDirectory(prefix="vcli-http-owner-") as root:
            base = Path(root)
            scratch = base / "scratch"
            scratch.mkdir()
            caller = base / "caller payload.json"
            caller.write_text('{"secret":"synthetic-marker"}\n', encoding="utf-8")
            captured = base / "captured.json"
            script = r'''
TMP="$SCRATCH"
VCLI_SESSION_TMP="$SCRATCH"
protocol=https
vault_host=127.0.0.1
vault_port=8200
vault_token=synthetic-token
debug(){ :; }
vcli_encode_endpoint(){ printf '%s' "$1"; }
vcli_match_kv2_mount(){
    [[ $KV2 == yes && $1 == kv2/* ]] || return 1
    VCLI_KV_MOUNT=kv2
}
vcli_kv_path(){ printf 'kv2/data/%s' "$1"; }
curl(){
    local output='' body=''
    while (( $# )); do
        case $1 in
            --output) output="$2"; shift 2;;
            --data-binary) body="@{2#@}"; shift 2;;
            *) shift;;
        esac
    done
    [[ -n $body ]] || return 70
    cp "$body" "$CAPTURE" || return 71
    if [[ $MODE == transport ]]; then return 52; fi
    printf '{"data":{"ok":true}}\n' > "$output"
    printf 200
}
_curl /"$PATH_TO_SECRET" "$CALLER" POST
'''.replace("@{", chr(36) + "{")
            env = dict(os.environ)
            for key in ("VAULT_CACERT", "VAULT_CAPATH", "VAULT_SKIP_VERIFY", "VAULT_CLIENT_CERT", "VAULT_CLIENT_KEY", "VAULT_NAMESPACE"):
                env.pop(key, None)
            env.update({"SCRATCH": str(scratch), "CALLER": str(caller),
                        "CAPTURE": str(captured), "MODE": mode,
                        "KV2": "yes" if kv2 else "no",
                        "PATH_TO_SECRET": "kv2/secret" if kv2 else "kv1/secret"})
            out = subprocess.run(["bash", "-c", self.http + "\n" + script],
                                 env=env, text=True, capture_output=True, timeout=10)
            return out, caller.read_bytes(), captured.read_bytes(), [p.name for p in scratch.iterdir()]

    def test_caller_payload_survives_success_in_kv1_and_kv2(self):
        for kv2 in (False, True):
            with self.subTest(kv2=kv2):
                out, caller, captured, leaked = self.invoke_http("success", kv2)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(caller, b'{"secret":"synthetic-marker"}\n')
                if kv2:
                    self.assertEqual(captured, b'{"data":{"secret":"synthetic-marker"}}\n')
                else:
                    self.assertEqual(captured, caller)
                self.assertEqual(leaked, [])

    def test_transport_failure_keeps_external_file_and_cleans_wrappers(self):
        for kv2 in (False, True):
            with self.subTest(kv2=kv2):
                out, caller, captured, leaked = self.invoke_http("transport", kv2)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn("OUTCOME_UNKNOWN", out.stderr)
                self.assertEqual(caller, b'{"secret":"synthetic-marker"}\n')
                self.assertTrue(captured)
                self.assertEqual(leaked, [])

    def invoke_owner(self, name, mode):
        with tempfile.TemporaryDirectory(prefix="vcli-owner-call-") as tmp:
            env = dict(os.environ)
            env.update({"SCRATCH": tmp, "MODE": mode, "OWNER": name})
            script = r'''
TMP="$SCRATCH"
VCLI_SESSION_TMP="$SCRATCH"
current_pwd=/
http_code=200
VAULT_KV_VERSION=1
debug(){ :; }
_vcli_absolute_path(){ printf '%s' "$1"; }
vcli_is_kv2_path(){ return 0; }
vcli_kv2_metadata_path(){ printf '%s' '/kv2/metadata/foo'; }
_curl(){
    # Internal typed Vault endpoints have an explicit private transport flag.
    [[ ${1:-} == -_api_path_- ]] && shift
    local endpoint="$1" body="@{2:-}"
    if [[ -n $body && -f $body ]]; then
        [[ -s $body ]] || return 69
    fi
    if [[ $MODE == fail && -n $body ]]; then return 52; fi
    case "$endpoint" in
        /auth/token/renew-self) printf '{"auth":{"renewable":true}}\n';;
        /kv2/foo) printf '{"data":{"data":{"fixture":"active"},"metadata":{"version":1,"deletion_time":""}}}\n';;
        /kv2/metadata/foo) printf '{"data":{"current_version":1}}\n';;
        *) http_code=200; printf '{}\n';;
    esac
}
case "$OWNER" in
    renew) _renew_token 45s;;
    mount) _mount /kv2test;;
    rm) _rm /kv2/foo;;
esac
'''.replace("@{", chr(36) + "{")
            cmd = functions("_renew_token", "_mount", "_rm") + "\n" + script
            out = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True,
                                 text=True, timeout=10)
            return out, [p.name for p in Path(tmp).iterdir()]

    def test_renewal_mount_and_delete_clean_owned_payloads(self):
        for owner in ("renew", "mount", "rm"):
            for mode in ("ok", "fail"):
                with self.subTest(owner=owner, mode=mode):
                    out, leftovers = self.invoke_owner(owner, mode)
                    if mode == "ok":
                        self.assertEqual(out.returncode, 0, out.stderr)
                    else:
                        self.assertNotEqual(out.returncode, 0)
                    self.assertEqual(leftovers, [])

    def test_already_deleted_kv2_http_200_null_data_never_deletes_again(self):
        """ACC-10: reproduce Vault's HTTP 200 + data:null deleted version."""
        with tempfile.TemporaryDirectory(prefix="vcli-deleted-200-") as root:
            env = dict(os.environ)
            env["DELETE_SENTINEL"] = str(Path(root) / "delete-called")
            script = r'''
current_pwd=/
TMP="$TMPDIR"
http_code=200
_vcli_absolute_path(){ printf '%s' "$1"; }
vcli_is_kv2_path(){ return 0; }
vcli_kv2_metadata_path(){ printf '/kv2/metadata/item'; }
_curl(){
    case "$3" in
        DELETE|POST) printf touched > "$DELETE_SENTINEL"; return 0;;
    esac
    case "$1" in
        /kv2/item)
            printf '%s\n' '{"data":{"data":null,"metadata":{"version":2,"deletion_time":"2026-11-22T19:19:49Z","destroyed":false}}}'
            ;;
        *) echo unexpected_endpoint >&2; return 42;;
    esac
}
_rm /kv2/item
'''
            result = subprocess.run(["bash", "-c", functions("_rm") + "\n" + script],
                                    capture_output=True, text=True, env=env, timeout=8)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("already soft-deleted", result.stderr)
            self.assertFalse(Path(env["DELETE_SENTINEL"]).exists(),
                             "A deleted KV2 version must not trigger another DELETE")

    def test_transport_never_deletes_caller_payload(self):
        source = functions("_curl")
        self.assertNotIn('rm -f "$data"', source)
        self.assertNotIn('rm -f -- "$data"', source)
        self.assertIn('rm -f -- "$payload_file"', source)
        for fn in ("_renew_token", "_mount", "_rm", "_mv", "_put"):
            with self.subTest(fn=fn):
                self.assertIn("rm -f --", functions(fn))

if __name__ == "__main__":
    unittest.main()
