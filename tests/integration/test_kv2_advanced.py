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


    def test_21_read_kv2_historical_versions_and_metadata(self):
        """Compare CLI reads with authoritative Vault API, including nested mounts."""
        import json
        mounts = "team,team/config,kv2"
        for mount in ("kv2", "team/config"):
            with self.subTest(mount=mount):
                leaf = f"data/{self.p}/history-{mount.replace('/', '-')}/item"
                secret = f"{mount}/{leaf}"
                first = {"generation": 1, "fixture": "not-latest"}
                latest = {"generation": 2, "fixture": "latest"}
                for data in (first, latest):
                    api("POST", f"{mount}/data/{leaf}", {"data": data})
                oracle1 = api("GET", f"{mount}/data/{leaf}?version=1")
                oracle2 = api("GET", f"{mount}/data/{leaf}?version=2")
                self.assertEqual(oracle1["data"]["data"], first)
                self.assertEqual(oracle2["data"]["data"], latest)

                for number, oracle in ((1, oracle1), (2, oracle2)):
                    result, _ = invoke(f"cat --version {number} {secret}",
                                       version=2, mounts=mounts)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["data"], oracle["data"]["data"])
                normal, _ = invoke(f"cat {secret}", version=2, mounts=mounts)
                self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
                self.assertEqual(json.loads(normal.stdout)["data"], latest)

                meta_oracle = api("GET", f"{mount}/metadata/{leaf}")["data"]
                details, _ = invoke(f"cat --metadata {secret}", version=2, mounts=mounts)
                self.assertEqual(details.returncode, 0, details.stdout + details.stderr)
                self.assertEqual(json.loads(details.stdout)["data"], meta_oracle)
                self.assertEqual(meta_oracle["current_version"], 2)

                rejected = (
                    f"cat --version 0 {secret}",
                    f"cat --version -1 {secret}",
                    f"cat --version bogus {secret}",
                    f"cat --version 1 {secret}?version=2",
                    f"cat --version 1 {mount}/data/../{self.p}/secret",
                    f"cat --metadata {secret}?version=1",
                    f"cat --version 1 kv1/{self.p}/not-kv2",
                    f"cat --metadata kv1/{self.p}/not-kv2",
                )
                for command in rejected:
                    with self.subTest(command=command):
                        result, _ = invoke(command, version=2, mounts=mounts)
                        self.assertNotEqual(result.returncode, 0,
                                            result.stdout + result.stderr)
                self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"],
                                 meta_oracle, "read-only commands modified metadata")

    def test_22_deleted_and_destroyed_versions_do_not_leak_data(self):
        """A tombstone is not readable; metadata remains inspectable."""
        import json
        mount, mounts = "kv2", "kv2,team/config"
        leaf = f"metadata/{self.p}/history-lifecycle/item"
        secret = f"{mount}/{leaf}"
        api("POST", f"{mount}/data/{leaf}", {"data": {"generation": 1}})
        api("POST", f"{mount}/data/{leaf}", {"data": {"generation": 2}})
        api("POST", f"{mount}/delete/{leaf}", {"versions": [2]})
        expected = api("GET", f"{mount}/metadata/{leaf}")["data"]
        self.assertTrue(expected["versions"]["2"]["deletion_time"])
        valid, _ = invoke(f"cat --version 1 {secret}", version=2, mounts=mounts)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertEqual(json.loads(valid.stdout)["data"], {"generation": 1})
        deleted, _ = invoke(f"cat --version 2 {secret}", version=2, mounts=mounts)
        self.assertNotEqual(deleted.returncode, 0)
        self.assertNotIn('"generation"', deleted.stdout)
        details, _ = invoke(f"cat --metadata {secret}", version=2, mounts=mounts)
        self.assertEqual(details.returncode, 0, details.stdout + details.stderr)
        self.assertEqual(json.loads(details.stdout)["data"], expected)

        # The independent API performs the destructive fixture operation:
        # the CLI's new history commands must remain read-only.
        api("POST", f"{mount}/destroy/{leaf}", {"versions": [1]})
        destroyed, _ = invoke(f"cat --version 1 {secret}", version=2, mounts=mounts)
        self.assertNotEqual(destroyed.returncode, 0)
        self.assertNotIn('"generation"', destroyed.stdout)
        after, _ = invoke(f"cat --metadata {secret}", version=2, mounts=mounts)
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        latest_metadata = api("GET", f"{mount}/metadata/{leaf}")["data"]
        self.assertTrue(latest_metadata["versions"]["1"]["destroyed"])
        self.assertEqual(json.loads(after.stdout)["data"], latest_metadata)


    def test_23_undelete_restores_only_selected_versions(self):
        """Use direct Vault API to prove exact version restoration."""
        mounts = "team,team/config,kv2"
        for mount in ("kv2", "team/config"):
            with self.subTest(mount=mount):
                leaf = f"data/{self.p}/undelete-{mount.replace('/', '-')}/item"
                sibling = f"data/{self.p}/undelete-{mount.replace('/', '-')}/sentinel"
                for generation in (1, 2, 3):
                    api("POST", f"{mount}/data/{leaf}", {"data": {"generation": generation}})
                api("POST", f"{mount}/data/{sibling}", {"data": {"keep": True}})
                api("POST", f"{mount}/delete/{leaf}", {"versions": [1, 3]})
                before = api("GET", f"{mount}/metadata/{leaf}")["data"]
                self.assertEqual(before["current_version"], 3)
                self.assertTrue(before["versions"]["1"]["deletion_time"])
                self.assertTrue(before["versions"]["3"]["deletion_time"])
                self.assertFalse(before["versions"]["2"]["deletion_time"])
                restored, _ = invoke(f"undelete --versions 1,3 {mount}/{leaf}",
                                     version=2, mounts=mounts)
                self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
                self.assertIn("Restored KV v2 version(s) 1,3", restored.stdout)
                after = api("GET", f"{mount}/metadata/{leaf}")["data"]
                self.assertEqual(after["current_version"], 3)
                self.assertEqual(set(after["versions"]), set(before["versions"]))
                for number in (1, 2, 3):
                    self.assertEqual(after["versions"][str(number)]["deletion_time"], "")
                    self.assertFalse(after["versions"][str(number)]["destroyed"])
                    self.assertEqual(api("GET", f"{mount}/data/{leaf}?version={number}")["data"]["data"],
                                     {"generation": number})
                self.assertEqual(api("GET", f"{mount}/data/{sibling}")["data"]["data"],
                                 {"keep": True})

    def test_24_undelete_invalid_batches_fail_before_modification(self):
        """Mixed active, destroyed or nonexistent versions cannot partially restore."""
        mount, mounts = "kv2", "kv2,team/config"
        leaf = f"metadata/{self.p}/undelete-guard/item"
        sentinel = f"metadata/{self.p}/undelete-guard/sentinel"
        for generation in (1, 2, 3):
            api("POST", f"{mount}/data/{leaf}", {"data": {"generation": generation}})
        api("POST", f"{mount}/data/{sentinel}", {"data": {"keep": True}})
        api("POST", f"{mount}/delete/{leaf}", {"versions": [1, 3]})
        api("POST", f"{mount}/destroy/{leaf}", {"versions": [2]})
        before = api("GET", f"{mount}/metadata/{leaf}")["data"]
        target = f"{mount}/{leaf}"
        for numbers in ("1,2", "1,99", "2", "3,3", "0", "-1", "01",
                        "1,", "1,,3", "1.0", "999999999999999999999"):
            with self.subTest(numbers=numbers):
                result, _ = invoke(f"undelete --versions {numbers} {target}",
                                   version=2, mounts=mounts)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"], before)
        for command in (f"undelete --versions 1 kv1/{self.p}/not-kv2",
                        "undelete --versions 1 kv2",
                        f"undelete --versions 1 {target}?version=1",
                        f"undelete --versions 1 {mount}/data/../{leaf}",
                        f"undelete --api-path {mount}/undelete/{leaf}",
                        f"undelete --versions 1 {target} extra"):
            with self.subTest(command=command):
                result, _ = invoke(command, version=2, mounts=mounts)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"], before)
        self.assertEqual(api("GET", f"{mount}/data/{sentinel}")["data"]["data"], {"keep": True})

    def test_25_undelete_acl_blocks_mutation_even_with_metadata_read(self):
        """Read access to metadata is not update access to KV2 undelete."""
        leaf = f"delete/{self.p}/undelete-acl/item"
        api("POST", f"kv2/data/{leaf}", {"data": {"version": 1}})
        api("POST", f"kv2/data/{leaf}", {"data": {"version": 2}})
        active, _ = invoke(f"undelete --versions 2 kv2/{leaf}", version=2)
        self.assertNotEqual(active.returncode, 0)
        api("POST", f"kv2/delete/{leaf}", {"versions": [1]})
        policy = "vcli-undelete-ro-" + uuid4().hex[:12]
        api("PUT", f"sys/policies/acl/{policy}", {
            "policy": f'path "kv2/metadata/{leaf}" {{ capabilities = ["read"] }}'
        })
        limited = api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "10m"
        })["auth"]["client_token"]
        try:
            denied, _ = invoke(f"undelete --versions 1 kv2/{leaf}",
                               version=2, token=limited)
            self.assertNotEqual(denied.returncode, 0, denied.stdout + denied.stderr)
            self.assertTrue(api("GET", f"kv2/metadata/{leaf}")["data"]
                            ["versions"]["1"]["deletion_time"])
        finally:
            api("POST", "auth/token/revoke", {"token": limited})
            api("DELETE", f"sys/policies/acl/{policy}")


    def test_26_destroy_selected_versions_preserves_other_history(self):
        """Destroy one live and one soft-deleted version on both KV2 mounts."""
        mounts = "team,team/config,kv2"
        for mount in ("kv2", "team/config"):
            with self.subTest(mount=mount):
                leaf = f"delete/{self.p}/destroy-{mount.replace('/', '-')}/item"
                sibling = f"delete/{self.p}/destroy-{mount.replace('/', '-')}/keep"
                for generation in (1, 2, 3):
                    api("POST", f"{mount}/data/{leaf}",
                        {"data": {"generation": generation}})
                api("POST", f"{mount}/data/{sibling}", {"data": {"safe": True}})
                api("POST", f"{mount}/delete/{leaf}", {"versions": [3]})
                before = api("GET", f"{mount}/metadata/{leaf}")["data"]
                self.assertEqual(before["current_version"], 3)
                self.assertTrue(before["versions"]["3"]["deletion_time"])
                result, _ = invoke(f"destroy --versions 1,3 --yes {mount}/{leaf}",
                                   version=2, mounts=mounts)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Permanently destroyed KV v2 version(s) 1,3", result.stdout)
                after = api("GET", f"{mount}/metadata/{leaf}")["data"]
                self.assertEqual(after["current_version"], 3)
                self.assertEqual(set(before["versions"]), set(after["versions"]))
                for number in (1, 3):
                    self.assertTrue(after["versions"][str(number)]["destroyed"])
                    with self.assertRaises(error.HTTPError) as missing:
                        api("GET", f"{mount}/data/{leaf}?version={number}")
                    self.assertEqual(missing.exception.code, 404)
                self.assertEqual(after["versions"]["2"], before["versions"]["2"])
                self.assertEqual(api("GET", f"{mount}/data/{leaf}?version=2")["data"]["data"],
                                 {"generation": 2})
                self.assertEqual(api("GET", f"{mount}/data/{sibling}")["data"]["data"],
                                 {"safe": True})
                refused, _ = invoke(f"undelete --versions 3 {mount}/{leaf}",
                                    version=2, mounts=mounts)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"], after)

    def test_27_destroy_refuses_invalid_batches_and_missing_confirmation(self):
        """No irreversible request when any version is invalid or user has not consented."""
        mount, mounts = "kv2", "kv2,team/config"
        leaf = f"data/{self.p}/destroy-guards/item"
        sibling = f"data/{self.p}/destroy-guards/sentinel"
        api("POST", f"{mount}/data/{leaf}", {"data": {"generation": 1}})
        api("POST", f"{mount}/data/{leaf}", {"data": {"generation": 2}})
        api("POST", f"{mount}/data/{sibling}", {"data": {"sentinel": True}})
        api("POST", f"{mount}/destroy/{leaf}", {"versions": [1]})
        before = api("GET", f"{mount}/metadata/{leaf}")["data"]
        target = f"{mount}/{leaf}"
        invalid = (
            f"destroy --versions 1,2 --yes {target}",
            f"destroy --versions 2,99 --yes {target}",
            f"destroy --versions 2,2 --yes {target}",
            f"destroy --versions 0 --yes {target}",
            f"destroy --versions -1 --yes {target}",
            f"destroy --versions 02 --yes {target}",
            f"destroy --versions 2, --yes {target}",
            f"destroy --versions 999999999999999999999 --yes {target}",
            f"destroy --versions 2 {target}",
            f"destroy --versions 2 --yes kv1/{self.p}/not-kv2",
            f"destroy --versions 2 --yes {target}?version=2",
            f"destroy --versions 2 --yes kv2/data/../{leaf}",
            f"destroy --api-path kv2/destroy/{leaf}",
            f"destroy --versions 2 --yes {target} extra",
            f"destroy --versions 2 --yes kv2",
        )
        for command in invalid:
            with self.subTest(command=command):
                result, _ = invoke(command, version=2, mounts=mounts)
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)
                self.assertEqual(api("GET", f"{mount}/metadata/{leaf}")["data"], before)
        self.assertEqual(api("GET", f"{mount}/data/{sibling}")["data"]["data"],
                         {"sentinel": True})

    def test_28_destroy_denied_when_only_metadata_read_is_permitted(self):
        """Privilege to inspect versions never implies privilege to destroy them."""
        leaf = f"metadata/{self.p}/destroy-acl/item"
        api("POST", f"kv2/data/{leaf}", {"data": {"generation": 1}})
        policy = "vcli-destroy-readonly-" + uuid4().hex[:12]
        api("PUT", f"sys/policies/acl/{policy}", {
            "policy": f'path "kv2/metadata/{leaf}" {{ capabilities = ["read"] }}'
        })
        limited = api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "10m"
        })["auth"]["client_token"]
        try:
            denied, _ = invoke(f"destroy --versions 1 --yes kv2/{leaf}",
                               version=2, token=limited)
            self.assertNotEqual(denied.returncode, 0, denied.stdout + denied.stderr)
            meta = api("GET", f"kv2/metadata/{leaf}")["data"]
            self.assertFalse(meta["versions"]["1"]["destroyed"])
            self.assertEqual(api("GET", f"kv2/data/{leaf}")["data"]["data"],
                             {"generation": 1})
        finally:
            api("POST", "auth/token/revoke", {"token": limited})
            api("DELETE", f"sys/policies/acl/{policy}")


    def test_29_auto_detects_kv1_kv2_and_nested_kv2_without_route_guessing(self):
        """The authoritative sys/mounts inventory controls live CLI routing."""
        import json
        automatic = {"VAULT_KV_VERSION": "auto", "VAULT_KV_MOUNTS": ""}
        leaf = f"data/{self.p}/auto-routing/item"
        fixtures = (
            ("kv1", f"kv1/{leaf}", {"engine": "kv1"}),
            ("kv2", f"kv2/data/{leaf}", {"engine": "kv2"}),
            ("team/config", f"team/config/data/{leaf}", {"engine": "nested-kv2"}),
        )
        for mount, endpoint, value in fixtures:
            payload = value if mount == "kv1" else {"data": value}
            api("POST", endpoint, payload)
        for mount, endpoint, value in fixtures:
            with self.subTest(mount=mount):
                result, _ = invoke(f"cat {mount}/{leaf}", env_extra=automatic)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["data"], value)
                if mount == "kv1":
                    self.assertEqual(api("GET", endpoint)["data"], value)
                else:
                    self.assertEqual(api("GET", endpoint)["data"]["data"], value)
        history, _ = invoke(f"cat --metadata kv2/{leaf}", env_extra=automatic)
        self.assertEqual(history.returncode, 0, history.stdout + history.stderr)
        self.assertEqual(json.loads(history.stdout)["data"],
                         api("GET", f"kv2/metadata/{leaf}")["data"])
        explicit, _ = invoke(f"cat --api-path team/config/data/{leaf}",
                             env_extra=automatic)
        self.assertEqual(explicit.returncode, 0, explicit.stdout + explicit.stderr)
        self.assertEqual(json.loads(explicit.stdout)["data"]["data"], {"engine": "nested-kv2"})

    def test_30_auto_routes_nested_kv1_below_kv2_without_rewriting(self):
        """A deeper KV1 mount blocks the ancestor KV2 mapping."""
        import json
        mount = "vcli-auto-" + uuid4().hex[:12]
        nested = f"{mount}/nested"
        automatic = {"VAULT_KV_VERSION": "auto", "VAULT_KV_MOUNTS": ""}
        api("POST", f"sys/mounts/{mount}", {
            "type": "kv", "options": {"version": "2"}
        })
        try:
            api("POST", f"sys/mounts/{nested}", {
                "type": "kv", "options": {"version": "1"}
            })
            api("POST", f"{mount}/data/outer", {"data": {"engine": "outer-kv2"}})
            api("POST", f"{nested}/inner", {"engine": "inner-kv1"})
            for logical, expected in (
                (f"{mount}/outer", {"engine": "outer-kv2"}),
                (f"{nested}/inner", {"engine": "inner-kv1"}),
            ):
                with self.subTest(path=logical):
                    result, _ = invoke(f"cat {logical}", env_extra=automatic)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["data"], expected)
            created, _ = invoke(f'put {nested}/created {{"safe":"kv1"}}',
                                env_extra=automatic)
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            self.assertEqual(api("GET", f"{nested}/created")["data"], {"safe": "kv1"})
            self.assertEqual(api("GET", f"{mount}/data/outer")["data"]["data"],
                             {"engine": "outer-kv2"})
        finally:
            try:
                api("DELETE", f"sys/mounts/{nested}")
            except error.HTTPError:
                pass
            api("DELETE", f"sys/mounts/{mount}")

    def test_31_auto_denied_inventory_fails_closed_and_manual_still_works(self):
        """The client cannot guess KV version when sys/mounts returns 403."""
        import json
        leaf = f"{self.p}/auto-permissions/item"
        value = {"authorized": True}
        api("POST", f"kv2/data/{leaf}", {"data": value})
        policy = "vcli-auto-ro-" + uuid4().hex[:12]
        api("PUT", f"sys/policies/acl/{policy}", {
            "policy": f'path "kv2/data/{leaf}" {{ capabilities = ["read"] }}'
        })
        restricted = api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "10m"
        })["auth"]["client_token"]
        try:
            denied, _ = invoke(f"cat kv2/{leaf}", token=restricted,
                               env_extra={"VAULT_KV_VERSION": "auto",
                                          "VAULT_KV_MOUNTS": ""})
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("sys/mounts discovery failed", denied.stderr)
            self.assertNotIn('"authorized"', denied.stdout)
            self.assertNotIn(restricted, denied.stdout + denied.stderr)
            explicit, _ = invoke(f"cat kv2/{leaf}", token=restricted,
                                 version=2, mounts="kv2")
            self.assertEqual(explicit.returncode, 0,
                             explicit.stdout + explicit.stderr)
            self.assertEqual(json.loads(explicit.stdout)["data"], value)
            self.assertEqual(api("GET", f"kv2/data/{leaf}")["data"]["data"], value)
        finally:
            api("POST", "auth/token/revoke", {"token": restricted})
            api("DELETE", f"sys/policies/acl/{policy}")

    def test_32_auto_rejects_conflicting_or_invalid_configuration(self):
        """No silent precedence if an explicit map and discovery are both set."""
        for config in (
            {"VAULT_KV_VERSION": "auto", "VAULT_KV_MOUNTS": "kv2"},
            {"VAULT_KV_VERSION": "3", "VAULT_KV_MOUNTS": "kv2"},
        ):
            with self.subTest(config=config):
                result, _ = invoke("ls kv2/", env_extra=config)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("VAULT_KV_VERSION", result.stderr)

    def test_07_field_updates_increment_versions(self):
        self.put("edit/item",{"x":1,"keep":True})
        self.cli(f'addsecret kv2/{self.p}/edit/item {{"y":2}}')
        self.cli(f'delsecret kv2/{self.p}/edit/item x')
        self.assertEqual(self.get("edit/item"),{"keep":True,"y":2})
        self.assertEqual(self.meta("edit/item")["current_version"],3)
