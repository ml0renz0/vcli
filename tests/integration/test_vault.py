"""Contract tests for the *full* Bash VCLI process against a live Vault API.

Use only with tests/integration/run.sh, which owns the ephemeral dev server.
Assertions use a separately issued raw Vault API request as an oracle; output
text alone is not proof that data was stored or deleted correctly.
"""
from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib import error, request
from uuid import uuid4

VCLI = Path(__file__).resolve().parents[2] / "vcli"
VAULT = os.environ.get("VAULT_ADDR", "https://127.0.0.1:8200")
ROOT_TOKEN = "local-ci-root-only"  # Intentionally ephemeral and CI-only.


def api(method: str, path: str, data=None, token=ROOT_TOKEN):
    body = json.dumps(data).encode() if data is not None else None
    req = request.Request(
        f"{VAULT}/v1/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
    )
    cafile = os.environ.get("VAULT_CACERT")
    if not VAULT.startswith("https://") or not cafile:
        raise RuntimeError("Vault E2E suite must use HTTPS with a trusted private CA")
    ctx = ssl.create_default_context(cafile=cafile)
    with request.urlopen(req, timeout=5, context=ctx) as response:
        contents = response.read()
        return json.loads(contents) if contents else {}


def invoke(command: str, *, version=1, mounts="kv2,team/config", token=ROOT_TOKEN,
           addr=VAULT, env_extra=None):
    """Call VCLI through its real character-by-character stdin parser."""
    with tempfile.TemporaryDirectory(prefix="vcli-ci-") as directory:
        env = os.environ.copy()
        for key in ("VAULT_HOST", "VAULT_PORT", "VAULT_NAMESPACE", "VAULT_CACERT",
                    "VAULT_CAPATH", "VAULT_SKIP_VERIFY", "VCLI_HISTORY_SECRETS"):
            env.pop(key, None)
        env.update({
            "HOME": directory,
            "TMPDIR": directory,
            "TERM": "dumb",
            "VAULT_ADDR": addr,
            "VAULT_TOKEN": token,
            "VAULT_KV_VERSION": str(version),
            "VAULT_KV_MOUNTS": mounts,
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
        })
        if env_extra:
            env.update(env_extra)
        completed = subprocess.run(
            [str(VCLI), "-n"],
            input=(command + "\n") if command else "",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            env=env,
        )
        history_file = Path(directory) / ".vclistory"
        history = history_file.read_text() if history_file.exists() else ""
        return completed, history


class RealVaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("VCLI_TEST_READONLY_TOKEN") is None:
            raise RuntimeError("Run with bash tests/integration/run.sh, not against any external Vault")
        cls.prefix = "vcli-ci-" + uuid4().hex[:12]
        if not VCLI.exists():
            raise RuntimeError(f"Patched script not found at {VCLI}")

    def test_01_version_without_credentials(self):
        env = os.environ.copy()
        env.pop("VAULT_TOKEN", None)
        out = subprocess.run([str(VCLI), "--version"], env=env, capture_output=True,
                             text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("0.6.3-revival", out.stdout)

    def test_02_readonly_token_can_start_without_mutation(self):
        ro = os.environ["VCLI_TEST_READONLY_TOKEN"]
        result, _ = invoke("", token=ro, version=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(ro, result.stdout + result.stderr)
        # Original VCLI wrote secret/-_test_- on startup. It must never return.
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", "secret/data/-_test_-")
        self.assertEqual(ctx.exception.code, 404)

    def test_03_kv1_put_cat_list_and_delete(self):
        path = f"kv1/{self.prefix}/kv1"
        output, history = invoke(f'put {path} {{"answer":"v1"}}')
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        self.assertEqual(api("GET", path)["data"], {"answer": "v1"})
        self.assertNotIn("answer", history)
        output, _ = invoke(f"cat {path}")
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        self.assertEqual(json.loads(output.stdout)["data"]["answer"], "v1")
        listing, _ = invoke(f"ls kv1/{self.prefix}/")
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        self.assertIn("kv1", listing.stdout)
        deleted, _ = invoke(f"rm {path}")
        self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", path)
        self.assertEqual(ctx.exception.code, 404)

    def test_04_kv2_put_cat_list_soft_delete(self):
        leaf = f"{self.prefix}/nested/kv2"
        legacy_path = f"kv2/{leaf}"
        output, _ = invoke(f'put {legacy_path} {{"answer":"v2"}}', version=2)
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
        self.assertEqual(api("GET", f"kv2/data/{leaf}")["data"]["data"], {"answer": "v2"})
        result, _ = invoke(f"cat {legacy_path}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], {"answer": "v2"})
        listing, _ = invoke(f"ls kv2/{self.prefix}/nested/", version=2)
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        self.assertIn("kv2", listing.stdout)
        deleted, _ = invoke(f"rm {legacy_path}", version=2)
        self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", f"kv2/data/{leaf}")
        self.assertEqual(ctx.exception.code, 404)
        metadata = api("GET", f"kv2/metadata/{leaf}")
        self.assertGreaterEqual(metadata["data"]["current_version"], 1)

    def test_05_kv2_nested_mount_and_mixed_routing(self):
        leaf = f"{self.prefix}/nested-mount"
        path = f"team/config/{leaf}"
        result, _ = invoke(f'put {path} {{"nested":true}}', version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(api("GET", f"team/config/data/{leaf}")["data"]["data"]["nested"])
        result, _ = invoke(f"cat {path}", version=2)
        self.assertEqual(json.loads(result.stdout)["data"]["nested"], True)

    def test_06_update_fields_copy_and_move(self):
        a = f"kv1/{self.prefix}/edit"
        b = f"kv1/{self.prefix}/copy"
        c = f"kv1/{self.prefix}/moved"
        for cmd in (f'put {a} {{"x":"one"}}',
                    f'addsecret {a} {{"y":"two"}}',
                    f'delsecret {a} x',
                    f'cp {a} {b}',
                    f'mv {b} {c}'):
            result, _ = invoke(cmd)
            self.assertEqual(result.returncode, 0, f"{cmd}: {result.stdout} {result.stderr}")
        self.assertEqual(api("GET", a)["data"], {"y": "two"})
        self.assertEqual(api("GET", c)["data"], {"y": "two"})
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", b)
        self.assertEqual(ctx.exception.code, 404)

    def test_07_acl_policy_and_approle_read(self):
        for cmd, text in (("lspol", "vcli-ci-ro"),
                          ("showpol vcli-ci-ro", "kv1/"),
                          ("lsrole", "ci-role"),
                          ("showrole ci-role", "default")):
            with self.subTest(command=cmd):
                result, _ = invoke(cmd)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(text, result.stdout)

    def test_08_denied_write_cannot_mutate(self):
        leaf = f"{self.prefix}/denied"
        ro = os.environ["VCLI_TEST_READONLY_TOKEN"]
        result, _ = invoke(f'put kv2/{leaf} {{"should_not":"exist"}}',
                           version=2, token=ro)
        # Assert by the server, independently of exit behavior.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", f"kv2/data/{leaf}")
        self.assertEqual(ctx.exception.code, 404)
        self.assertNotIn(ro, result.stdout + result.stderr)

    def test_09_wrong_token_cannot_read(self):
        path = f"kv1/{self.prefix}/auth"
        api("POST", path, {"secret": "no-leak"})
        result, _ = invoke(f"cat {path}", token="ci-invalid-token")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("no-leak", result.stdout + result.stderr)
        self.assertNotIn("ci-invalid-token", result.stdout + result.stderr)

    def test_10_history_and_tempfile_privacy(self):
        path = f"kv1/{self.prefix}/history"
        result, history = invoke(f'put {path} {{"password":"canary-123"}}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("canary-123", history)
        self.assertNotIn(ROOT_TOKEN, result.stdout + result.stderr)
        # Secret is deliberately allowed in Vault, not in local history/logs.
        self.assertEqual(api("GET", path)["data"]["password"], "canary-123")


if __name__ == "__main__":
    unittest.main()
