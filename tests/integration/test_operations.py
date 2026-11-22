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
            self.assertIn(ctx.exception.code, (400, 404))
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
        # A directory-style trailing slash must not create // in child LIST URLs.
        trailing = self.command(f"tree kv1/{self.prefix}/browse/")
        self.assertIn("alpha", trailing)
        self.assertIn("beta", trailing)
        matches = self.command(f'find kv1/{self.prefix}/browse {{"marker":"match"}}')
        self.assertIn("alpha", matches)
        self.assertNotIn("beta", matches)

    def test_16_root_tree_lists_actual_mounts(self):
        """ACC-01: assert the *complete* root inventory, not three substrings.

        The Vault sys/mounts API is the independent source of truth; comparing
        just tree against ls could let two equally broken commands pass.
        """
        mounts = api("GET", "sys/mounts")["data"]
        self.assertIsInstance(mounts, dict)
        self.assertGreaterEqual(len(mounts), 3)
        expected = set(mounts)
        self.assertIn("cubbyhole/", expected)

        def exact_root_entries(output):
            lines = output.splitlines()
            self.assertTrue(lines and lines[0] == "/", output)
            self.assertEqual(len(lines), len(expected) + 1, output)
            self.assertTrue(all(line.lstrip().startswith("` ") for line in lines[1:]), output)
            entries = [line.rsplit(None, 1)[-1] for line in lines[1:]]
            self.assertEqual(len(entries), len(set(entries)), "Duplicate mounts in root tree")
            self.assertEqual(set(entries), expected, "Tree omits or invents mountpoints")
            return entries

        explicit = self.command("tree /")
        implicit = self.command("tree")
        self.assertEqual(exact_root_entries(explicit), exact_root_entries(implicit))
        listing = self.command("ls /")
        self.assertEqual(set(listing.splitlines()), expected, "ls and API disagree")


    def test_17_list_api_authority_for_empty_and_populated_mounts(self):
        """Vault's raw LIST response, not vcli, decides expected status."""
        kv1 = "vcli-empty-" + uuid4().hex[:12]
        kv2 = "vcli-empty2-" + uuid4().hex[:12]
        api("POST", f"sys/mounts/{kv1}", {"type": "kv", "options": {"version": "1"}})
        api("POST", f"sys/mounts/{kv2}", {"type": "kv", "options": {"version": "2"}})
        try:
            mounts = api("GET", "sys/mounts")["data"]
            for name in (kv1, kv2, "cubbyhole"):
                self.assertIn(name + "/", mounts)

            for path, raw_endpoint in (
                (kv1, f"{kv1}/"),
                (kv2, f"{kv2}/metadata/"),
                ("cubbyhole", "cubbyhole/"),
            ):
                with self.subTest(empty=path):
                    with self.assertRaises(error.HTTPError) as ctx:
                        api("LIST", raw_endpoint)
                    self.assertEqual(ctx.exception.code, 404)
                    for command in ("ls", "tree"):
                        output, _ = invoke(f"{command} {path}",
                                           version=2 if path == kv2 else 1,
                                           mounts=f"kv2,team/config,{kv2}")
                        self.assertNotEqual(output.returncode, 0,
                                            f"{command} falsely reported success: {output.stdout}")
                        if command == "tree":
                            self.assertEqual(output.stdout, "",
                                             "tree must not draw a successful empty mount")

            # Positive controls: the identical endpoints respond 200 when populated.
            api("POST", f"{kv1}/sentinel", {"present": True})
            api("POST", f"{kv2}/data/sentinel", {"data": {"present": True}})
            for path, raw_endpoint in (
                (kv1, f"{kv1}/"), (kv2, f"{kv2}/metadata/")
            ):
                with self.subTest(populated=path):
                    self.assertEqual(api("LIST", raw_endpoint)["data"]["keys"], ["sentinel"])
                    for command in ("ls", "tree"):
                        output = self.command(f"{command} {path}",
                                              version=2 if path == kv2 else 1,
                                              mounts=f"kv2,team/config,{kv2}")
                        self.assertIn("sentinel", output)

            for command in ("ls", "tree"):
                failed, _ = invoke(f"{command} {kv1}/absent")
                self.assertNotEqual(failed.returncode, 0)
        finally:
            api("DELETE", f"sys/mounts/{kv2}")
            api("DELETE", f"sys/mounts/{kv1}")

    def test_18_mount_inventory_reports_empty_populated_and_non_kv(self):
        empty = "vcli-occupancy-" + uuid4().hex[:12]
        api("POST", f"sys/mounts/{empty}", {"type": "kv", "options": {"version": "1"}})
        try:
            listing = self.command("mount")
            self.assertIn("first-level content only", listing)
            self.assertRegex(listing, rf"(?m)^{empty}/\s+kv\s+empty\s*$")
            self.put("occupancy/sentinel", {"present": True})
            populated = self.command("mount")
            self.assertRegex(populated, r"(?m)^kv1/\s+kv\s+contains entries\s*$")
            self.assertRegex(populated, r"(?m)^sys/\s+system\s+not a KV secret mount\s*$")
        finally:
            api("DELETE", f"sys/mounts/{empty}")

    def test_19_mount_inventory_checks_kv2_metadata_without_client_configuration(self):
        """ACC-08: the Vault mount version, not VAULT_KV_MOUNTS, selects LIST."""
        dynamic = "vcli-occupancy2-" + uuid4().hex[:12]
        api("POST", f"sys/mounts/{dynamic}",
            {"type": "kv", "options": {"version": "2"}})
        try:
            mount_info = api("GET", "sys/mounts")["data"][dynamic + "/"]
            self.assertEqual(mount_info["options"]["version"], "2")
            with self.assertRaises(error.HTTPError) as empty_api:
                api("LIST", f"{dynamic}/metadata/")
            self.assertEqual(empty_api.exception.code, 404)
            # Deliberately omit the new mount from the client's KV2 mapping.
            empty, _ = invoke("mount", version=1, mounts="kv2,team/config")
            self.assertEqual(empty.returncode, 0, empty.stdout + empty.stderr)
            self.assertRegex(empty.stdout, rf"(?m)^{dynamic}/\s+kv\s+empty\s*$")
            api("POST", f"{dynamic}/data/leaf",
                {"data": {"occupancy_private": "do-not-print"}})
            self.assertEqual(api("LIST", f"{dynamic}/metadata/")["data"]["keys"], ["leaf"])
            filled, _ = invoke("mount", version=1, mounts="kv2,team/config")
            self.assertEqual(filled.returncode, 0, filled.stdout + filled.stderr)
            self.assertRegex(filled.stdout, rf"(?m)^{dynamic}/\s+kv\s+contains entries\s*$")
            self.assertNotIn("occupancy_private", filled.stdout)
            self.assertNotIn("do-not-print", filled.stdout)
        finally:
            api("DELETE", f"sys/mounts/{dynamic}")

    def test_10_find_compares_json_values_without_whitespace_collapse(self):
        """JSON string whitespace and nested object key order must be respected."""
        self.put("find-json/exact", {
            "nested": {"b": 2, "a": 1},
            "value": "a  b",
        })
        self.put("find-json/different", {
            "nested": {"a": 1, "b": 2},
            "value": "a b",
        })
        output = self.command(
            f'find kv1/{self.prefix}/find-json '
            '{"value":"a\\u0020\\u0020b","nested":{"a":1,"b":2}}'
        )
        self.assertIn("exact", output)
        self.assertNotIn("different", output)

    def test_11_find_invalid_json_is_rejected(self):
        result, _ = invoke(f"find kv1/{self.prefix}/find-json not-json")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

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



    def test_12_absolute_cd_without_slash_reads_relative_secret(self):
        self.put("nav-absolute/child", {"marker": "absolute-cd"})
        result, _ = invoke(f"cd /kv1/{self.prefix}/nav-absolute\ncat child")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"absolute-cd"', result.stdout)

    def test_13_missing_cd_reports_failure(self):
        result, _ = invoke(f"cd /kv1/{self.prefix}/missing-nav")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("cannot list directory", result.stderr)

    def test_14_cd_parent_restores_relative_secret_path(self):
        self.put("nav-parent/child", {"marker": "parent-cd"})
        result, _ = invoke(
            f"cd kv1/{self.prefix}/nav-parent\ncd ..\ncat nav-parent/child"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"parent-cd"', result.stdout)


    def test_15_ls_and_tree_relative_to_absolute_cd(self):
        self.put("nav-relative-list/alpha", {"marker": "listing-visible"})
        self.put("nav-relative-list/branch/beta", {"marker": "nested-visible"})
        command = (
            f"cd /kv1/{self.prefix}/nav-relative-list\n"
            "ls\n"
            "ls branch\n"
            "tree branch\n"
            "cat branch/beta"
        )
        result, _ = invoke(command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("branch/", result.stdout)
        self.assertIn("beta", result.stdout)
        self.assertIn('"nested-visible"', result.stdout)

if __name__ == "__main__":
    unittest.main()
