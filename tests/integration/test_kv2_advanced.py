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

    def test_10_explicit_nested_mount_api_path_is_not_rewritten(self):
        """Raw data endpoints must remain raw even with overlapping prefixes."""
        leaf = "routing/raw-path"
        self.put(leaf, {"raw": True}, mount="team/config")
        path = f"team/config/data/{self.p}/{leaf}"
        result, _ = invoke(
            f"cat {path}", version=2, mounts="team,team/config,kv2"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import json
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"]["data"], {"raw": True})

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

    def test_07_field_updates_increment_versions(self):
        self.put("edit/item",{"x":1,"keep":True})
        self.cli(f'addsecret kv2/{self.p}/edit/item {{"y":2}}')
        self.cli(f'delsecret kv2/{self.p}/edit/item x')
        self.assertEqual(self.get("edit/item"),{"keep":True,"y":2})
        self.assertEqual(self.meta("edit/item")["current_version"],3)
