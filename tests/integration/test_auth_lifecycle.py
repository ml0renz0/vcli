"""Real Vault authentication lifecycle acceptance contracts.

Exercises the production Bash process only. The independent REST oracle
creates short-lived roles/tokens in the throwaway TLS Vault fixture.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from urllib import error
from uuid import uuid4

from .test_vault import VAULT, ROOT_TOKEN, api, invoke


class AuthLifecycleContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.environ.get("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("Requires ephemeral TLS Vault fixture")
        cls.prefix = "vcli-auth-" + uuid4().hex[:12]
        cls.role = "vcli-auth-role-" + uuid4().hex[:10]
        cls.policy = "vcli-auth-policy-" + uuid4().hex[:10]
        cls.leaf = f"kv1/{cls.prefix}/allowed"
        api("POST", cls.leaf, {"can": "read"})
        api("PUT", f"sys/policies/acl/{cls.policy}", {
            "policy": f'path "{cls.leaf}" {{ capabilities = ["read"] }}'
        })
        api("POST", f"auth/approle/role/{cls.role}", {
            "token_policies": [cls.policy], "token_no_default_policy": True,
            "token_ttl": "5m", "token_max_ttl": "10m"
        })
        cls.role_id = api("GET", f"auth/approle/role/{cls.role}/role-id")["data"]["role_id"]
        cls.secret_id = api("POST", f"auth/approle/role/{cls.role}/secret-id")["data"]["secret_id"]

    def invoke_approle(self, cmd, **kwargs):
        return invoke(cmd, env_extra={
            "VAULT_TOKEN": "",
            "VAULT_ROLE_ID": self.role_id,
            "VAULT_SECRET_ID": self.secret_id,
            **kwargs,
        })

    def test_01_approle_login_reads_allowed_secret(self):
        result, history = self.invoke_approle(f"cat {self.leaf}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"can"', result.stdout)
        self.assertNotIn(self.secret_id, result.stdout + result.stderr + history)
        self.assertNotIn(self.role_id, result.stdout + result.stderr + history)

    def test_02_approle_token_obeys_read_only_acl(self):
        blocked = f"kv1/{self.prefix}/blocked"
        result, history = self.invoke_approle(f'put {blocked} {{"forbidden":true}}')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.secret_id, result.stdout + result.stderr + history)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", blocked)
        self.assertEqual(ctx.exception.code, 404)

    def test_03_explicit_token_takes_precedence_over_approle(self):
        result, history = invoke(
            f"cat {self.leaf}",
            token=ROOT_TOKEN,
            env_extra={"VAULT_ROLE_ID": "not-a-role", "VAULT_SECRET_ID": "bad-secret-id"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"can"', result.stdout)
        self.assertNotIn(ROOT_TOKEN, result.stdout + result.stderr + history)

    def test_04_incomplete_approle_credentials_fail_without_network_data(self):
        result, history = invoke(
            f"cat {self.leaf}",
            env_extra={"VAULT_TOKEN": "", "VAULT_ROLE_ID": self.role_id, "VAULT_SECRET_ID": ""},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.role_id, result.stdout + result.stderr + history)

    def test_05_wrong_secret_id_rejected_without_leaks(self):
        bogus = "invalid-secret-" + uuid4().hex
        result, history = invoke(
            f"cat {self.leaf}",
            env_extra={"VAULT_TOKEN": "", "VAULT_ROLE_ID": self.role_id, "VAULT_SECRET_ID": bogus},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(bogus, result.stdout + result.stderr + history)
        self.assertNotIn(self.role_id, result.stdout + result.stderr + history)
        self.assertNotIn('"can"', result.stdout)

    def test_06_revoked_token_fails_without_modifying_vault(self):
        created = api("POST", "auth/token/create", {"ttl": "1m"})["auth"]["client_token"]
        api("POST", "auth/token/revoke", {"token": created})
        result, history = invoke(f'put kv1/{self.prefix}/revoked {{"unsafe":true}}', token=created)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(created, result.stdout + result.stderr + history)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", f"kv1/{self.prefix}/revoked")
        self.assertEqual(ctx.exception.code, 404)

    def test_07_expired_token_fails_closed(self):
        created = api("POST", "auth/token/create", {
            "ttl": "2s", "explicit_max_ttl": "2s"
        })["auth"]["client_token"]
        time.sleep(3)
        result, history = invoke(f'put kv1/{self.prefix}/expired {{"unsafe":true}}', token=created)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(created, result.stdout + result.stderr + history)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", f"kv1/{self.prefix}/expired")
        self.assertEqual(ctx.exception.code, 404)

    def test_08_explicit_renew_self_uses_current_token(self):
        created = api("POST", "auth/token/create", {
            "ttl": "10s", "explicit_max_ttl": "2m", "renewable": True
        })["auth"]["client_token"]
        try:
            result, history = invoke("renew-token 45s", token=created)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("renewed", result.stdout.lower())
            self.assertNotIn(created, result.stdout + result.stderr + history)
            status = api("POST", "auth/token/lookup", {"token": created})["data"]
            self.assertGreater(status["ttl"], 10)
        finally:
            api("POST", "auth/token/revoke", {"token": created})

    def test_09_nonrenewable_token_cannot_be_renewed(self):
        created = api("POST", "auth/token/create", {
            "ttl": "1m", "renewable": False
        })["auth"]["client_token"]
        try:
            result, history = invoke("renew-token 45s", token=created)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(created, result.stdout + result.stderr + history)
            info = api("POST", "auth/token/lookup", {"token": created})["data"]
            self.assertFalse(info["renewable"])
        finally:
            api("POST", "auth/token/revoke", {"token": created})


    def run_timed_session(self, first, second, token, *, pause=5.5):
        """Keep ONE Bash process alive past a short original token TTL.

        The first command is flushed to stdin while the process is running.
        After the pause the same process executes the second command. This
        catches a CLI that quietly restarts/re-authenticates for each command.
        """
        with tempfile.TemporaryDirectory(prefix="vcli-session-auth-") as home:
            env = os.environ.copy()
            env.update({
                "HOME": home, "TMPDIR": home, "TERM": "dumb",
                "VAULT_ADDR": VAULT, "VAULT_TOKEN": token,
                "VAULT_CACERT": os.environ["VAULT_CACERT"],
                "VAULT_KV_VERSION": "1",
                "VAULT_ROLE_ID": "", "VAULT_SECRET_ID": "",
            })
            proc = subprocess.Popen(
                [str(Path(__file__).resolve().parents[2] / "vcli-revival.sh"), "-n"],
                env=env, text=True, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                proc.stdin.write(first + "\n")
                proc.stdin.flush()
                # Wait for completion of the first command before measuring
                # the post-renewal lifetime; runner startup speed is variable.
                import select
                import os as _os
                fd = proc.stdout.fileno()
                ready, _, _ = select.select([fd], [], [], 8)
                if not ready:
                    raise AssertionError("First session command produced no output")
                first_output = _os.read(fd, 65536).decode()
                self.assertIn("Vault token renewed", first_output)
                time.sleep(pause)
                proc.stdin.write(second + "\n")
                proc.stdin.close()
                proc.stdin = None
                stdout, stderr = proc.communicate(timeout=20)
                stdout = first_output + stdout
                history_path = Path(home) / ".vclistory"
                history = history_path.read_text() if history_path.exists() else ""
                self.assertNotIn(token, stdout + stderr + history)
                return proc.returncode, stdout, stderr, history
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=5)

    def test_10_renewed_token_survives_original_ttl_in_same_session(self):
        created = api("POST", "auth/token/create", {
            "ttl": "8s", "explicit_max_ttl": "45s", "renewable": True,
            "policies": [self.policy], "no_default_policy": True,
        })["auth"]["client_token"]
        try:
            status, stdout, stderr, _ = self.run_timed_session(
                "renew-token 25s", f"cat {self.leaf}", created, pause=9.5
            )
            self.assertEqual(status, 0, stdout + stderr)
            self.assertIn("Vault token renewed", stdout)
            self.assertIn('"can"', stdout)
            lookup = api("POST", "auth/token/lookup", {"token": created})["data"]
            self.assertGreater(lookup["ttl"], 4)
            self.assertIn(self.policy, lookup["policies"])
        finally:
            api("POST", "auth/token/revoke", {"token": created})

    def test_11_unrenewed_token_expires_between_commands_in_same_session(self):
        created = api("POST", "auth/token/create", {
            "ttl": "4s", "explicit_max_ttl": "4s",
            "policies": [self.policy], "no_default_policy": True,
        })["auth"]["client_token"]
        code, stdout, stderr, _ = self.run_timed_session(
            f"cat {self.leaf}", f"cat {self.leaf}", created
        )
        self.assertNotEqual(code, 0, stdout + stderr)
        self.assertEqual(stdout.count('"can"'), 1, stdout + stderr)
        with self.assertRaises(error.HTTPError) as caught:
            api("POST", "auth/token/lookup", {"token": created})
        self.assertIn(caught.exception.code, (400, 403, 404))

    def test_12_failed_renewal_does_not_interrupt_later_read(self):
        created = api("POST", "auth/token/create", {
            "ttl": "2m", "renewable": False,
            "policies": [self.policy], "no_default_policy": True,
        })["auth"]["client_token"]
        try:
            out, history = invoke(f"renew-token 30s\ncat {self.leaf}", token=created)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn('"can"', out.stdout)
            self.assertNotIn("Vault token renewed", out.stdout)
            self.assertNotIn(created, out.stdout + out.stderr + history)
        finally:
            api("POST", "auth/token/revoke", {"token": created})

    def test_13_invalid_renew_increment_fails_without_ending_session(self):
        created = api("POST", "auth/token/create", {
            "ttl": "2m", "renewable": True,
            "policies": [self.policy], "no_default_policy": True,
        })["auth"]["client_token"]
        try:
            out, history = invoke(f"renew-token invalid\ncat {self.leaf}", token=created)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("invalid renewal increment", out.stderr)
            self.assertIn('"can"', out.stdout)
            self.assertNotIn(created, out.stdout + out.stderr + history)
        finally:
            api("POST", "auth/token/revoke", {"token": created})


if __name__ == "__main__":
    unittest.main()
