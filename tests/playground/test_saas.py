"""One interactive SaaS acceptance, optional asciinema, independent REST oracle.

All CLI operations go through the same real PTY driver. Run this file directly
for the identical acceptance without asciinema. The source catalogue and its
KV2 history stay intact; full recovery uses isolated sibling prefixes.
"""
from __future__ import annotations

import base64
from collections import Counter
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import subprocess
import tarfile
import sys
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".playground"
MOUNTS = ("team/config", "kv2", "kv1")
SHARED = {"username": "legacy_gateway", "password": "FAKE-SHARED-OLD"}
ROTATED = {"username": "legacy_gateway", "password": "FAKE-SHARED-ROTATED"}


class SaaSContracts(unittest.TestCase):
    def __init__(self, v, shell, enter):
        super().__init__()
        self.v, self.shell, self.enter = v, shell, enter
        status = subprocess.check_output(["scripts/playground.sh", "status"], cwd=ROOT, text=True)
        match = re.fullmatch(r"Playground running with verified TLS at (https://127\.0\.0\.1:\d+)\n", status)
        if not match or (STATE / "profile").read_text().strip() != "saas":
            raise RuntimeError("Requires the owned SaaS playground")
        self.address = match.group(1)
        self.root_token = (STATE / "root-token").read_text().strip()
        self.readonly = (STATE / "readonly-token").read_text().strip()
        self.context = ssl.create_default_context(cafile=str(STATE / "ca.pem"))
        self.conn = http.client.HTTPSConnection("127.0.0.1", urlsplit(self.address).port,
                                                context=self.context, timeout=30)
        self.work = STATE / "work"
        if self.work.is_symlink() or not self.work.is_dir():
            raise RuntimeError("Unsafe playground work directory")
        self.prefix = "saas-test-" + uuid4().hex[:12]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.conn.close()

    def api(self, method, endpoint, expected=200):
        # No mutation retries. The oracle only reads; recover a closed
        # keep-alive connection once, after long CLI commands.
        for attempt in range(2):
            try:
                self.conn.request(method, "/v1/" + endpoint,
                                  headers={"X-Vault-Token": self.root_token})
                response = self.conn.getresponse()
                raw = response.read()
                break
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                self.conn.close()
                if attempt or method not in ("GET", "LIST"):
                    raise
        allowed = expected if isinstance(expected, tuple) else (expected,)
        self.assertIn(response.status, allowed, f"{method} {endpoint}")
        return json.loads(raw) if raw else {}

    def cli(self, command, expected=b"", code=None, *, leaves=None, case=None, denied=False):
        # Close the independent REST connection BEFORE a potentially long CLI
        # operation. This avoids reusing a TLS connection closed by the server.
        self.conn.close()
        output = self.v(command, expected, code, leaves=leaves, case=case, denied=denied)
        for credential in (self.root_token, self.readonly):
            self.assertNotIn(credential.encode(), output, "Credential leaked")
        return output

    def open_role(self, role):
        self.conn.close()
        self.enter("scripts/playground.sh shell " + role)

    def close_role(self):
        self.conn.close()
        self.v("exit", prompt=rb"playground\$ $")

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
            response = self.api("LIST", self.endpoint(path, True) + "/", expected=(200, 404))
            for key in (response.get("data") or {}).get("keys", []):
                child = relative + "/" + key if relative else key
                if key.endswith("/"):
                    walk(child.rstrip("/"))
                else:
                    raw = self.api("GET", self.endpoint(root + "/" + child), expected=(200, 404))
                    data = raw.get("data")
                    if data is not None:
                        values[child] = data if root.startswith("kv1/") else data["data"]
        walk("")
        return values

    def backup(self, root, values):
        before = set(self.work.glob("backup_vault*.tgz"))
        self.cli("backup " + root, b"Vault backup created:",
                 leaves=len(values), case="backup " + root)
        created = set(self.work.glob("backup_vault*.tgz")) - before
        self.assertEqual(len(created), 1)
        archive = created.pop()
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.verify_archive(archive, root, values)
        return archive

    def verify_archive(self, archive, root, values):
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
    
    def exercise(self):
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

        # The actual CLI demonstration starts after the independent inventory.
        self.shell("# 32 tenants | 8640 live values | 1728 historical KV2 revisions")
        self.open_role("readonly")
        self.cli("help", b"Available commands:")
        self.cli("ls kv2/saas", b"tenant-032", leaves=32)
        self.cli("tree kv2/saas/tenant-001/production/eu-west", b"notifications", leaves=18)
        self.cli("cd /kv2/saas/tenant-001/production/eu-west/payments")
        self.cli("ls", b"database/")
        self.cli("cat database/postgres/primary", b"replicas")
        self.cli("cat messaging/kafka/producer", b"SCRAM-SHA-512")
        self.cli("cd /")
        self.cli("cat team/config/saas/tenant-001/production/eu-west/payments/deployment/runtime/config",
                 b"feature_flags")
        audit = self.cli("getallcertexpiration kv1/saas/tenant-001", b"[EXPIRED]", leaves=54)
        for status in (b"[OK]", b"[WARNING]", b"[CRITICAL]", b"[EXPIRED]"):
            self.assertIn(status, audit)
        self.cli("getallcertlist kv1/saas/tenant-001", b"expired.example.test", leaves=54)
        root = "kv1/flavours/pki/"
        states = self.cli("getallcertexpiration " + root + "valid", b"[EXPIRED]", leaves=5)
        for leaf, status in (("healthy", "OK"), ("warning", "WARNING"),
                             ("critical", "CRITICAL"), ("expired", "EXPIRED")):
            self.assertRegex(states.decode(),
                             r"/" + root + "valid/" + leaf
                             + r"\): -?\d+ days to renew certificate \[" + status + r"\]")
        for flag, omitted in (("-oo", (b"[OK]",)), ("-ow", (b"[WARNING]",)),
                              ("-oc", (b"[CRITICAL]", b"[EXPIRED]"))):
            result = self.cli("getallcertexpiration " + root + "valid " + flag,
                              b"days to renew certificate", leaves=5)
            for marker in omitted:
                self.assertNotIn(marker, result)
        self.cli("getcertexpiration " + root + "valid/expired", b"[EXPIRED]")
        self.cli("getcertlist " + root + "valid/multiple", b"alt.healthy.example.test")
        self.cli("getsan " + root + "valid/healthy", b"healthy.example.test")
        self.cli("certinfo " + root + "valid/critical", b"Certificate:")
        pem = self.read(root + "valid/multiple")
        if any((self.work / (stem + suffix)).exists() for stem in ("first", "second")
               for suffix in (".crt", ".key")):
            raise RuntimeError("Existing export destination: use a fresh playground")
        self.cli("getcert " + root + "valid/multiple", b"Exported")
        for stem in ("first", "second"):
            for suffix in ("crt", "key"):
                path = self.work / (stem + "." + suffix)
                self.assertEqual(path.read_text(), pem[stem + "_" + suffix])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        pem_before = set(self.work.glob("*.crt")) | set(self.work.glob("*.key"))
        for fault in ("key-mismatch", "malformed", "missing-key", "extra-key"):
            self.cli("getcert " + root + "invalid/" + fault,
                     b"certificate/private key mismatch" if fault == "key-mismatch" else b"",
                     denied=True)
            self.assertEqual(set(self.work.glob("*.crt")) | set(self.work.glob("*.key")), pem_before)

        self.cli("getallkrblist kv1/saas/tenant-001", b"tenant-001.production.eu-west", leaves=54)
        krb = "kv1/saas/tenant-001/production/eu-west/identity/legacy/kerberos/http"
        keytab = self.read(krb)
        principal = keytab["http_principal"]
        self.cli("getprinc " + krb, principal.encode())
        self.cli("krbinfo " + krb, b"principal:")
        self.cli("getkrblist " + krb, principal.encode())
        path = self.work / (principal.replace("/", "_") + ".keytab")
        if path.exists() or path.is_symlink():
            raise RuntimeError("Existing keytab export destination")
        self.cli("getkrb " + krb, b"Exported")
        self.assertEqual(path.read_bytes(), base64.b64decode(keytab["http_keytab"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for fault in ("wrong-principal", "prefix-principal", "bad-base64", "corrupt-keytab",
                      "unsafe-principal", "missing-keytab", "extra-keytab", "shell-metachar"):
            before = set(self.work.glob("*.keytab"))
            marker = b"invalid keytab or principal" if fault in (
                "wrong-principal", "prefix-principal", "bad-base64", "corrupt-keytab") else b""
            self.cli("getkrb kv1/flavours/kerberos/invalid/" + fault, marker, denied=True)
            self.assertEqual(set(self.work.glob("*.keytab")), before)
        self.cli("cat kv2/flavours/lifecycle/soft-deleted", code=b"404")
        self.cli("cat kv2/flavours/lifecycle/versioned", b"revision")
        self.cli("cat kv1/flavours/json/types", b"unicode")
        self.cli("renew-token 30m", b"Vault token renewed")

        # Full backup of the three source mounts. Each TAR is verified once
        # against the independently obtained exact REST snapshot.
        archives = {mount: self.backup(root, originals[mount]) for mount, root in roots.items()}
        self.close_role()
        self.shell("# REST and tar verification: all 8640 original values matched")
        self.shell("ls -l .playground/work/first.{crt,key} .playground/work/second.{crt,key}", b"first.crt")
        self.shell("klist -kt .playground/work/" + path.name, principal.encode())

        # Actual AppRole login by VCLI, with explicit role transitions.
        self.shell("# Tenant AppRole: own data allowed, foreign tenant and writes denied")
        self.open_role("tenant-001")
        own = "tenant-001/production/eu-west/payments/database/postgres/primary"
        self.cli("cat kv2/saas/" + own, b"FAKE-", code=b"200")
        self.cli("cat kv2/saas/" + own.replace("tenant-001", "tenant-002"), code=b"403")
        self.cli('put kv2/saas/' + own + ' {"denied":true}', code=b"403")
        for mount in ("kv1", "team/config"):
            relative = next(k for k in originals[mount] if k.startswith("tenant-001/")
                            and ("/credentials/" in k or mount == "team/config"))
            self.cli("cat " + roots[mount] + "/" + relative, code=b"200")
            self.cli("cat " + roots[mount] + "/" + relative.replace("tenant-001", "tenant-002"),
                     code=b"403")
        self.close_role()
        self.shell("# Administrator: one 270-secret migration and one bulk credential rotation")
        self.open_role("admin")
        self.cli("lsrole", b"saas-tenant-032")
        self.cli("showrole saas-tenant-001", b"saas-tenant-001")
        self.cli("lspol", b"saas-tenant-032")
        self.cli("showpol saas-tenant-001", b"capabilities")
        self.cli("mount", b"mounted paths (first-level content only):")
        self.cli("mount " + self.prefix, b"mounted")
        self.cli("umount " + self.prefix, code=b"204")

        target = "kv1/" + self.prefix
        combined = {}
        tenant = "tenant-001"
        for mount, label in (("kv1", "legacy"), ("kv2", "versioned"), ("team/config", "config")):
            source = roots[mount] + "/" + tenant
            values = {k[len(tenant)+1:]: v for k, v in originals[mount].items()
                      if k.startswith(tenant + "/")}
            self.cli("cptree " + source + " " + target + "/" + label, b"Copied tree",
                     leaves=len(values), case="migrate tenant from " + mount)
            combined.update({label + "/" + k: v for k, v in values.items()})
        self.assertEqual(len(combined), 270)
        self.assertTrue(self.snapshot(target) == combined, "Tenant copy mismatch")
        self.cli("tree " + target + "/legacy", b"credentials", leaves=54)

        # Real collision preflight; verify exact contents and no side effects.
        conflict = target + "-collision"
        last = sorted(k[len("versioned/"):] for k in combined if k.startswith("versioned/"))[-1]
        self.cli("put " + conflict + "/" + last + ' {"sentinel":true}', code=b"204")
        self.cli("cptree kv2/saas/" + tenant + " " + conflict, b"Destination already exists:",
                 leaves=162, case="collision preflight", denied=True)
        self.assertEqual(self.snapshot(conflict), {last: {"sentinel": True}})

        matches = {k for k, v in combined.items() if v == SHARED}
        self.assertEqual(len(matches), 18)
        old = json.dumps(SHARED, separators=(",", ":"))
        new = json.dumps(ROTATED, separators=(",", ":"))
        found = self.cli("find " + target + " " + old, b"/credentials/service",
                         leaves=270, case="find shared credentials")
        expression = rb"/kv1/" + self.prefix.encode() + rb"/[a-zA-Z0-9_./-]+/credentials/service"
        expected_matches = {("/" + target + "/" + k).encode() for k in matches}
        self.assertEqual(set(re.findall(expression, found)), expected_matches)
        changed = self.cli("replace " + target + " " + old + " " + new,
                           b"/credentials/service", leaves=270, case="rotate 18 shared credentials")
        self.assertEqual(set(re.findall(expression, changed)), expected_matches)
        updated = {k: ROTATED if k in matches else v for k, v in combined.items()}
        self.assertTrue(self.snapshot(target) == updated, "Rotation modified unexpected values")
        leaf = "legacy/production/eu-west/billing/legacy/credentials/service"
        self.cli("cat " + target + "/" + leaf, b"FAKE-SHARED-ROTATED")
        self.cli("cat kv1/saas/tenant-001/production/eu-west/billing/legacy/credentials/service",
                 b"FAKE-SHARED-OLD")

        # All remaining CRUD commands on a copied value, including KV2 CAS
        # and version-specific move/delete. Do not mutate the original tree.
        temporary = "kv2/" + self.prefix + "-edit/credential"
        migrated = target + "/migrated"
        self.cli("cp " + target + "/" + leaf + " " + temporary, leaves=1)
        self.assertEqual(self.read(temporary), ROTATED)
        self.cli('addsecret ' + temporary + ' {"owner":"payments","temporary":true}')
        self.assertEqual(self.read(temporary), {**ROTATED, "owner": "payments", "temporary": True})
        self.cli("delsecret " + temporary + " temporary")
        self.assertEqual(self.read(temporary), {**ROTATED, "owner": "payments"})
        self.cli("mv " + temporary + " " + migrated, b"Moved version")
        self.assertEqual(self.read(migrated), {**ROTATED, "owner": "payments"})
        self.api("GET", self.endpoint(temporary), expected=404)
        self.cli("rm " + migrated)
        self.api("GET", self.endpoint(migrated), expected=404)

        sample = {k[len("config/"):]: v for k, v in updated.items() if k.startswith("config/")}
        for mount in ("kv2", "team/config"):
            dest = mount + "/" + self.prefix
            self.cli("cptree " + target + "/config " + dest, b"Copied tree",
                     leaves=len(sample), case="cross-mount copy to " + mount)
            self.assertTrue(self.snapshot(dest) == sample)
            for child in sample:
                meta = self.api("GET", self.endpoint(dest + "/" + child, True))["data"]
                self.assertEqual(meta["current_version"], 1)
            self.cli("rmtree -f " + dest, b"Removed secrets beneath", leaves=len(sample),
                     case="KV2 soft delete " + mount)
            for child in sample:
                self.api("GET", self.endpoint(dest + "/" + child), expected=404)
                meta = self.api("GET", self.endpoint(dest + "/" + child, True))["data"]
                self.assertTrue(meta["versions"]["1"]["deletion_time"])
                self.assertFalse(meta["versions"]["1"]["destroyed"])

        sentinel = target + "-sibling/keep"
        self.cli("put " + sentinel + ' {"keep":true}')
        archive = self.backup(target, updated)
        self.cli("rmtree -f " + target, b"Removed secrets beneath",
                 leaves=270, case="delete migrated tenant")
        self.assertEqual(self.snapshot(target), {})
        self.cli("cat " + target + "/" + leaf, code=b"404")
        self.assertEqual(self.read(sentinel), {"keep": True})
        self.cli("restore " + str(archive.relative_to(ROOT)), b"Vault backup restored",
                 leaves=270, case="recover migrated tenant")
        self.assertTrue(self.snapshot(target) == updated, "Tenant restore mismatch")
        self.cli("rmtree -f " + target, b"Removed secrets beneath",
                 leaves=270, case="cleanup recovered tenant")
        self.assertEqual(self.snapshot(target), {})
        self.cli("rmtree -f " + conflict, b"Removed secrets beneath")
        self.cli("rm " + sentinel)
        self.cli("history", b"restore ")
        self.cli("verbose", b"verbose mode ON")
        self.cli("help history")
        self.cli("verbose", b"verbose mode OFF")
        # Diagnostic switches must not emit authentication credentials.
        self.cli("enable-debug", b"debug enabled")
        self.cli("enable-hexdump-debug", b"hexdump debug enabled")
        self.cli("help ls")
        self.cli("disable-hexdump-debug", b"hexdump debug disabled")
        self.cli("disable-debug", b"debug disabled")
        self.close_role()

        # Full recovery to a new prefix in each original engine. Explicitly
        # rewrite archive member names, never secret contents or source paths.
        self.shell("# Full disaster recovery: remap backup paths into isolated recovery prefixes")
        recovered = {}
        for mount, archive in archives.items():
            destination = mount + "/" + self.prefix + "-recovery"
            self.assertEqual(self.snapshot(destination), {})
            remapped = self.work / (mount.replace("/", "-") + "-" + self.prefix + "-recovery.tgz")
            self.shell("python3 tests/playground/test_saas.py --remap "
                       + str(archive.relative_to(ROOT)) + " " + roots[mount] + " "
                       + destination + " " + str(remapped.relative_to(ROOT)),
                       b"Remapped archive:")
            self.verify_archive(remapped, destination, originals[mount])
            recovered[mount] = (destination, remapped)

        self.open_role("admin")
        for mount, (destination, archive) in recovered.items():
            self.cli("restore " + str(archive.relative_to(ROOT)), b"Vault backup restored",
                     leaves=len(originals[mount]), case="full recovery " + mount)
            self.assertTrue(self.snapshot(destination) == originals[mount],
                            f"Full restore differs: {mount}")
            if mount != "kv1":
                for child in originals[mount]:
                    meta = self.api("GET", self.endpoint(destination + "/" + child, True))["data"]
                    self.assertEqual(meta["current_version"], 1)
        self.close_role()

        # No mutation of the source catalogue or its historical versions.
        for mount, source in roots.items():
            self.assertTrue(self.snapshot(source) == originals[mount], f"Source changed: {mount}")
        for child in dbs:
            meta = self.api("GET", "kv2/metadata/saas/" + child)["data"]
            self.assertEqual(meta["current_version"], 2)
            self.assertEqual(set(meta["versions"]), {"1", "2"})
        self.shell("# REST acceptance PASS: 8640 restored values, 8640 originals intact, 1728 historical versions")
        # The recovered catalogues and verified private archives remain available
        # until explicit playground down; do not delete operator files.



def remap_archive(source_file, source, destination, output_file):
    """Explicit local path rewrite; no Vault writes and no extraction."""
    if source not in {mount + "/saas" for mount in MOUNTS}:
        raise ValueError("Source must be a seeded SaaS root")
    mount = source.removesuffix("/saas")
    if not re.fullmatch(re.escape(mount) + r"/saas-test-[a-f0-9]{12}-recovery", destination):
        raise ValueError("Destination must be an isolated recovery prefix")
    source_file, output_file = Path(source_file), Path(output_file)
    work = (STATE / "work").resolve()
    if (source_file.resolve().parent != work or output_file.resolve().parent != work
            or source_file.is_symlink() or output_file.is_symlink()):
        raise ValueError("Archive path must be in the owned playground work directory")
    count = 0
    seen = set()
    try:
        with source_file.open("rb") as source_stream, output_file.open("xb") as output_stream:
            with tarfile.open(fileobj=source_stream, mode="r:gz") as src, \
                    tarfile.open(fileobj=output_stream, mode="w:gz") as dst:
                for member in src:
                    if member.isdir():
                        continue
                    name = member.name.removeprefix("./")
                    if (not member.isfile() or not name.startswith(source + "/")
                            or ".." in PurePosixPath(name).parts or name in seen):
                        raise ValueError("Unexpected backup member")
                    seen.add(name)
                    with src.extractfile(member) as stream:
                        value = stream.read()
                    info = tarfile.TarInfo(destination + "/" + name[len(source)+1:])
                    info.mode, info.size = 0o600, len(value)
                    import io
                    dst.addfile(info, io.BytesIO(value))
                    count += 1
        if not count:
            raise ValueError("Empty backup")
    except Exception:
        # Never unlink an existing target when exclusive creation failed.
        raise
    print(f"Remapped archive: {count} members")


if __name__ == "__main__":
    os.umask(0o077)
    if len(sys.argv) == 6 and sys.argv[1] == "--remap":
        remap_archive(*sys.argv[2:])
    elif len(sys.argv) == 1:
        if not (STATE / "profile").is_file() or (STATE / "profile").read_text().strip() != "saas":
            raise SystemExit("Start the owned SaaS playground first")
        os.environ.setdefault("VCLI_SAAS_METRICS", str(STATE / "work/saas-metrics.json"))
        os.chdir(ROOT)
        os.execvp("bash", ["bash", "scripts/playground-demo.sh"])
    else:
        raise SystemExit("Usage: test_saas.py [--remap SOURCE_TAR SOURCE_ROOT DEST_ROOT DEST_TAR]")
