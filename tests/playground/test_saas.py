"""Large acceptance: actual Bash CLI operations; independently inspect Vault REST.

No VCLI stub, no real tenant data, no mutations to the source catalogue.
Run only against the owned SaaS playground created by the launcher.
"""
from __future__ import annotations

from collections import Counter
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tarfile
import tempfile
import time
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".playground"
MOUNTS = ("team/config", "kv2", "kv1")
SHARED = {"username": "legacy_gateway", "password": "FAKE-SHARED-OLD"}
ROTATED = {"username": "legacy_gateway", "password": "FAKE-SHARED-ROTATED"}


class SaaSContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status = subprocess.check_output(["scripts/playground.sh", "status"], cwd=ROOT, text=True)
        match = re.fullmatch(r"Playground running with verified TLS at (https://127\.0\.0\.1:\d+)\n", status)
        if not match or (STATE / "profile").read_text().strip() != "saas":
            raise RuntimeError("Requires the owned SaaS playground")
        cls.address = match.group(1)
        cls.root_token = (STATE / "root-token").read_text().strip()
        cls.readonly = (STATE / "readonly-token").read_text().strip()
        cls.context = ssl.create_default_context(cafile=str(STATE / "ca.pem"))
        cls.conn = http.client.HTTPSConnection("127.0.0.1", urlsplit(cls.address).port,
                                               context=cls.context, timeout=30)
        cls.addClassCleanup(cls.conn.close)
        local = tempfile.TemporaryDirectory(prefix="saas-contract-", dir=STATE)
        cls.addClassCleanup(local.cleanup)
        cls.work = Path(local.name)
        cls.prefix = "saas-test-" + uuid4().hex[:12]

    def api(self, method, endpoint, token=None, body=None, expected=200):
        headers = {"X-Vault-Token": token or self.root_token, "Content-Type": "application/json"}
        self.conn.request(method, "/v1/" + endpoint,
                          None if body is None else json.dumps(body), headers)
        response = self.conn.getresponse()
        raw = response.read()
        self.assertEqual(response.status, expected, f"{method} {endpoint}")
        return json.loads(raw) if raw else {}

    def cli(self, command, *, token=None, success=True):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.work),
               "TMPDIR": str(self.work), "TERM": "dumb", "LANG": "C.UTF-8",
               "VAULT_ADDR": self.address, "VAULT_CACERT": str(STATE / "ca.pem"),
               "VAULT_TOKEN": token or self.root_token, "VAULT_KV_VERSION": "2",
               "VAULT_KV_MOUNTS": "kv2,team/config",
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        start = time.monotonic()
        result = subprocess.run([str(ROOT / "vcli"), "-n"], input=command + "\n",
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=7200)
        print(f"{command.split()[0]}: {time.monotonic()-start:.2f}s", flush=True)
        for credential in (self.root_token, self.readonly, token):
            if credential:
                self.assertTrue(credential not in result.stdout + result.stderr,
                                "Credential leaked")
        if success:
            self.assertEqual(result.returncode, 0, f"{command.split()[0]} failed")
        else:
            self.assertNotEqual(result.returncode, 0)
        return result.stdout

    def endpoint(self, path, listing=False):
        mount = next(m for m in MOUNTS if path.startswith(m + "/"))
        if mount == "kv1":
            return path
        return mount + ("/metadata/" if listing else "/data/") + path[len(mount)+1:]

    def read(self, path):
        value = self.api("GET", self.endpoint(path))["data"]
        return value if path.startswith("kv1/") else value["data"]

    def snapshot(self, root):
        values = {}

        def walk(relative):
            path = root + ("/" + relative if relative else "")
            self.conn.request("LIST", "/v1/" + self.endpoint(path, True) + "/",
                              headers={"X-Vault-Token": self.root_token})
            response = self.conn.getresponse()
            raw = response.read()
            if response.status == 404:
                return
            self.assertEqual(response.status, 200, path)
            for key in json.loads(raw)["data"]["keys"]:
                child = relative + "/" + key if relative else key
                if key.endswith("/"):
                    walk(child.rstrip("/"))
                else:
                    values[child] = self.read(root + "/" + child)
        walk("")
        return values

    def backup(self, root, values, token=None):
        before = set(self.work.glob("backup_vault*.tgz"))
        self.cli("backup " + root, token=token)
        created = set(self.work.glob("backup_vault*.tgz")) - before
        self.assertEqual(len(created), 1)
        archive = created.pop()
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        actual = {}
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if member.isdir():
                    continue
                self.assertTrue(member.isfile())
                name = member.name.removeprefix("./")
                self.assertTrue(name.startswith(root + "/"))
                rel = name[len(root)+1:]
                self.assertNotIn(rel, actual)
                self.assertNotIn("..", Path(rel).parts)
                with tar.extractfile(member) as stream:
                    actual[rel] = json.load(stream)
        # Boolean comparison avoids dumping PEM and bulk contents on failures.
        self.assertEqual(set(actual), set(values))
        self.assertTrue(actual == values, f"Backup payload mismatch: {root}")
        return archive

    def test_full_saas_acceptance(self):
        roots = {mount: mount + "/saas" for mount in MOUNTS}
        originals = {mount: self.snapshot(root) for mount, root in roots.items()}
        self.assertEqual({m: len(v) for m, v in originals.items()},
                         {"kv1": 1728, "kv2": 5184, "team/config": 1728})
        tenants = {f"tenant-{i:03d}" for i in range(1, 33)}
        # Independent shape/cardinality oracle; not imported from seed script.
        expected_groups = {(tenant, env, region, svc)
                           for tenant in tenants
                           for env in ("development", "staging", "production")
                           for region in ("eu-west", "us-east", "ap-south")
                           for svc in ("payments", "identity", "catalog", "billing",
                                       "search", "notifications")}
        for mount, values in originals.items():
            groups = Counter(tuple(path.split("/")[:4]) for path in values)
            self.assertEqual(set(groups), expected_groups)
            self.assertEqual(set(groups.values()), {3 if mount == "kv2" else 1})
        self.assertEqual(sum(value == SHARED for value in originals["kv1"].values()), 576)
        dbs = [path for path in originals["kv2"] if path.endswith("/database/postgres/primary")]
        self.assertEqual(len(dbs), 1728)
        for path in dbs:
            meta = self.api("GET", "kv2/metadata/saas/" + path)["data"]
            self.assertEqual(meta["current_version"], 2)
            self.assertEqual(set(meta["versions"]), {"1", "2"})
            prior = self.api("GET", "kv2/data/saas/" + path + "?version=1")["data"]["data"]
            self.assertNotEqual(prior["password"], originals["kv2"][path]["password"])

        # Full backup of all 8,640 values, including each nested mount and
        # actual binary-as-base64/PEM fixtures. No sampling of backup members.
        for mount, root in roots.items():
            self.backup(root, originals[mount], self.readonly)

        # Actual AppRole authentication and isolation. No secrets are printed.
        role = "saas-tenant-001"
        role_id = self.api("GET", "auth/approle/role/" + role + "/role-id")["data"]["role_id"]
        secret_id = self.api("POST", "auth/approle/role/" + role + "/secret-id",
                             body={})["data"]["secret_id"]
        token = self.api("POST", "auth/approle/login",
                         body={"role_id": role_id, "secret_id": secret_id})["auth"]["client_token"]
        own = "tenant-001/production/eu-west/payments/database/postgres/primary"
        foreign = own.replace("tenant-001", "tenant-002")
        self.assertIn("FAKE-", self.cli("cat kv2/saas/" + own, token=token))
        self.cli("cat kv2/saas/" + foreign, token=token, success=False)
        self.cli('put kv2/saas/' + own + ' {"denied":true}', token=token, success=False)
        for mount in MOUNTS:
            relative = next(k for k in originals[mount] if k.startswith("tenant-001/"))
            self.api("GET", self.endpoint(mount + "/saas/" + relative), token=token)
            self.api("GET", self.endpoint(mount + "/saas/" + relative.replace("tenant-001", "tenant-002")),
                     token=token, expected=403)
        self.api("POST", "auth/token/revoke", body={"token": token}, expected=204)

        # One complete 270-leaf tenant migrated into a separate KV1 tree.
        target = "kv1/" + self.prefix
        combined = {}
        tenant = "tenant-001"
        for mount, label in (("kv1", "legacy"), ("kv2", "versioned"), ("team/config", "config")):
            source = roots[mount] + "/" + tenant
            values = {k[len(tenant)+1:]: v for k, v in originals[mount].items()
                      if k.startswith(tenant + "/")}
            self.cli("cptree " + source + " " + target + "/" + label)
            combined.update({label + "/" + k: v for k, v in values.items()})
        self.assertEqual(len(combined), 270)
        self.assertTrue(self.snapshot(target) == combined)

        # Detect collision before any write; never overwrite existing target.
        conflict = target + "-collision"
        last = sorted(k[len("versioned/"):] for k in combined if k.startswith("versioned/"))[-1]
        self.cli("put " + conflict + "/" + last + ' {"sentinel":true}')
        self.cli("cptree kv2/saas/" + tenant + " " + conflict, success=False)
        self.assertEqual(self.snapshot(conflict), {last: {"sentinel": True}})

        matches = {k for k, v in combined.items() if v == SHARED}
        self.assertEqual(len(matches), 18)
        out = self.cli("find " + target + " " + json.dumps(SHARED, separators=(",", ":")))
        found = set(re.findall(r"/kv1/" + self.prefix + r"/[a-zA-Z0-9_./-]+", out))
        self.assertEqual(found, {"/" + target + "/" + k for k in matches})
        self.cli("replace " + target + " " + json.dumps(SHARED, separators=(",", ":"))
                 + " " + json.dumps(ROTATED, separators=(",", ":")))
        updated = {k: ROTATED if k in matches else v for k, v in combined.items()}
        self.assertTrue(self.snapshot(target) == updated)

        # Cross-mount copy back to both KV2 engines; verify version metadata
        # and soft deletion without treating retained metadata as live data.
        sample = {k[len("config/"):]: v for k, v in updated.items() if k.startswith("config/")}
        for mount in ("kv2", "team/config"):
            dest = mount + "/" + self.prefix
            self.cli("cptree " + target + "/config " + dest)
            self.assertTrue(self.snapshot(dest) == sample)
            for leaf in sample:
                meta = self.api("GET", self.endpoint(dest + "/" + leaf, True))["data"]
                self.assertEqual(meta["current_version"], 1)
            self.cli("rmtree -f " + dest)
            for leaf in sample:
                self.api("GET", self.endpoint(dest + "/" + leaf), expected=404)
                meta = self.api("GET", self.endpoint(dest + "/" + leaf, True))["data"]
                self.assertTrue(meta["versions"]["1"]["deletion_time"])
                self.assertFalse(meta["versions"]["1"]["destroyed"])

        archive = self.backup(target, updated)
        sentinel = target + "-sibling/keep"
        self.cli("put " + sentinel + ' {"keep":true}')
        self.cli("rmtree -f " + target)
        self.assertEqual(self.snapshot(target), {})
        self.assertEqual(self.read(sentinel), {"keep": True})
        self.cli("restore " + str(archive))
        self.assertTrue(self.snapshot(target) == updated)
        self.cli("rmtree -f " + target)
        self.assertEqual(self.snapshot(target), {})
        self.cli("rmtree -f " + conflict)
        self.cli("rm " + sentinel)

        # Compare every original value again, not just samples.
        for mount, root in roots.items():
            self.assertTrue(self.snapshot(root) == originals[mount], f"Source changed: {mount}")
        print("Verified 8640 original values, 1728 historical versions, and 270-leaf recovery",
              flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
