"""Shared HTTP transport defaults and security contract."""
from __future__ import annotations
import os, re, subprocess, unittest
from pathlib import Path
VCLI = Path(__file__).resolve().parents[2] / "vcli"

class TransportTests(unittest.TestCase):
    def run_helper(self, updates=None):
        source = VCLI.read_text()
        match = re.search(r"^function vcli_curl_transport_args\(\)\{.*?^\}", source, re.M | re.S)
        self.assertIsNotNone(match)
        env = dict(os.environ)
        for key in ("VAULT_CACERT", "VAULT_CAPATH", "VAULT_SKIP_VERIFY", "VAULT_CLIENT_CERT", "VAULT_CLIENT_KEY", "VAULT_NAMESPACE", "VCLI_CONNECT_TIMEOUT", "VCLI_HTTP_TIMEOUT"):
            env.pop(key, None)
        env.update(updates or {})
        shell = match.group(0) + "\nargs=()\nvcli_curl_transport_args args || exit $?\nprintf '%s\\0' \"${args[@]}\"\n"
        p = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, timeout=8)
        args = [value.decode() for value in p.stdout.split(b"\x00")[:-1]]
        return p, args

    def test_defaults(self):
        p, args = self.run_helper()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(args, ["--silent","--show-error","--connect-timeout","2","--max-time","30"])

    def test_tls_mtls_namespace_with_spaces(self):
        p, args = self.run_helper({"VAULT_CACERT":"/ca file", "VAULT_CAPATH":"/ca dir", "VAULT_CLIENT_CERT":"/cert file", "VAULT_CLIENT_KEY":"/key file", "VAULT_NAMESPACE":"team/app", "VCLI_CONNECT_TIMEOUT":"7", "VCLI_HTTP_TIMEOUT":"41"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(args, ["--silent","--show-error","--connect-timeout","7","--max-time","41","--cacert","/ca file","--capath","/ca dir","--cert","/cert file","--key","/key file","--header","X-Vault-Namespace: team/app"])

    def test_incomplete_mtls_rejected(self):
        for env in ({"VAULT_CLIENT_CERT":"onlycert"},{"VAULT_CLIENT_KEY":"onlykey"}):
            with self.subTest(env=env):
                p, args = self.run_helper(env)
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(args, [])
                self.assertIn(b"both VAULT_CLIENT_CERT and VAULT_CLIENT_KEY",p.stderr)

    def test_insecure_requires_optin(self):
        for value, expected in (("false",False),("no",False),("true",True),("YES",True),("1",True)):
            with self.subTest(value=value):
                p,args = self.run_helper({"VAULT_SKIP_VERIFY":value})
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual("--insecure" in args,expected)

    def test_both_callers_use_common_helper(self):
        source=VCLI.read_text()
        for fn in ("_curl","_vcli_login_approle"):
            with self.subTest(fn=fn):
                match=re.search(r"^function "+fn+r"\(\)\{.*?^\}",source,re.M|re.S)
                self.assertIsNotNone(match)
                self.assertIn("vcli_curl_transport_args args",match.group(0))

if __name__ == "__main__":
    unittest.main()
