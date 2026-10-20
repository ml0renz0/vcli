"""CLI-first functional acceptance tests for legacy administrative operations.

Only ephemeral, uniquely prefixed secrets and mounts in disposable Vault.
All effects are checked directly through the real Vault API.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import tarfile
import unittest
from urllib import error
from uuid import uuid4

from .test_vault import VCLI, VAULT, ROOT_TOKEN, api, invoke


class ExtendedVaultContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("VCLI_TEST_READONLY_TOKEN") or not VAULT.startswith("https://"):
            raise RuntimeError("Only run with the ephemeral TLS Vault integration fixture")
        cls.prefix = "vcli-more-" + uuid4().hex[:12]

    def put(self, leaf: str, content: dict) -> None:
        api("POST", f"kv1/{self.prefix}/{leaf}", content)

    def stored(self, leaf: str) -> dict:
        return api("GET", f"kv1/{self.prefix}/{leaf}")["data"]

    def not_found(self, leaf: str) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            self.stored(leaf)
        self.assertEqual(ctx.exception.code, 404)

    def command(self, line: str, **kwargs):
        result, _ = invoke(line, **kwargs)
        self.assertEqual(result.returncode, 0, f"{line!r}: {result.stdout}\n{result.stderr}")
        return result.stdout

    def test_01_create_and_unmount_ephemeral_kv_engine(self):
        # Tests the *public CLI commands*, not direct Vault setup calls.
        mount = "vcli-extra-" + uuid4().hex[:12]
        try:
            self.command(f"mount {mount}")
            settings = api("GET", f"sys/mounts/{mount}")
            self.assertEqual(settings["data"]["type"], "kv")
            self.command(f"umount {mount}")
            with self.assertRaises(error.HTTPError) as ctx:
                api("GET", f"sys/mounts/{mount}")
            self.assertEqual(ctx.exception.code, 400 if ctx.exception.code == 400 else 404)
        finally:
            # Do not leak a mount if a test assertion fails.
            try:
                api("DELETE", f"sys/mounts/{mount}")
            except error.HTTPError:
                pass

    def test_02_tree_and_find_nested_matches(self):
        self.put("browse/alpha", {"marker": "match"})
        self.put("browse/branch/beta", {"marker": "different"})
        tree = self.command(f"tree kv1/{self.prefix}/browse")
        self.assertIn("alpha", tree)
        self.assertIn("beta", tree)
        matches = self.command(f'find kv1/{self.prefix}/browse {{"marker":"match"}}')
        self.assertIn("alpha", matches)
        self.assertNotIn("beta", matches)

    def test_03_replace_changes_only_matching_secrets(self):
        self.put("replace/a", {"state": "old"})
        self.put("replace/deep/b", {"state": "old"})
        self.put("replace/deep/keep", {"state": "other"})
        self.command(
            f'replace kv1/{self.prefix}/replace '
            '{"state":"old"} {"state":"new"}'
        )
        self.assertEqual(self.stored("replace/a"), {"state": "new"})
        self.assertEqual(self.stored("replace/deep/b"), {"state": "new"})
        self.assertEqual(self.stored("replace/deep/keep"), {"state": "other"})

    def test_04_cptree_preserves_hierarchy_and_source(self):
        self.put("source/alpha", {"id": "alpha"})
        self.put("source/nested/beta", {"id": "beta"})
        self.command(f"cptree kv1/{self.prefix}/source kv1/{self.prefix}/dest")
        self.assertEqual(self.stored("dest/alpha"), {"id": "alpha"})
        self.assertEqual(self.stored("dest/nested/beta"), {"id": "beta"})
        self.assertEqual(self.stored("source/alpha"), {"id": "alpha"})
        self.assertEqual(self.stored("source/nested/beta"), {"id": "beta"})

    def test_05_rmtree_deletes_only_selected_subtree(self):
        self.put("delete-me/one", {"id": 1})
        self.put("delete-me/deeper/two", {"id": 2})
        self.put("do-not-delete/sentinel", {"safe": True})
        self.command(f"rmtree -f kv1/{self.prefix}/delete-me")
        self.not_found("delete-me/one")
        self.not_found("delete-me/deeper/two")
        self.assertEqual(self.stored("do-not-delete/sentinel"), {"safe": True})

    def test_06_backup_restore_roundtrip_without_external_paths(self):
        self.put("backup/a", {"value": "first"})
        self.put("backup/sub/b", {"value": "second"})
        with TemporaryDirectory(prefix="vcli-backup-") as tmp:
            env = os.environ.copy()
            env.update({"HOME": tmp, "TMPDIR": tmp, "TERM": "dumb",
                        "VAULT_ADDR": VAULT, "VAULT_TOKEN": ROOT_TOKEN,
                        "VAULT_CACERT": os.environ["VAULT_CACERT"],
                        "VAULT_KV_VERSION": "1"})
            backup = subprocess.run(
                [str(VCLI), "-n"], input=f"backup kv1/{self.prefix}/backup\n",
                text=True, capture_output=True, env=env, timeout=20
            )
            self.assertEqual(backup.returncode, 0, backup.stdout + backup.stderr)
            archives = list(Path(tmp).glob("backup_vault*.tgz"))
            self.assertEqual(len(archives), 1, backup.stdout)
            with tarfile.open(archives[0], "r:gz") as tar:
                regular = [m for m in tar if m.isfile()]
                self.assertGreaterEqual(len(regular), 2)
                self.assertTrue(all(not m.name.startswith("/") and
                                    ".." not in Path(m.name).parts for m in regular))
            api("DELETE", f"kv1/{self.prefix}/backup/a")
            api("DELETE", f"kv1/{self.prefix}/backup/sub/b")
            restore = subprocess.run(
                [str(VCLI), "-n"], input=f"restore {archives[0]}\n",
                text=True, capture_output=True, env=env, timeout=20
            )
            self.assertEqual(restore.returncode, 0, restore.stdout + restore.stderr)
        self.assertEqual(self.stored("backup/a"), {"value": "first"})
        self.assertEqual(self.stored("backup/sub/b"), {"value": "second"})

    def test_07_missing_secret_returns_nonzero(self):
        missing = f"kv1/{self.prefix}/no-such-secret"
        result, _ = invoke(f"cat {missing}")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(ROOT_TOKEN, result.stdout + result.stderr)

    def test_08_restricted_token_cannot_mount_engine(self):
        ro = os.environ["VCLI_TEST_READONLY_TOKEN"]
        mount = "vcli-no-create-" + uuid4().hex[:12]
        result, _ = invoke(f"mount {mount}", token=ro)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(ro, result.stdout + result.stderr)
        with self.assertRaises(error.HTTPError) as ctx:
            api("GET", f"sys/mounts/{mount}")
        self.assertIn(ctx.exception.code, (400, 404))

    def test_09_relative_cd_and_multiple_batch_commands(self):
        self.put("navigation/child", {"reachable": True})
        result, _ = invoke(f"cd kv1/{self.prefix}/navigation\ncat child")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"reachable"', result.stdout)
        self.assertIn("true", result.stdout)


if __name__ == "__main__":
    unittest.main()
