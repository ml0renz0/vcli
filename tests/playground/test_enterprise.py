"""Enterprise-scale acceptance against the owned, disposable TLS playground.

VCLI performs every operation under test. The separate REST client enumerates
and compares complete JSON objects, not VCLI's own listing or success output.
No test mutates the seeded enterprise source; all writes use a unique prefix.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import subprocess
import tarfile
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".playground"
MOUNTS = ("team/config", "kv2", "kv1")
ENVIRONMENTS = ("development", "staging", "production")
APPS = ("payments", "identity", "catalog")
LEGACY_MATCH = {"username": "shared_svc", "password": "FAKE-LEGACY-OLD"}
ROTATED = {"username": "shared_svc", "password": "FAKE-ROTATED-TEST"}


def split_path(path):
    for mount in MOUNTS:
        if path.startswith(mount + "/"):
            return mount, path[len(mount) + 1:]
    raise AssertionError("Not a playground KV path")


def expected_paths():
    return {
        "kv1/enterprise": {
            *(f"pki/{env}/payments/ingress" for env in ENVIRONMENTS),
            *(f"kerberos/{env}/shared/http" for env in ENVIRONMENTS),
            *("legacy/" + leaf for leaf in (
                "payments/eu/primary", "payments/us/primary",
                "identity/eu/primary", "catalog/eu/primary",
                "payments/eu/analytics", "catalog/us/backup")),
        },
        "kv2/enterprise": {
            f"{env}/{app}/{leaf}"
            for env in ENVIRONMENTS for app in APPS
            for leaf in ("database/primary", "api/gateway", "messaging/broker")
        },
        "team/config/enterprise": {
            f"{env}/{leaf}" for env in ENVIRONMENTS
            for leaf in ("platform/feature-flags", "observability/collector")
        },
    }


class EnterpriseContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The launcher verifies ownership, local Docker, address and TLS.
        status = subprocess.run(
            ["scripts/playground.sh", "status"], cwd=ROOT,
            check=True, text=True, capture_output=True, timeout=30,
        ).stdout.strip()
        match = re.fullmatch(
            r"Playground running with verified TLS at (https://127\.0\.0\.1:[0-9]+)",
            status,
        )
        if not match:
            raise RuntimeError("Requires the running owned TLS playground")
        cls.address = match.group(1)
        cls.token = (STATE / "root-token").read_text().strip()
        cls.readonly = (STATE / "readonly-token").read_text().strip()
        cls.ca = STATE / "ca.pem"
        cls.http = build_opener(ProxyHandler({}), HTTPSHandler(
            context=ssl.create_default_context(cafile=str(cls.ca))))
        cls.local = tempfile.TemporaryDirectory(prefix="enterprise-contract-", dir=STATE)
        cls.addClassCleanup(cls.local.cleanup)
        cls.work = Path(cls.local.name)
        cls.prefix = "enterprise-test-" + uuid4().hex[:12]

    def cli(self, *commands, readonly=False, success=True):
        # Isolate the CLI exactly as the playground launcher does. Never
        # inherit environment variables pointing to a production Vault.
        env = {
            "PATH": os.environ["PATH"], "HOME": str(self.work),
            "TMPDIR": str(self.work), "TERM": "dumb", "LANG": "C.UTF-8",
            "VAULT_ADDR": self.address, "VAULT_CACERT": str(self.ca),
            "VAULT_TOKEN": self.readonly if readonly else self.token,
            "VAULT_KV_VERSION": "2", "VAULT_KV_MOUNTS": "kv2,team/config",
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        }
        result = subprocess.run(
            [str(ROOT / "vcli"), "-n"], input="\n".join(commands) + "\n",
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=180,
        )
        for credential in (self.token, self.readonly):
            self.assertTrue(credential not in result.stdout + result.stderr,
                            "CLI output contains an authentication credential")
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "Unexpected CLI success")
        return result.stdout

    def api(self, method, path):
        req = Request(self.address + "/v1/" + path,
                      headers={"X-Vault-Token": self.token}, method=method)
        with self.http.open(req, timeout=10) as response:
            body = response.read()
            return json.loads(body) if body else {}

    def endpoint(self, path, *, listing=False):
        mount, relative = split_path(path)
        if mount == "kv1":
            return path
        return mount + ("/metadata/" if listing else "/data/") + relative

    def read(self, path):
        response = self.api("GET", self.endpoint(path))["data"]
        return response if split_path(path)[0] == "kv1" else response["data"]

    def missing(self, path):
        with self.assertRaises(HTTPError) as caught:
            self.read(path)
        self.assertEqual(caught.exception.code, 404)

    def snapshot(self, root):
        """Relative path -> exact live value. KV2 metadata may outlive data."""
        result = {}

        def walk(relative):
            path = root + ("/" + relative if relative else "")
            try:
                keys = self.api("LIST", self.endpoint(path, listing=True) + "/")["data"]["keys"]
            except HTTPError as exc:
                if exc.code == 404:
                    return
                raise
            for key in keys:
                child = relative + "/" + key if relative else key
                if key.endswith("/"):
                    walk(child.rstrip("/"))
                else:
                    try:
                        result[child] = self.read(root + "/" + child)
                    except HTTPError as exc:
                        if exc.code != 404:
                            raise
        walk("")
        return result

    def archive(self, root, values, *, readonly=False):
        before = set(self.work.glob("backup_vault*.tgz"))
        self.cli("backup " + root, readonly=readonly)
        created = set(self.work.glob("backup_vault*.tgz")) - before
        self.assertEqual(len(created), 1)
        path = created.pop()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        actual = {}
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.removeprefix("./")
                self.assertFalse(PurePosixPath(name).is_absolute())
                self.assertNotIn("..", PurePosixPath(name).parts)
                if member.isdir():
                    continue
                self.assertTrue(member.isfile(), "Unexpected tar member type")
                self.assertTrue(name.startswith(root + "/"))
                rel = name[len(root) + 1:]
                self.assertNotIn(rel, actual, "Duplicate archive entry")
                with tar.extractfile(member) as stream:
                    actual[rel] = json.load(stream)
        self.assertEqual(actual, values)
        return path

    def test_all_persistent_flavours(self):
        root = "kv1/flavours"
        good = self.snapshot(root + "/pki/valid")
        self.assertEqual(set(good), {"healthy", "warning", "critical", "expired", "multiple"})
        out = self.cli("getallcertexpiration " + root + "/pki/valid", readonly=True)
        for leaf, status in (("healthy", "OK"), ("warning", "WARNING"),
                             ("critical", "CRITICAL"), ("expired", "EXPIRED")):
            self.assertRegex(out, r"/" + root + "/pki/valid/" + leaf
                             + r"\): -?\d+ days to renew certificate \[" + status + r"\]")
        self.assertNotIn("[OK]", self.cli("getallcertexpiration " + root + "/pki/valid -oo",
                                        readonly=True))
        self.assertNotIn("[WARNING]", self.cli("getallcertexpiration " + root + "/pki/valid -ow",
                                             readonly=True))
        critical = self.cli("getallcertexpiration " + root + "/pki/valid -oc", readonly=True)
        self.assertNotIn("[CRITICAL]", critical)
        self.assertNotIn("[EXPIRED]", critical)
        self.cli("getcert " + root + "/pki/valid/multiple", readonly=True)
        for stem in ("first", "second"):
            for suffix in ("crt", "key"):
                file = self.work / (stem + "." + suffix)
                self.assertEqual(file.read_text(), good["multiple"][stem + "_" + suffix])
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                file.unlink()
        for leaf in ("key-mismatch", "malformed", "missing-key", "extra-key"):
            self.cli("getcert " + root + "/pki/invalid/" + leaf, readonly=True, success=False)
        self.assertFalse(list(self.work.glob("*.crt")))
        self.assertFalse(list(self.work.glob("*.key")))

        import base64
        valid = self.read(root + "/kerberos/valid/multiple")
        out = self.cli("getallkrblist " + root + "/kerberos/valid", readonly=True)
        for stem in ("first", "second"):
            self.assertIn(valid[stem + "_principal"], out)
        self.cli("getkrb " + root + "/kerberos/valid/multiple", readonly=True)
        for stem in ("first", "second"):
            file = self.work / (valid[stem + "_principal"].replace("/", "_") + ".keytab")
            self.assertEqual(file.read_bytes(), base64.b64decode(valid[stem + "_keytab"]))
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            file.unlink()
        for leaf in ("bad-base64", "corrupt-keytab", "wrong-principal", "prefix-principal",
                     "unsafe-principal", "missing-keytab", "extra-keytab", "shell-metachar"):
            for command in ("getkrb", "getprinc"):
                self.cli(command + " " + root + "/kerberos/invalid/" + leaf,
                         readonly=True, success=False)
        self.assertFalse(list(self.work.glob("*.keytab")))
        self.assertEqual(self.read(root + "/json/empty"), {"object": {}, "array": [], "string": ""})
        typed = self.read(root + "/json/types")
        self.assertIs(typed["bool"], True)
        self.assertIsNone(typed["null"])
        self.assertEqual(typed["string"], "001")
        self.assertEqual(typed["number"], 1)
        self.assertEqual(typed["array"], [1, False, {"a": "b"}])
        for mount in ("kv2", "team/config"):
            prefix = mount + "/flavours/lifecycle/"
            meta = self.api("GET", self.endpoint(prefix + "versioned", listing=True))["data"]
            self.assertEqual(meta["current_version"], 2)
            self.assertTrue(meta["cas_required"])
            self.assertEqual(self.read(prefix + "versioned"), {"revision": 2})
            for leaf, destroyed in (("soft-deleted", False), ("destroyed", True)):
                self.missing(prefix + leaf)
                meta = self.api("GET", self.endpoint(prefix + leaf, listing=True))["data"]
                self.assertEqual(meta["versions"]["1"]["destroyed"], destroyed)
                if not destroyed:
                    self.assertTrue(meta["versions"]["1"]["deletion_time"])

    def test_enterprise_recursion_full_catalog(self):
        manifest = expected_paths()
        originals = {root: self.snapshot(root) for root in manifest}
        for root, expected in manifest.items():
            self.assertEqual(set(originals[root]), expected, root)
        self.assertEqual(sum(map(len, originals.values())), 45)

        # Read-only backup over EVERY actual source mount, including nested
        # KV2: exact contents, binary-as-base64 data and PEM are compared.
        for root, data in originals.items():
            with self.subTest(source_backup=root):
                self.archive(root, data, readonly=True)

        # Copy every seeded leaf to a single independent KV1 deployment.
        target = "kv1/" + self.prefix
        combined = {}
        for name, root in (("legacy-engine", "kv1/enterprise"),
                           ("versioned-engine", "kv2/enterprise"),
                           ("nested-engine", "team/config/enterprise")):
            self.cli("cptree " + root + " " + target + "/" + name)
            combined.update({name + "/" + key: value for key, value in originals[root].items()})
        self.assertEqual(self.snapshot(target), combined)

        # Copy collision preflight: an existing late leaf must prevent ALL
        # writes, not just the write that collides.
        collision = target + "-collision"
        source = "kv2/enterprise"
        last = sorted(originals[source])[-1]
        self.cli("put " + collision + "/" + last + ' {"sentinel":true}')
        self.cli("cptree " + source + " " + collision, success=False)
        self.assertEqual(self.snapshot(collision), {last: {"sentinel": True}})

        # Multi-match find and replace verify exact sets, including negatives.
        legacy = target + "/legacy-engine/legacy"
        matches = {key for key, value in combined.items() if value == LEGACY_MATCH}
        self.assertEqual(len(matches), 4)
        output = self.cli("find " + legacy + " " + json.dumps(LEGACY_MATCH))
        found = {line.strip() for line in output.splitlines() if line.strip().startswith("/" + target)}
        self.assertEqual(found, {"/" + target + "/" + key for key in matches})
        self.cli("replace " + legacy + " " + json.dumps(LEGACY_MATCH) + " " + json.dumps(ROTATED))
        combined = {key: ROTATED if key in matches else value for key, value in combined.items()}
        self.assertEqual(self.snapshot(target), combined)

        # Copy in the opposite direction, to both KV2 mount layouts. Verify
        # their actual JSON and version metadata, not command output alone.
        for mount in ("kv2", "team/config"):
            dest = mount + "/" + self.prefix
            sample = self.snapshot(legacy)
            self.cli("cptree " + legacy + " " + dest)
            self.assertEqual(self.snapshot(dest), sample)
            for leaf in sample:
                meta = self.api("GET", self.endpoint(dest + "/" + leaf, listing=True))["data"]
                self.assertEqual(meta["current_version"], 1)
            self.cli("rmtree -f " + dest)
            self.assertEqual(self.snapshot(dest), {})
            for leaf in sample:
                self.missing(dest + "/" + leaf)
                meta = self.api("GET", self.endpoint(dest + "/" + leaf, listing=True))["data"]
                self.assertTrue(meta["versions"]["1"]["deletion_time"])
                self.assertFalse(meta["versions"]["1"]["destroyed"])

        # Full disaster recovery of the 45-leaf mixed estate. The sibling
        # sentinel proves rmtree did not delete beyond the requested root.
        sentinel = target + "-sentinel/keep"
        self.cli("put " + sentinel + ' {"keep":true}')
        archive = self.archive(target, combined)
        self.cli("rmtree -f " + target)
        self.assertEqual(self.snapshot(target), {})
        for leaf in combined:
            self.missing(target + "/" + leaf)
        self.assertEqual(self.read(sentinel), {"keep": True})
        self.cli("restore " + str(archive))
        self.assertEqual(self.snapshot(target), combined)
        self.assertEqual(self.read(sentinel), {"keep": True})

        # Forbidden write, including a bulk operation, leaves source intact.
        self.cli("cptree " + source + " " + target + "-forbidden",
                 readonly=True, success=False)
        self.assertEqual(self.snapshot(target + "-forbidden"), {})
        for root, data in originals.items():
            self.assertEqual(self.snapshot(root), data, "Original seed changed")
        self.cli("rmtree -f " + target, "rmtree -f " + collision, "rm " + sentinel)
        self.assertEqual(self.snapshot(target), {})
        self.assertEqual(self.snapshot(collision), {})
        self.missing(sentinel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
