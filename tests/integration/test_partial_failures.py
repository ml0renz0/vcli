"""Mid-operation failure tests: HTTPS-to-HTTPS fault proxy backed by REAL Vault.

The proxy forwards all ordinary calls to an ephemeral Vault over verified TLS,
but injects a 503 at the second specified mutation/leaf read. We independently
verify which secrets really exist in Vault after the CLI exits. No mock Vault.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import socket
import time
from pathlib import Path
import ssl
import subprocess
import tarfile
import tempfile
from threading import Thread
from urllib import error, request
from uuid import uuid4
import unittest

from .test_vault import VAULT, VCLI, ROOT_TOKEN, api, invoke


class _Proxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class InterruptedOperations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.environ.get("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("Tests require an ephemeral HTTPS Vault")
        cls.unique = "vcli-interrupt-" + uuid4().hex[:12]
        cls.certs = tempfile.TemporaryDirectory(prefix="vcli-interrupt-tls-")
        cls.cert = Path(cls.certs.name) / "proxy.pem"
        cls.key = Path(cls.certs.name) / "proxy.key"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "1", "-sha256", "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(cls.key), "-out", str(cls.cert),
        ], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.certs.cleanup()

    def path(self, leaf):
        return f"kv1/{self.unique}/{leaf}"

    def store(self, leaf, data):
        api("POST", self.path(leaf), data)

    def stored(self, leaf):
        return api("GET", self.path(leaf))["data"]

    def absent(self, leaf):
        with self.assertRaises(error.HTTPError) as caught:
            self.stored(leaf)
        self.assertEqual(caught.exception.code, 404)

    @contextmanager
    def proxy(self, *, fail_method, fail_prefix, fail_index=2, revoke_token=None,
              drop_success_index=None, pause_after_first_success=0):
        """Terminate test TLS locally, then forward over *verified* TLS to Vault."""
        observations = []
        state = {"matched": 0}
        backend_context = ssl.create_default_context(cafile=os.environ["VAULT_CACERT"])

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.process()
            def do_LIST(self):
                self.process()
            def do_POST(self):
                self.process()
            def do_DELETE(self):
                self.process()
            def process(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n) if n else None
                inject = self.command == fail_method and self.path.startswith(fail_prefix)
                if inject:
                    state["matched"] += 1
                    if revoke_token is None and state["matched"] == fail_index:
                        payload = b'{"errors":["injected failure after prior success"]}'
                        observations.append((self.command, self.path, 503))
                        self.respond(503, payload)
                        return

                headers = {"X-Vault-Token": self.headers.get("X-Vault-Token", ""),
                           "Content-Type": "application/json"}
                upstream = request.Request(
                    VAULT + self.path, data=body, method=self.command, headers=headers
                )
                try:
                    with request.urlopen(upstream, timeout=5, context=backend_context) as resp:
                        status = resp.status
                        payload = resp.read()
                except error.HTTPError as exc:
                    status = exc.code
                    payload = exc.read()
                observations.append((self.command, self.path, status))
                # The first Vault mutation really succeeded. Revoke the
                # restricted token using our independent root API before
                # responding, so the next request sees a genuine Vault 403.
                if (revoke_token is not None and inject and
                        state["matched"] == 1 and 200 <= status < 300):
                    api("POST", "auth/token/revoke", {"token": revoke_token})
                if (inject and state["matched"] == 1 and
                        200 <= status < 300 and pause_after_first_success):
                    time.sleep(pause_after_first_success)
                if (inject and drop_success_index == state["matched"] and
                        200 <= status < 300):
                    # Vault accepted the mutation, but the TLS connection
                    # dies before a status line can reach the CLI.
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.close_connection = True
                    return
                self.respond(status, payload)

            def respond(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                    self.wfile.flush()

            def log_message(self, *_):
                pass

        server = _Proxy(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}", observations
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def cli(self, cmd, addr):
        completed, history = invoke(
            cmd, addr=addr, env_extra={"VAULT_CACERT": str(self.cert)}
        )
        self.assertNotIn(ROOT_TOKEN, completed.stdout + completed.stderr + history)
        return completed

    def test_01_cptree_second_write_fails_with_truthful_partial_report(self):
        prefix = "copy/source"
        self.store(prefix + "/a", {"id": "a"})
        self.store(prefix + "/z", {"id": "z"})
        target = f"/v1/{self.path('copy/dest')}/"
        with self.proxy(fail_method="POST", fail_prefix=target) as (url, observed):
            out = self.cli(
                f"cptree {self.path('copy/source')} {self.path('copy/dest')}", url
            )
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(len([s for m, p, s in observed if m == "POST" and p.startswith(target)]), 2)
        self.assertEqual(self.stored("copy/dest/a"), {"id": "a"})
        self.absent("copy/dest/z")
        self.assertEqual(self.stored("copy/source/z"), {"id": "z"})

    def test_02_rmtree_second_delete_fails_with_truthful_partial_report(self):
        self.store("delete/a", {"id": "a"})
        self.store("delete/z", {"id": "z"})
        self.store("protected/sentinel", {"safe": True})
        target = f"/v1/{self.path('delete')}/"
        with self.proxy(fail_method="DELETE", fail_prefix=target) as (url, observed):
            out = self.cli(f"rmtree -f {self.path('delete')}", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(len([s for m, p, s in observed if m == "DELETE" and p.startswith(target)]), 2)
        self.absent("delete/a")
        self.assertEqual(self.stored("delete/z"), {"id": "z"})
        self.assertEqual(self.stored("protected/sentinel"), {"safe": True})

    def test_03_restore_second_write_fails_with_truthful_partial_report(self):
        self.store("restore-outside/sentinel", {"safe": True})
        with tempfile.TemporaryDirectory(prefix="vcli-interrupt-tar-") as tmp:
            archive = Path(tmp) / "restore.tgz"
            with tarfile.open(archive, "w:gz") as tar:
                for name in ("a", "z"):
                    data = ('{"id":"' + name + '"}').encode()
                    member = tarfile.TarInfo(self.path("restore/" + name))
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            target = f"/v1/{self.path('restore')}/"
            with self.proxy(fail_method="POST", fail_prefix=target) as (url, observed):
                out = self.cli(f"restore {archive}", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(len([s for m, p, s in observed if m == "POST" and p.startswith(target)]), 2)
        self.assertEqual(self.stored("restore/a"), {"id": "a"})
        self.absent("restore/z")
        self.assertEqual(self.stored("restore-outside/sentinel"), {"safe": True})

    def test_04_backup_second_leaf_read_fails_without_archive(self):
        self.store("backup/a", {"id": "a"})
        self.store("backup/z", {"id": "z"})
        target = f"/v1/{self.path('backup')}/"
        with tempfile.TemporaryDirectory(prefix="vcli-interrupt-backup-") as tmp:
            env = os.environ.copy()
            env.update({
                "HOME": tmp, "TMPDIR": tmp, "TERM": "dumb",
                "VAULT_ADDR": "unused", "VAULT_TOKEN": ROOT_TOKEN,
                "VAULT_CACERT": str(self.cert),
                "VAULT_KV_VERSION": "1",
            })
            with self.proxy(fail_method="GET", fail_prefix=target) as (url, observed):
                env["VAULT_ADDR"] = url
                out = subprocess.run(
                    [str(VCLI), "-n"],
                    input=f"backup {self.path('backup')}\n",
                    env=env, text=True, capture_output=True, timeout=20
                )
            self.assertNotEqual(out.returncode, 0)
            self.assertNotIn(ROOT_TOKEN, out.stdout + out.stderr)
            self.assertEqual(list(Path(tmp).glob("backup_vault*.tgz")), [])
            self.assertEqual(len([s for m, p, s in observed if m == "GET" and p.startswith(target)]), 2)
        self.assertEqual(self.stored("backup/a"), {"id": "a"})
        self.assertEqual(self.stored("backup/z"), {"id": "z"})


    def create_limited_token(self):
        policy = "vcli-revoke-" + uuid4().hex[:12]
        api("PUT", f"sys/policies/acl/{policy}", {
            "policy": (
                f'path "kv1/{self.unique}/*" '
                '{ capabilities = ["create","read","update","delete","list"] }'
            ),
        })
        return api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "2m",
        })["auth"]["client_token"]

    def restricted_cli(self, command, address, token):
        out, history = invoke(
            command, token=token, addr=address,
            env_extra={"VAULT_CACERT": str(self.cert)},
        )
        self.assertNotIn(token, out.stdout + out.stderr + history)
        self.assertNotIn(ROOT_TOKEN, out.stdout + out.stderr + history)
        return out

    def test_05_cptree_stops_after_real_token_revocation(self):
        self.store("revoked-copy/source/a", {"id": "a"})
        self.store("revoked-copy/source/z", {"id": "z"})
        token = self.create_limited_token()
        target = f"/v1/{self.path('revoked-copy/dest')}/"
        with self.proxy(fail_method="POST", fail_prefix=target,
                        revoke_token=token) as (address, observed):
            out = self.restricted_cli(
                f"cptree {self.path('revoked-copy/source')} "
                f"{self.path('revoked-copy/dest')}", address, token
            )
        writes = [(method, status) for method, path, status in observed
                  if method == "POST" and path.startswith(target)]
        # Revocation blocks reading the next source secret, preventing
        # another destination write entirely.
        self.assertEqual(len(writes), 1, writes)
        self.assertIn(writes[0][1], (200, 204))
        self.assertEqual(
            len([status for method, path, status in observed
                 if method == "GET" and path.endswith("/revoked-copy/source/z")
                 and status == 403]), 1, observed
        )
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL cptree", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(self.stored("revoked-copy/dest/a"), {"id": "a"})
        self.absent("revoked-copy/dest/z")
        self.assertEqual(self.stored("revoked-copy/source/z"), {"id": "z"})

    def test_06_rmtree_stops_after_real_token_revocation(self):
        self.store("revoked-delete/a", {"id": "a"})
        self.store("revoked-delete/z", {"id": "z"})
        self.store("revoked-delete-safe/sentinel", {"safe": True})
        token = self.create_limited_token()
        target = f"/v1/{self.path('revoked-delete')}/"
        with self.proxy(fail_method="DELETE", fail_prefix=target,
                        revoke_token=token) as (address, observed):
            out = self.restricted_cli(
                f"rmtree -f {self.path('revoked-delete')}", address, token
            )
        deletes = [(method, status) for method, path, status in observed
                   if method == "DELETE" and path.startswith(target)]
        self.assertEqual(len(deletes), 2, deletes)
        self.assertIn(deletes[0][1], (200, 204))
        self.assertEqual(deletes[1][1], 403)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL rmtree", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.absent("revoked-delete/a")
        self.assertEqual(self.stored("revoked-delete/z"), {"id": "z"})
        self.assertEqual(self.stored("revoked-delete-safe/sentinel"), {"safe": True})

    def test_07_restore_stops_after_real_token_revocation(self):
        token = self.create_limited_token()
        target = f"/v1/{self.path('revoked-restore')}/"
        with tempfile.TemporaryDirectory(prefix="vcli-revocation-tar-") as tmp:
            archive = Path(tmp) / "revocation.tgz"
            with tarfile.open(archive, "w:gz") as tar:
                for leaf in ("a", "z"):
                    data = ('{"id":"' + leaf + '"}').encode()
                    member = tarfile.TarInfo(self.path("revoked-restore/" + leaf))
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            with self.proxy(fail_method="POST", fail_prefix=target,
                            revoke_token=token) as (address, observed):
                out = self.restricted_cli(f"restore {archive}", address, token)
        writes = [(method, status) for method, path, status in observed
                  if method == "POST" and path.startswith(target)]
        self.assertEqual(len(writes), 2, writes)
        self.assertIn(writes[0][1], (200, 204))
        self.assertEqual(writes[1][1], 403)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PARTIAL restore", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(self.stored("revoked-restore/a"), {"id": "a"})
        self.absent("revoked-restore/z")



    def test_08_restore_reports_ambiguous_write_after_vault_commits(self):
        target = f"/v1/{self.path('ambiguous-restore')}/"
        with tempfile.TemporaryDirectory(prefix="vcli-ambiguous-response-") as tmp:
            archive = Path(tmp) / "ambiguous.tgz"
            with tarfile.open(archive, "w:gz") as tar:
                for leaf in ("a", "b", "z"):
                    payload = ('{"id":"' + leaf + '"}').encode()
                    member = tarfile.TarInfo(self.path("ambiguous-restore/" + leaf))
                    member.size = len(payload)
                    tar.addfile(member, io.BytesIO(payload))
            with self.proxy(fail_method="POST", fail_prefix=target,
                            drop_success_index=2) as (addr, observed):
                out = self.cli(f"restore {archive}", addr)
        posts = [(p, status) for method, p, status in observed
                 if method == "POST" and p.startswith(target)]
        self.assertEqual(len(posts), 2, posts)
        self.assertTrue(all(status in (200, 204) for _, status in posts), posts)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("OUTCOME_UNKNOWN", out.stderr)
        self.assertIn("PARTIAL restore", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(self.stored("ambiguous-restore/a"), {"id": "a"})
        self.assertEqual(self.stored("ambiguous-restore/b"), {"id": "b"})
        self.absent("ambiguous-restore/z")

    def test_09_expiration_during_restore_preserves_unprocessed_secrets(self):
        target = f"/v1/{self.path('expired-restore')}/"
        # TTL is long enough for first write; its response is held until the
        # token expires, so the second write receives a real Vault 403.
        token = api("POST", "auth/token/create", {
            "ttl": "6s", "explicit_max_ttl": "6s",
        })["auth"]["client_token"]
        with tempfile.TemporaryDirectory(prefix="vcli-expired-mid-restore-") as tmp:
            archive = Path(tmp) / "expiry.tgz"
            with tarfile.open(archive, "w:gz") as tar:
                for leaf in ("a", "z"):
                    payload = ('{"id":"' + leaf + '"}').encode()
                    member = tarfile.TarInfo(self.path("expired-restore/" + leaf))
                    member.size = len(payload)
                    tar.addfile(member, io.BytesIO(payload))
            with self.proxy(fail_method="POST", fail_prefix=target,
                            pause_after_first_success=7.0) as (addr, observed):
                out = self.restricted_cli(f"restore {archive}", addr, token)
        posts = [(p, status) for method, p, status in observed
                 if method == "POST" and p.startswith(target)]
        self.assertEqual(len(posts), 2, posts)
        self.assertIn(posts[0][1], (200, 204))
        self.assertEqual(posts[1][1], 403)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PARTIAL restore", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(self.stored("expired-restore/a"), {"id": "a"})
        self.absent("expired-restore/z")


if __name__ == "__main__":
    unittest.main()
