"""Deterministic CAS and concurrent-modification tests using disposable HTTPS Vault.

A verified-TLS reverse proxy can synchronize two reads on the same old version
or inject a competing writer precisely after cptree's preflight check.
This verifies actual Vault state independently; no mocked secret store.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
from threading import Barrier, BrokenBarrierError, Thread
from urllib import error, request
from uuid import uuid4
import unittest

from .test_vault import VAULT, VCLI, ROOT_TOKEN, api, invoke


class _Proxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class CASContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.environ.get("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("CAS tests require the disposable HTTPS Vault fixture")
        cls.p = "vcli-cas-" + uuid4().hex[:12]
        cls.certs = tempfile.TemporaryDirectory(prefix="vcli-cas-cert-")
        cls.cert = Path(cls.certs.name) / "proxy.pem"
        cls.key = Path(cls.certs.name) / "proxy.key"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-days", "1", "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(cls.key), "-out", str(cls.cert)
        ], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.certs.cleanup()

    def secret(self, path):
        return f"kv2/{self.p}/{path}"

    def write(self, name, obj):
        api("POST", f"kv2/data/{self.p}/{name}", {"data": obj})

    def read(self, name):
        return api("GET", f"kv2/data/{self.p}/{name}")["data"]

    def metadata(self, name):
        return api("GET", f"kv2/metadata/{self.p}/{name}")["data"]

    @contextmanager
    def proxy(self, *, barrier_path=None, takeover_path=None, takeover_value=None,
              takeover_after_post=None, takeover_before_delete=None):
        events = []
        state = {"claimed": False}
        barrier = Barrier(2) if barrier_path else None
        backend_tls = ssl.create_default_context(cafile=os.environ["VAULT_CACERT"])
        test = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.forward()
            def do_LIST(self):
                self.forward()
            def do_POST(self):
                self.forward()
            def forward(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else None

                # Inject a competing writer before a destination write or
                # immediately before the version-specific source delete.
                if self.command == "POST" and self.path == takeover_path and not state["claimed"]:
                    state["claimed"] = True
                    test.write(takeover_value[0], takeover_value[1])
                if (self.command == "POST" and takeover_before_delete is not None
                        and self.path == takeover_before_delete[0]
                        and not state["claimed"]):
                    state["claimed"] = True
                    test.write(takeover_before_delete[1], takeover_before_delete[2])

                req = request.Request(
                    VAULT + self.path,
                    data=body,
                    method=self.command,
                    headers={"X-Vault-Token": self.headers.get("X-Vault-Token", ""),
                             "Content-Type": "application/json"},
                )
                try:
                    with request.urlopen(req, timeout=6, context=backend_tls) as r:
                        status, payload = r.status, r.read()
                except error.HTTPError as exc:
                    status, payload = exc.code, exc.read()

                if (self.command == "POST" and takeover_after_post is not None
                        and self.path == takeover_after_post[0] and 200 <= status < 300
                        and not state["claimed"]):
                    state["claimed"] = True
                    test.write(takeover_after_post[1], takeover_after_post[2])

                if self.command == "GET" and self.path == barrier_path:
                    try:
                        barrier.wait(timeout=10)
                    except BrokenBarrierError:
                        status, payload = 503, b'{"errors":["barrier timed out"]}'

                events.append((self.command, self.path, status))
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
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}", events
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_01_addsecret_respects_required_cas(self):
        name = "required/add"
        self.write(name, {"keep": "original"})
        api("POST", f"kv2/metadata/{self.p}/{name}", {"cas_required": True})
        result, _ = invoke(f'addsecret {self.secret(name)} {{"added":true}}', version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(name)["data"], {"keep": "original", "added": True})
        self.assertEqual(self.metadata(name)["current_version"], 2)

    def test_02_delsecret_respects_required_cas(self):
        name = "required/del"
        self.write(name, {"remove": 1, "preserve": 2})
        api("POST", f"kv2/metadata/{self.p}/{name}", {"cas_required": True})
        result, _ = invoke(f"delsecret {self.secret(name)} remove", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(name)["data"], {"preserve": 2})
        self.assertEqual(self.metadata(name)["current_version"], 2)

    def test_03_concurrent_updates_cannot_silently_lose_an_update(self):
        name = "race/shared"
        self.write(name, {"base": True})
        synchronized_path = f"/v1/kv2/data/{self.p}/{name}"
        with self.proxy(barrier_path=synchronized_path) as (url, events):
            env = os.environ.copy()
            env.update({
                "VAULT_ADDR": url, "VAULT_TOKEN": ROOT_TOKEN,
                "VAULT_CACERT": str(self.cert),
                "VAULT_KV_VERSION": "2", "VAULT_KV_MOUNTS": "kv2,team/config",
                "TERM": "dumb",
            })
            with tempfile.TemporaryDirectory(prefix="vcli-cas-concurrent-") as tmp:
                env["HOME"] = tmp
                env["TMPDIR"] = tmp
                procs = [
                    subprocess.Popen([str(VCLI), "-n"], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
                    for _ in range(2)
                ]
                # Both writers must read version 1 before either may mutate.
                inputs = [
                    f'addsecret {self.secret(name)} {{"left":true}}\n',
                    f'addsecret {self.secret(name)} {{"right":true}}\n',
                ]
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda pair: pair[0].communicate(pair[1], timeout=13),
                        zip(procs, inputs),
                    ))
                codes = [p.returncode for p in procs]
        self.assertEqual(sorted(codes), [0, 1], (codes, results, events))
        obj = self.read(name)["data"]
        self.assertTrue(obj["base"])
        self.assertEqual(int("left" in obj) + int("right" in obj), 1, obj)
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertEqual(len([e for e in events if e[0] == "GET" and e[1] == synchronized_path]), 2)

    def test_04_cptree_race_must_not_overwrite_concurrent_destination(self):
        name = "copy-race/dest/a"
        self.write("copy-race/src/a", {"source": True})
        target = f"/v1/kv2/data/{self.p}/{name}"
        with self.proxy(takeover_path=target, takeover_value=(name, {"racer": True})) as (url, events):
            result, _ = invoke(
                f"cptree {self.secret('copy-race/src')} {self.secret('copy-race/dest')}",
                addr=url, version=2, env_extra={"VAULT_CACERT": str(self.cert)},
            )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(name)["data"], {"racer": True})
        self.assertEqual(self.metadata(name)["current_version"], 1)
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)

    def test_05_put_respects_required_cas_on_existing_version(self):
        name = "put-required/value"
        self.write(name, {"old": True})
        api("POST", f"kv2/metadata/{self.p}/{name}", {"cas_required": True})
        out, _ = invoke(f'put {self.secret(name)} {{"new":true}}', version=2)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(name)["data"], {"new": True})
        self.assertEqual(self.metadata(name)["current_version"], 2)

    def test_06_put_race_rejects_stale_version(self):
        name = "put-race/value"
        self.write(name, {"original": True})
        target = f"/v1/kv2/data/{self.p}/{name}"
        with self.proxy(takeover_path=target, takeover_value=(name, {"racer": True})) as (url, events):
            out, _ = invoke(f'put {self.secret(name)} {{"mine":true}}',
                            version=2, addr=url,
                            env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(name)["data"], {"racer": True})
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)

    def test_07_cp_refuses_existing_destination_without_mutating_it(self):
        src, dest = "cp-collision/source", "cp-collision/dest"
        self.write(src, {"source": True})
        self.write(dest, {"existing": True})
        out, _ = invoke(f"cp {self.secret(src)} {self.secret(dest)}", version=2)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(src)["data"], {"source": True})
        self.assertEqual(self.read(dest)["data"], {"existing": True})
        self.assertEqual(self.metadata(dest)["current_version"], 1)

    def test_08_cp_race_rejects_concurrent_destination_creation(self):
        src, dest = "cp-race/source", "cp-race/dest"
        self.write(src, {"source": True})
        target = f"/v1/kv2/data/{self.p}/{dest}"
        with self.proxy(takeover_path=target, takeover_value=(dest, {"racer": True})) as (url, events):
            out, _ = invoke(f"cp {self.secret(src)} {self.secret(dest)}",
                            addr=url, version=2,
                            env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(dest)["data"], {"racer": True})
        self.assertEqual(self.metadata(dest)["current_version"], 1)
        self.assertEqual(self.read(src)["data"], {"source": True})
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)

    def test_09_replace_race_rejects_concurrent_modification(self):
        name = "replace-race/tree/a"
        self.write(name, {"v": "old"})
        target = f"/v1/kv2/data/{self.p}/{name}"
        with self.proxy(takeover_path=target, takeover_value=(name, {"v": "racer"})) as (url, events):
            out, _ = invoke(
                f'replace {self.secret("replace-race/tree")} {{"v":"old"}} {{"v":"new"}}',
                version=2, addr=url, env_extra={"VAULT_CACERT": str(self.cert)}
            )
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(name)["data"], {"v": "racer"})
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)

    def test_10_restore_race_does_not_overwrite_concurrent_change(self):
        import io
        import tarfile
        name = "restore-race/value"
        self.write(name, {"before": True})
        target = f"/v1/kv2/data/{self.p}/{name}"
        with tempfile.TemporaryDirectory(prefix="vcli-cas-restore-") as root:
            archive = Path(root) / "sample.tgz"
            content = b'{"restored":true}'
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo(self.secret(name))
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
            with self.proxy(takeover_path=target, takeover_value=(name, {"racer": True})) as (url, events):
                out, _ = invoke(f"restore {archive}", version=2, addr=url,
                                env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(name)["data"], {"racer": True})
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)

    def test_11_mv_collision_never_deletes_source(self):
        src, dest = "move-collision/source", "move-collision/dest"
        self.write(src, {"must": "survive"})
        self.write(dest, {"existing": True})
        out, _ = invoke(f"mv {self.secret(src)} {self.secret(dest)}", version=2)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.read(src)["data"], {"must": "survive"})
        self.assertEqual(self.read(dest)["data"], {"existing": True})
        self.assertEqual(self.metadata(src)["current_version"], 1)



    def test_12_mv_kv2_soft_deletes_exact_source_version(self):
        src, dest = "move-version/src", "move-version/dest"
        self.write(src, {"original": "v1"})
        result, _ = invoke(f"mv {self.secret(src)} {self.secret(dest)}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(dest)["data"], {"original": "v1"})
        with self.assertRaises(error.HTTPError) as caught:
            self.read(src)
        self.assertEqual(caught.exception.code, 404)
        meta = self.metadata(src)
        self.assertEqual(meta["current_version"], 1)
        self.assertTrue(meta["versions"]["1"]["deletion_time"])

    def test_13_mv_detects_source_change_after_copy_and_preserves_latest(self):
        src, dest = "move-after-copy/src", "move-after-copy/dest"
        self.write(src, {"before": "move"})
        destination_post = f"/v1/kv2/data/{self.p}/{dest}"
        with self.proxy(takeover_after_post=(destination_post, src, {"newer": "writer"})) as (url, events):
            out, _ = invoke(f"mv {self.secret(src)} {self.secret(dest)}",
                            version=2, addr=url,
                            env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PARTIAL mv", out.stderr)
        self.assertIn("completed=1", out.stderr)
        self.assertEqual(self.read(src)["data"], {"newer": "writer"})
        self.assertEqual(self.metadata(src)["current_version"], 2)
        self.assertEqual(self.read(dest)["data"], {"before": "move"})
        self.assertFalse(any(m == "DELETE" and "/data/" in p for m, p, _ in events))
        self.assertFalse(any(m == "POST" and "/delete/" in p for m, p, _ in events))

    def test_14_mv_uses_version_specific_delete_to_preserve_late_writer(self):
        src, dest = "move-delete-race/src", "move-delete-race/dest"
        self.write(src, {"old": 1})
        delete_target = f"/v1/kv2/delete/{self.p}/{src}"
        with self.proxy(takeover_before_delete=(delete_target, src, {"newer": 2})) as (url, events):
            out, _ = invoke(f"mv {self.secret(src)} {self.secret(dest)}",
                            version=2, addr=url,
                            env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PARTIAL mv", out.stderr)
        self.assertEqual(self.read(src)["data"], {"newer": 2})
        self.assertEqual(self.read(dest)["data"], {"old": 1})
        self.assertEqual(self.metadata(src)["current_version"], 2)
        self.assertFalse(any(m == "DELETE" and "/data/" in p for m, p, _ in events))
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == delete_target]), 1)

    def test_15_mv_supports_nested_kv2_mount(self):
        src, dest = "nested-move/src", "nested-move/dest"
        api("POST", f"team/config/data/{self.p}/{src}", {"data": {"nested": True}})
        out, _ = invoke(
            f"mv team/config/{self.p}/{src} team/config/{self.p}/{dest}",
            version=2,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        copy = api("GET", f"team/config/data/{self.p}/{dest}")
        self.assertEqual(copy["data"]["data"], {"nested": True})
        with self.assertRaises(error.HTTPError) as caught:
            api("GET", f"team/config/data/{self.p}/{src}")
        self.assertEqual(caught.exception.code, 404)
        meta = api("GET", f"team/config/metadata/{self.p}/{src}")["data"]
        self.assertEqual(meta["current_version"], 1)
        self.assertTrue(meta["versions"]["1"]["deletion_time"])


    def test_16_rm_kv2_only_soft_deletes_captured_version(self):
        name = "delete-version/item"
        self.write(name, {"first": True})
        self.write(name, {"second": True})
        result, _ = invoke(f"rm {self.secret(name)}", version=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.assertRaises(error.HTTPError) as caught:
            self.read(name)
        self.assertEqual(caught.exception.code, 404)
        versions = self.metadata(name)["versions"]
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertFalse(versions["1"]["deletion_time"])
        self.assertTrue(versions["2"]["deletion_time"])
        self.assertFalse(versions["2"]["destroyed"])
        # Historical data must remain restorable after a logical deletion.
        recovered = api("GET", f"kv2/data/{self.p}/{name}?version=1")
        self.assertEqual(recovered["data"]["data"], {"first": True})

    def test_17_rm_does_not_delete_concurrent_new_version(self):
        name = "delete-race/item"
        self.write(name, {"before": 1})
        target = f"/v1/kv2/delete/{self.p}/{name}"
        with self.proxy(takeover_before_delete=(target, name, {"newer": 2})) as (url, events):
            out, history = invoke(f"rm {self.secret(name)}", version=2, addr=url,
                                  env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("source advanced", out.stderr.lower())
        self.assertEqual(self.read(name)["data"], {"newer": 2})
        versions = self.metadata(name)["versions"]
        self.assertEqual(self.metadata(name)["current_version"], 2)
        self.assertTrue(versions["1"]["deletion_time"])
        self.assertFalse(versions["2"]["deletion_time"])
        self.assertEqual(len([e for e in events if e[0] == "POST" and e[1] == target]), 1)
        self.assertFalse(any(m == "DELETE" and "/data/" in p for m, p, _ in events))

    def test_18_rmtree_works_with_read_list_and_version_delete_only(self):
        name = "leastpriv/tree"
        self.write(f"{name}/a", {"id": "a"})
        self.write(f"{name}/sub/b", {"id": "b"})
        self.write("leastpriv/sentinel", {"keep": True})
        policy = f"vcli-least-delete-{uuid4().hex[:12]}"
        rules = (
            f'path "kv2/data/{self.p}/*" {{ capabilities = ["read"] }}\n'
            f'path "kv2/metadata/{self.p}/*" {{ capabilities = ["list"] }}\n'
            f'path "kv2/delete/{self.p}/*" {{ capabilities = ["update"] }}\n'
        )
        api("PUT", f"sys/policies/acl/{policy}", {"policy": rules})
        limited = api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "10m"
        })["auth"]["client_token"]
        try:
            out, history = invoke(f"rmtree -f {self.secret(name)}", version=2,
                                  token=limited)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertNotIn(limited, out.stdout + out.stderr + history)
            for leaf in (f"{name}/a", f"{name}/sub/b"):
                with self.subTest(leaf=leaf):
                    with self.assertRaises(error.HTTPError) as caught:
                        self.read(leaf)
                    self.assertEqual(caught.exception.code, 404)
                    info = self.metadata(leaf)["versions"]["1"]
                    self.assertTrue(info["deletion_time"])
                    self.assertFalse(info["destroyed"])
            self.assertEqual(self.read("leastpriv/sentinel")["data"], {"keep": True})
            # The token cannot destroy versions or metadata.
            with self.assertRaises(error.HTTPError) as caught:
                api("POST", f"kv2/destroy/{self.p}/{name}/a",
                    {"versions": [1]}, token=limited)
            self.assertEqual(caught.exception.code, 403)
        finally:
            api("POST", "auth/token/revoke", {"token": limited})

    def test_19_rm_does_not_mutate_when_version_delete_permission_missing(self):
        name = "deny-version-delete/item"
        self.write(name, {"retain": True})
        policy = f"vcli-no-version-delete-{uuid4().hex[:12]}"
        rules = f'path "kv2/data/{self.p}/*" {{ capabilities = ["read"] }}\n'
        api("PUT", f"sys/policies/acl/{policy}", {"policy": rules})
        limited = api("POST", "auth/token/create", {
            "policies": [policy], "no_default_policy": True, "ttl": "10m"
        })["auth"]["client_token"]
        try:
            out, _ = invoke(f"rm {self.secret(name)}", version=2, token=limited)
            self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(self.read(name)["data"], {"retain": True})
            info = self.metadata(name)["versions"]["1"]
            self.assertFalse(info["deletion_time"])
            self.assertFalse(info["destroyed"])
        finally:
            api("POST", "auth/token/revoke", {"token": limited})

    def test_20_rm_rejects_deleted_source_without_history_destruction(self):
        name = "already-soft-deleted/item"
        self.write(name, {"original": True})
        api("DELETE", f"kv2/data/{self.p}/{name}")
        out, _ = invoke(f"rm {self.secret(name)}", version=2)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        meta = self.metadata(name)
        self.assertEqual(meta["current_version"], 1)
        self.assertTrue(meta["versions"]["1"]["deletion_time"])
        self.assertFalse(meta["versions"]["1"]["destroyed"])


if __name__ == "__main__":
    unittest.main()
