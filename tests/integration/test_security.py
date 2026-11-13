"""Security regression contracts executed against ephemeral HTTPS Vault.

Every destructive check uses uniquely prefixed paths. Negative tests verify
the server state, not just the CLI error code.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from urllib import error
from uuid import uuid4

from .test_vault import ROOT_TOKEN, VAULT, api, invoke


class DestructiveSafetyContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("VCLI_TEST_READONLY_TOKEN") or not VAULT.startswith("https://"):
            raise RuntimeError("Require the ephemeral HTTPS Vault fixture")
        cls.prefix = "vcli-guard-" + uuid4().hex[:12]

    def path(self, name: str) -> str:
        return f"kv1/{self.prefix}/{name}"

    def store(self, name: str, payload: dict) -> None:
        api("POST", self.path(name), payload)

    def fetch(self, name: str) -> dict:
        return api("GET", self.path(name))["data"]

    def missing(self, name: str) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            self.fetch(name)
        self.assertEqual(ctx.exception.code, 404)

    def reject(self, cmd: str, **kwargs) -> None:
        result, _ = invoke(cmd, **kwargs)
        self.assertNotEqual(result.returncode, 0, f"{cmd}: {result.stdout}\n{result.stderr}")
        self.assertNotIn(ROOT_TOKEN, result.stdout + result.stderr)

    @staticmethod
    def archive(members: list[tuple[str, bytes | tuple[str, str]]], path: Path) -> None:
        """Write a controlled TAR without extracting untrusted content."""
        with tarfile.open(path, "w:gz") as dest:
            for name, value in members:
                info = tarfile.TarInfo(name)
                if isinstance(value, tuple):
                    info.type = tarfile.SYMTYPE
                    info.linkname = value[1]
                    info.size = 0
                    dest.addfile(info)
                else:
                    info.size = len(value)
                    dest.addfile(info, io.BytesIO(value))

    def test_01_rmtree_refuses_whole_mount(self):
        self.store("rmtree-mount-sentinel", {"unchanged": True})
        self.reject("rmtree -f kv1")
        self.assertEqual(self.fetch("rmtree-mount-sentinel"), {"unchanged": True})

    def test_02_rmtree_rejects_parent_traversal(self):
        self.store("rmtree-traversal-sentinel", {"unchanged": True})
        self.reject(f"rmtree -f kv1/{self.prefix}/dummy/../rmtree-traversal-sentinel")
        self.assertEqual(self.fetch("rmtree-traversal-sentinel"), {"unchanged": True})

    def test_03_cptree_cannot_copy_into_own_descendant(self):
        self.store("copy-self/root", {"data": "intact"})
        source = self.path("copy-self")
        self.reject(f"cptree {source} {source}/inside")
        self.assertEqual(self.fetch("copy-self/root"), {"data": "intact"})
        self.missing("copy-self/inside/root")

    def test_04_restore_rejects_path_traversal_before_any_writes(self):
        good = "tar-traversal-first"
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            arc = Path(temp) / "traversal.tgz"
            self.archive([
                (self.path(good), b'{"should":"never-write"}'),
                ("../outside-vault-root", b'{"attack":true}'),
            ], arc)
            self.reject(f"restore {arc}")
        self.missing(good)

    def test_05_restore_rejects_symlink_before_any_writes(self):
        good = "tar-symlink-first"
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            arc = Path(temp) / "symlink.tgz"
            self.archive([
                (self.path(good), b'{"should":"never-write"}'),
                (self.path("link"), ("symlink", "/etc/passwd")),
            ], arc)
            self.reject(f"restore {arc}")
        self.missing(good)

    def test_06_restore_rejects_invalid_json_before_any_writes(self):
        good = "tar-invalid-json-first"
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            arc = Path(temp) / "invalid-json.tgz"
            self.archive([
                (self.path(good), b'{"should":"never-write"}'),
                (self.path("invalid"), b'["not-an-object"]'),
            ], arc)
            self.reject(f"restore {arc}")
        self.missing(good)

    def test_07_restore_rejects_duplicate_paths_before_any_writes(self):
        good = "tar-duplicate-target"
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            arc = Path(temp) / "duplicate.tgz"
            self.archive([
                (self.path(good), b'{"first":true}'),
                (self.path(good), b'{"second":true}'),
            ], arc)
            self.reject(f"restore {arc}")
        self.missing(good)

    def test_08_restore_rejects_admin_mount_paths_before_any_writes(self):
        good = "tar-admin-first"
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            arc = Path(temp) / "admin-path.tgz"
            self.archive([
                (self.path(good), b'{"should":"never-write"}'),
                ("sys/policies/acl/ci-unexpected", b'{"policy":"path * {}"}'),
            ], arc)
            self.reject(f"restore {arc}")
        self.missing(good)

    def test_09_restore_rejects_missing_and_corrupt_archive(self):
        with TemporaryDirectory(prefix="vcli-malicious-tar-") as temp:
            self.reject(f"restore {Path(temp) / 'missing.tgz'}")
            arc = Path(temp) / "broken.tgz"
            arc.write_bytes(b"NOT A TAR ARCHIVE")
            self.reject(f"restore {arc}")

    def test_10_closed_https_port_reports_failure_without_token_leak(self):
        self.store("connection-failure-sentinel", {"unchanged": True})
        result, _ = invoke(
            f"cat {self.path('connection-failure-sentinel')}",
            addr="https://127.0.0.1:1",
            env_extra={"VCLI_CONNECT_TIMEOUT": "1", "VCLI_HTTP_TIMEOUT": "2"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(ROOT_TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.fetch("connection-failure-sentinel"), {"unchanged": True})


if __name__ == "__main__":
    unittest.main()
