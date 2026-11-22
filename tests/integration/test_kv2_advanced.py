"""Black-box advanced KV v2 acceptance tests against native-TLS Vault."""
import os
import unittest
from urllib import error
from uuid import uuid4
from .test_vault import api, invoke, VAULT

class KV2AdvancedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.getenv("VCLI_TEST_READONLY_TOKEN") or not VAULT.startswith("https://"):
            raise RuntimeError("Must run against disposable TLS Vault")
        cls.p = "vcli-kv2-advanced-" + uuid4().hex[:12]

    def put(self, leaf, value, mount="kv2"):
        api("POST", f"{mount}/data/{self.p}/{leaf}", {"data": value})

    def get(self, leaf, mount="kv2"):
        return api("GET", f"{mount}/data/{self.p}/{leaf}")["data"]["data"]

    def meta(self, leaf, mount="kv2"):
        return api("GET", f"{mount}/metadata/{self.p}/{leaf}")["data"]

    def cli(self, cmd, success=True):
        output, _ = invoke(cmd, version=2)
        self.assertEqual(output.returncode == 0, success, cmd+"\n"+output.stdout+output.stderr)

    def test_01_replace_creates_versions_only_for_matching_secrets(self):
        self.put("replace/a", {"state":"old"})
        self.put("replace/deep/b", {"state":"old"})
        self.put("replace/deep/sentinel", {"state":"keep"})
        self.cli(f'replace kv2/{self.p}/replace {{"state":"old"}} {{"state":"new"}}')
        self.assertEqual(self.get("replace/a"), {"state":"new"})
        self.assertEqual(self.get("replace/deep/b"), {"state":"new"})
        self.assertEqual(self.meta("replace/a")["current_version"], 2)
        self.assertEqual(self.meta("replace/deep/b")["current_version"], 2)
        self.assertEqual(self.get("replace/deep/sentinel"), {"state":"keep"})
        self.assertEqual(self.meta("replace/deep/sentinel")["current_version"], 1)

    def test_02_recursive_copy_preserves_nested_data_and_source_versions(self):
        self.put("copy/src/a", {"x":1})
        self.put("copy/src/deep/b", {"x":2})
        self.cli(f"cptree kv2/{self.p}/copy/src kv2/{self.p}/copy/dest")
        for leaf, val in (("a",1),("deep/b",2)):
            self.assertEqual(self.get("copy/dest/"+leaf), {"x":val})
            self.assertEqual(self.get("copy/src/"+leaf), {"x":val})
            self.assertEqual(self.meta("copy/src/"+leaf)["current_version"], 1)

    def test_03_recursive_delete_is_soft_and_keeps_other_paths(self):
        self.put("remove/a", {"x":1})
        self.put("remove/deep/b", {"x":2})
        self.put("keep/sentinel", {"safe":True})
        self.cli(f"rmtree -f kv2/{self.p}/remove")
        for leaf in ("remove/a","remove/deep/b"):
            with self.subTest(leaf=leaf):
                with self.assertRaises(error.HTTPError) as caught:
                    self.get(leaf)
                self.assertEqual(caught.exception.code,404)
                self.assertEqual(self.meta(leaf)["current_version"],1)
                self.assertTrue(self.meta(leaf)["versions"]["1"]["deletion_time"])
        self.assertEqual(self.get("keep/sentinel"),{"safe":True})

    def test_04_copy_collision_does_not_partially_mutate_destination(self):
        self.put("collision/src/a", {"id":"a"})
        self.put("collision/src/z", {"id":"z"})
        self.put("collision/dest/z", {"untouched":True})
        self.cli(f"cptree kv2/{self.p}/collision/src kv2/{self.p}/collision/dest",False)
        with self.assertRaises(error.HTTPError) as caught:
            self.get("collision/dest/a")
        self.assertEqual(caught.exception.code,404)
        self.assertEqual(self.get("collision/dest/z"),{"untouched":True})
        self.assertEqual(self.meta("collision/dest/z")["current_version"],1)

    def test_05_copy_rejects_soft_deleted_destination(self):
        self.put("resurrect/src/a", {"source":True})
        self.put("resurrect/dest/a", {"original":True})
        api("DELETE",f"kv2/data/{self.p}/resurrect/dest/a")
        self.cli(f"cptree kv2/{self.p}/resurrect/src kv2/{self.p}/resurrect/dest",False)
        with self.assertRaises(error.HTTPError) as caught:
            self.get("resurrect/dest/a")
        self.assertEqual(caught.exception.code,404)
        self.assertEqual(self.meta("resurrect/dest/a")["current_version"],1)

    def test_06_cross_mount_nested_copy(self):
        self.put("cross/src/a",{"x":1})
        self.put("cross/src/deep/b",{"x":2})
        self.cli(f"cptree kv2/{self.p}/cross/src team/config/{self.p}/cross/dest")
        self.assertEqual(self.get("cross/dest/a",mount="team/config"),{"x":1})
        self.assertEqual(self.get("cross/dest/deep/b",mount="team/config"),{"x":2})

    def test_11_cptree_preflight_checks_nested_mount_metadata_before_writing(self):
        """An existing target under team/config aborts the *entire* copy."""
        source = "kv2/" + self.p + "/overlap-copy/src"
        target = "team/config/" + self.p + "/overlap-copy/dest"
        self.put("overlap-copy/src/a", {"candidate": "a"})
        self.put("overlap-copy/src/z", {"candidate": "z"})
        self.put("overlap-copy/dest/z", {"existing": True}, mount="team/config")

        result, _ = invoke(
            f"cptree {source} {target}",
            version=2,
            mounts="team,team/config,kv2",
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Destination already exists", result.stdout + result.stderr)
        with self.assertRaises(error.HTTPError) as caught:
            self.get("overlap-copy/dest/a", mount="team/config")
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(
            self.get("overlap-copy/dest/z", mount="team/config"),
            {"existing": True},
        )
        self.assertEqual(
            self.meta("overlap-copy/dest/z", mount="team/config")["current_version"],
            1,
        )

    def test_12_cptree_refuses_soft_deleted_nested_mount_target(self):
        """Soft-deleted KV v2 metadata still counts as occupied."""
        source = "kv2/" + self.p + "/overlap-soft/src"
        target = "team/config/" + self.p + "/overlap-soft/dest"
        self.put("overlap-soft/src/a", {"candidate": "a"})
        self.put("overlap-soft/src/z", {"candidate": "z"})
        self.put("overlap-soft/dest/z", {"previous": True}, mount="team/config")
        api("DELETE", "team/config/data/" + self.p + "/overlap-soft/dest/z")

        result, _ = invoke(
            f"cptree {source} {target}",
            version=2,
            mounts="team,team/config,kv2",
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Destination already exists", result.stdout + result.stderr)
        for leaf in ("a", "z"):
            with self.subTest(leaf=leaf):
                with self.assertRaises(error.HTTPError) as caught:
                    self.get("overlap-soft/dest/" + leaf, mount="team/config")
                self.assertEqual(caught.exception.code, 404)
        self.assertEqual(
            self.meta("overlap-soft/dest/z", mount="team/config")["current_version"],
            1,
        )

    def test_09_nested_mount_wins_over_prefix_independent_of_order(self):
        """A configured 'team' prefix must not steal team/config requests."""
        leaf = "routing/longest-prefix"
        secret = f"team/config/{self.p}/{leaf}"
        self.put(leaf, {"initial": True}, mount="team/config")
        for mounts in ("team,team/config,kv2", "team/config,team,kv2"):
            with self.subTest(mounts=mounts):
                read, _ = invoke(f"cat {secret}", version=2, mounts=mounts)
                self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
                import json
                self.assertEqual(json.loads(read.stdout)["data"], {"initial": True})
                listing, _ = invoke(
                    f"ls team/config/{self.p}/routing", version=2, mounts=mounts
                )
                self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
                self.assertIn("longest-prefix", listing.stdout)

        updated, _ = invoke(
            f'put {secret} {{"updated":true}}',
            version=2, mounts="team,team/config,kv2",
        )
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertEqual(self.get(leaf, mount="team/config"), {"updated": True})
        self.assertEqual(self.meta(leaf, mount="team/config")["current_version"], 2)

    def test_10_nested_mount_data_prefix_is_logical_not_raw_api(self):
        """data/ is a user folder; raw API endpoints are internal only."""
        import json
        leaf = "data/" + self.p + "/routing/raw-path"
        # Seed exactly the requested logical path, avoiding double prefix.
        api("POST", f"team/config/data/{leaf}", {"data": {"literal": True}})
        self.assertEqual(api("GET", f"team/config/data/{leaf}")["data"]["data"],
                         {"literal": True})
        result, _ = invoke(f"cat team/config/{leaf}", version=2,
                           mounts="team,team/config,kv2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], {"literal": True})

    def test_08_backup_restore_creates_new_versions_after_soft_delete(self):
        import os
        from pathlib import Path
        import subprocess
        import tarfile
        import tempfile
        from .test_vault import VCLI, VAULT, ROOT_TOKEN
        self.put("snapshot/a", {"before":"a"})
        self.put("snapshot/deep/b", {"before":"b"})
        with tempfile.TemporaryDirectory(prefix="vcli-kv2-backup-") as temp:
            env = os.environ.copy()
            env.update({"HOME":temp, "TMPDIR":temp, "TERM":"dumb",
                        "VAULT_ADDR":VAULT, "VAULT_TOKEN":ROOT_TOKEN,
                        "VAULT_CACERT":os.environ["VAULT_CACERT"],
                        "VAULT_KV_VERSION":"2", "VAULT_KV_MOUNTS":"kv2,team/config"})
            def cli_cmd(cmd):
                p=subprocess.run([str(VCLI),"-n"],input=cmd+"\n",text=True,
                                 capture_output=True,env=env,timeout=20)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            cli_cmd(f"backup kv2/{self.p}/snapshot")
            archives=list(Path(temp).glob("backup_vault*.tgz"))
            self.assertEqual(len(archives),1)
            with tarfile.open(archives[0],"r:gz") as tar:
                entries={m.name.removeprefix("./") for m in tar if m.isfile()}
            self.assertEqual(entries,{f"kv2/{self.p}/snapshot/a",
                                      f"kv2/{self.p}/snapshot/deep/b"})
            for leaf in ("snapshot/a","snapshot/deep/b"):
                api("DELETE",f"kv2/data/{self.p}/{leaf}")
                with self.assertRaises(error.HTTPError):
                    self.get(leaf)
            cli_cmd(f"restore {archives[0]}")
        self.assertEqual(self.get("snapshot/a"),{"before":"a"})
        self.assertEqual(self.get("snapshot/deep/b"),{"before":"b"})
        self.assertEqual(self.meta("snapshot/a")["current_version"],2)
        self.assertEqual(self.meta("snapshot/deep/b")["current_version"],2)

    def test_13_rm_soft_delete_explains_metadata_then_purge_removes_entry(self):
        leaf = "purge/primary"
        path = f"kv2/{self.p}/{leaf}"
        self.put(leaf, {"key":"value"})
        result, _ = invoke(f"rm {path}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Soft-deleted KV v2 version", result.stdout)
        self.assertEqual(self.meta(leaf)["current_version"], 1)
        with self.assertRaises(error.HTTPError) as denied:
            self.get(leaf)
        self.assertEqual(denied.exception.code, 404)
        # A second rm is not a second successful delete.
        repeated, _ = invoke(f"rm {path}", version=2)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("already soft-deleted", repeated.stderr)
        # Permanent deletion is disallowed in batch without a conscious --yes.
        refused, _ = invoke(f"rm --purge {path}", version=2)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("requires an interactive confirmation", refused.stderr)
        self.assertEqual(self.meta(leaf)["current_version"], 1)
        result, _ = invoke(f"rm --purge --yes {path}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Permanently removed KV v2 metadata", result.stdout)
        with self.assertRaises(error.HTTPError) as missing:
            api("GET", f"kv2/metadata/{self.p}/{leaf}")
        self.assertEqual(missing.exception.code, 404)
    def test_14_deleted_version_is_not_a_successful_cat_or_backup(self):
        """ACC-10/KV2: API metadata is authoritative; tombstones are not data."""
        leaf = "tombstone/item"
        secret = f"kv2/{self.p}/{leaf}"
        self.put(leaf, {"active": True})
        api("DELETE", f"kv2/data/{self.p}/{leaf}")
        meta = self.meta(leaf)
        self.assertEqual(meta["current_version"], 1)
        self.assertTrue(meta["versions"]["1"]["deletion_time"])
        listed, _ = invoke(f"ls kv2/{self.p}/tombstone", version=2)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn("item", listed.stdout)
        read, _ = invoke(f"cat {secret}", version=2)
        self.assertNotEqual(read.returncode, 0, read.stdout + read.stderr)
        self.assertNotIn('"metadata"', read.stdout)
        self.assertNotIn('"active"', read.stdout)
        backup, _ = invoke(f"backup kv2/{self.p}/tombstone", version=2)
        self.assertNotEqual(backup.returncode, 0,
                            "A tombstone may not yield a successful archive")
        self.assertEqual(self.meta(leaf)["current_version"], 1)

    def test_15_rmtree_skips_previously_deleted_versions_without_purging(self):
        """Already-deleted KV2 metadata survives idempotent recursive cleanup."""
        root = "mixed-deletion"
        self.put(root + "/active", {"fixture": "active"})
        self.put(root + "/deleted", {"fixture": "deleted"})
        self.put("outside/sentinel", {"fixture": "untouched"})
        api("DELETE", f"kv2/data/{self.p}/{root}/deleted")
        deleted_before = self.meta(root + "/deleted")
        result, _ = invoke(f"rmtree -f kv2/{self.p}/{root}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already deleted", result.stdout)
        for leaf in ("active", "deleted"):
            meta = self.meta(root + "/" + leaf)
            self.assertEqual(meta["current_version"], 1)
            self.assertTrue(meta["versions"]["1"]["deletion_time"])
        self.assertEqual(self.meta(root + "/deleted"), deleted_before)
        self.assertEqual(self.get("outside/sentinel"), {"fixture": "untouched"})

    def test_16_rmtree_purge_removes_every_version_and_tombstone(self):
        """Permanent recursive KV2 deletion must erase metadata, not just data."""
        root = "purge-tree"
        self.put(root + "/active", {"generation": 1})
        self.put(root + "/active", {"generation": 2})
        self.put(root + "/deleted", {"generation": 1})
        self.put(root + "/deep/leaf", {"generation": 1})
        self.put("outside/purge-sentinel", {"keep": True})
        api("DELETE", f"kv2/data/{self.p}/{root}/deleted")
        self.assertEqual(self.meta(root + "/active")["current_version"], 2)
        self.assertTrue(self.meta(root + "/deleted")["versions"]["1"]["deletion_time"])

        command = f"rmtree -f --purge kv2/{self.p}/{root}"
        done, _ = invoke(command, version=2)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Permanently purged 3 KV v2 secret(s)", done.stdout)
        for leaf in ("active", "deleted", "deep/leaf"):
            with self.subTest(leaf=leaf):
                with self.assertRaises(error.HTTPError) as missing:
                    api("GET", f"kv2/metadata/{self.p}/{root}/{leaf}")
                self.assertEqual(missing.exception.code, 404)
        self.assertEqual(self.get("outside/purge-sentinel"), {"keep": True})

    def test_17_rmtree_purge_rejects_kv1_and_entire_mount(self):
        """A destructive mode must never fall back to a KV1 DELETE."""
        from .test_vault import api
        prefix = self.p + "/not-purged"
        api("POST", f"kv1/{prefix}/keep", {"keep": True})
        for cmd in (f"rmtree -f --purge kv1/{prefix}",
                    "rmtree -f --purge kv2",
                    "rmtree -f --purge team/config"):
            with self.subTest(cmd=cmd):
                done, _ = invoke(cmd, version=2)
                self.assertNotEqual(done.returncode, 0)
        self.assertEqual(api("GET", f"kv1/{prefix}/keep")["data"], {"keep": True})


    def test_18_reserved_names_are_literal_kv2_folders_in_nested_mounts(self):
        """ACC-10: compare normal CLI reads/LIST with authoritative KV2 API."""
        import json
        mounts = "team,team/config,kv2"  # exercises longest prefix
        for mount in ("kv2", "team/config"):
            for name in ("data", "metadata", "delete"):
                with self.subTest(mount=mount, literal=name):
                    leaf = f"{name}/{self.p}/literal"
                    value = {"literal_folder": name, "mount": mount}
                    api("POST", f"{mount}/data/{leaf}", {"data": value})
                    self.assertEqual(api("GET", f"{mount}/data/{leaf}")["data"]["data"], value)
                    read, _ = invoke(f"cat {mount}/{leaf}", version=2, mounts=mounts)
                    self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
                    self.assertEqual(json.loads(read.stdout)["data"], value)
                    # The suite may already have created sibling paths here
                    # (e.g. test_10's routing/). Vault's LIST is the oracle.
                    parent = f"{name}/{self.p}"
                    expected = api("LIST", f"{mount}/metadata/{parent}")["data"]["keys"]
                    self.assertIn("literal", expected)
                    listing, _ = invoke(
                        f"ls {mount}/{parent}", version=2, mounts=mounts)
                    self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
                    actual = listing.stdout.strip().splitlines()
                    self.assertEqual(len(actual), len(set(actual)), "duplicate LIST entries")
                    self.assertEqual(set(actual), set(expected),
                                     "vcli LIST disagrees with the Vault API")

    def test_20_explicit_api_read_mode_matches_vault_and_rejects_writes(self):
        """Explicit API reads match Vault directly and cannot mutate secrets."""
        import json
        mounts = "team,team/config,kv2"
        for mount in ("kv2", "team/config"):
            leaf = f"data/{self.p}/explicit-api-{mount.replace('/', '-')}/item"
            value = {"mode": "read-only", "mount": mount}
            api("POST", f"{mount}/data/{leaf}", {"data": value})
            oracle = api("GET", f"{mount}/data/{leaf}")
            self.assertEqual(oracle["data"]["data"], value)

            logical, _ = invoke(f"cat {mount}/{leaf}", version=2, mounts=mounts)
            self.assertEqual(logical.returncode, 0, logical.stdout + logical.stderr)
            self.assertEqual(json.loads(logical.stdout)["data"], value)

            explicit, _ = invoke(f"cat --api-path {mount}/data/{leaf}",
                                 version=2, mounts=mounts)
            self.assertEqual(explicit.returncode, 0, explicit.stdout + explicit.stderr)
            raw = json.loads(explicit.stdout)
            self.assertEqual(raw["data"]["data"], oracle["data"]["data"])
            self.assertEqual(raw["data"]["metadata"]["version"],
                             oracle["data"]["metadata"]["version"])

            prefix = leaf.rsplit("/", 1)[0]
            direct_listing = api("LIST", f"{mount}/metadata/{prefix}")["data"]["keys"]
            listed, _ = invoke(f"ls --api-path {mount}/metadata/{prefix}",
                               version=2, mounts=mounts)
            self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
            self.assertEqual(listed.stdout.strip().splitlines(), direct_listing)

            forbidden = (
                f"cat --api-path {mount}/metadata/{leaf}",
                f"ls --api-path {mount}/data/{prefix}",
                f"cat --api-path {mount}/delete/{leaf}",
                f"cat --api-path {mount}/data/../{leaf}",
                f"cat --api-path {mount}/data/{leaf}?version=1",
                f"rm --api-path {mount}/data/{leaf}",
                f'put --api-path {mount}/data/{leaf} {{"overwrite":true}}',
            )
            for command in forbidden:
                with self.subTest(mount=mount, command=command):
                    rejected, _ = invoke(command, version=2, mounts=mounts)
                    self.assertNotEqual(rejected.returncode, 0,
                                        rejected.stdout + rejected.stderr)
            self.assertEqual(api("GET", f"{mount}/data/{leaf}")["data"]["data"], value)
            self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"]["current_version"], 1)

        for path in ("sys/mounts", "kv1/example", "kv2/data", "kv2/metadata"):
            with self.subTest(invalid=path):
                result, _ = invoke(f"cat --api-path {path}", version=2, mounts=mounts)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_19_reserved_names_support_safe_copy_recursive_purge_and_move(self):
        """Use only isolated Vault TLS fixtures and verify final server state."""
        mounts = "team,team/config,kv2"
        source = f"data/{self.p}/copy-src"
        destination = f"metadata/{self.p}/copy-dest"
        sentinel = f"delete/{self.p}/sentinel"
        api("POST", f"kv2/data/{sentinel}", {"data": {"untouched": True}})
        for name in ("one", "deep/two"):
            api("POST", f"kv2/data/{source}/{name}", {"data": {"id": name}})
        cp, _ = invoke(f"cptree kv2/{source} kv2/{destination}",
                       version=2, mounts=mounts)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        for name in ("one", "deep/two"):
            self.assertEqual(api("GET", f"kv2/data/{destination}/{name}")["data"]["data"],
                             {"id": name})
        purge, _ = invoke(f"rmtree -f --purge kv2/{source}",
                          version=2, mounts=mounts)
        self.assertEqual(purge.returncode, 0, purge.stdout + purge.stderr)
        for name in ("one", "deep/two"):
            with self.assertRaises(error.HTTPError) as gone:
                api("GET", f"kv2/metadata/{source}/{name}")
            self.assertEqual(gone.exception.code, 404)
            self.assertEqual(api("GET", f"kv2/data/{destination}/{name}")["data"]["data"],
                             {"id": name})
        self.assertEqual(api("GET", f"kv2/data/{sentinel}")["data"]["data"],
                         {"untouched": True})

        src = f"metadata/{self.p}/move/from"
        dst = f"data/{self.p}/move/to"
        api("POST", f"team/config/data/{src}", {"data": {"move": True}})
        moved, _ = invoke(f"mv team/config/{src} team/config/{dst}",
                           version=2, mounts=mounts)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assertEqual(api("GET", f"team/config/data/{dst}")["data"]["data"],
                         {"move": True})
        original = api("GET", f"team/config/metadata/{src}")["data"]
        self.assertTrue(original["versions"]["1"]["deletion_time"])

        target = f"delete/{self.p}/remove/item"
        api("POST", f"kv2/data/{target}", {"data": {"remove": True}})
        removed, _ = invoke(f"rm kv2/{target}", version=2, mounts=mounts)
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertTrue(api("GET", f"kv2/metadata/{target}")["data"]["versions"]["1"]["deletion_time"])
        permanently, _ = invoke(f"rm --purge --yes kv2/{target}",
                                 version=2, mounts=mounts)
        self.assertEqual(permanently.returncode, 0,
                         permanently.stdout + permanently.stderr)
        with self.assertRaises(error.HTTPError) as gone:
            api("GET", f"kv2/metadata/{target}")
        self.assertEqual(gone.exception.code, 404)
        self.assertEqual(api("GET", f"kv2/data/{sentinel}")["data"]["data"],
                         {"untouched": True})

    def test_07_field_updates_increment_versions(self):
        self.put("edit/item",{"x":1,"keep":True})
        self.cli(f'addsecret kv2/{self.p}/edit/item {{"y":2}}')
        self.cli(f'delsecret kv2/{self.p}/edit/item x')
        self.assertEqual(self.get("edit/item"),{"keep":True,"y":2})
        self.assertEqual(self.meta("edit/item")["current_version"],3)
