"""Advanced TLS contracts for the actual Bash CLI, backed by live HTTPS Vault.

The local TLS gateway supplies precisely crafted certificate chains, forwards
successful requests to the disposable Vault with CA verification, and records
which requests arrived. Rejecting a bad handshake is not simulated.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
from threading import Thread
import unittest
from urllib import error, request
from uuid import uuid4

from .test_vault import ROOT_TOKEN, VAULT, api, invoke


def openssl(*args: str) -> None:
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True,
                   timeout=25)


class TlsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AdvancedTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VAULT.startswith("https://") or not os.getenv("VCLI_TEST_READONLY_TOKEN"):
            raise RuntimeError("Advanced TLS suite requires disposable Vault")
        cls.tmp = tempfile.TemporaryDirectory(prefix="vcli-tls-advanced-")
        cls.base = Path(cls.tmp.name)
        cls.leaf = "kv1/vcli-tls-advanced-" + uuid4().hex[:12]
        api("POST", cls.leaf, {"tls": "tls-trust-chain-verified"})
        cls._root("trusted")
        cls._root("rotated")
        cls._intermediate()
        cls._server_cert("valid", "trusted-int", "IP:127.0.0.1")
        cls._server_cert("wrong-san", "trusted-int", "DNS:wrong-host.invalid")
        cls._expired_cert()
        cls._server_cert("rotated-server", "rotated", "IP:127.0.0.1")
        cls._client_cert()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _root(cls, label):
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-days", "3", "-sha256", "-subj", f"/CN={label} vcli CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", cls.base / f"{label}.key",
                "-out", cls.base / f"{label}.pem")

    @classmethod
    def _intermediate(cls):
        openssl("req", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=VCLI intermediate CA",
                "-keyout", cls.base / "trusted-int.key",
                "-out", cls.base / "trusted-int.csr")
        ext = cls.base / "int.ext"
        ext.write_text(
            "basicConstraints=critical,CA:TRUE,pathlen:0\n"
            "keyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n"
        )
        openssl("x509", "-req", "-in", cls.base / "trusted-int.csr",
                "-CA", cls.base / "trusted.pem",
                "-CAkey", cls.base / "trusted.key",
                "-CAcreateserial", "-days", "3", "-sha256", "-extfile", ext,
                "-out", cls.base / "trusted-int.pem")

    @classmethod
    def _server_cert(cls, name, ca, san):
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=localhost",
                "-keyout", cls.base / f"{name}.key",
                "-out", cls.base / f"{name}.csr")
        ext = cls.base / f"{name}.ext"
        ext.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName={san}\n"
        )
        openssl("x509", "-req", "-in", cls.base / f"{name}.csr",
                "-CA", cls.base / f"{ca}.pem",
                "-CAkey", cls.base / f"{ca}.key",
                "-CAcreateserial", "-days", "3", "-sha256", "-extfile", ext,
                "-out", cls.base / f"{name}.pem")
        chain = (cls.base / f"{name}.pem").read_bytes()
        if ca == "trusted-int":
            chain += (cls.base / "trusted-int.pem").read_bytes()
        (cls.base / f"{name}-chain.pem").write_bytes(chain)

    @classmethod
    def _expired_cert(cls):
        # openssl x509 -days cannot reliably create an already-expired cert;
        # openssl ca accepts absolute validity times, which we verify here.
        name = "expired"
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=localhost", "-keyout", cls.base / f"{name}.key",
                "-out", cls.base / f"{name}.csr")
        (cls.base / "newcerts").mkdir()
        (cls.base / "index.txt").touch()
        (cls.base / "serial").write_text("1000\n")
        conf = cls.base / "ca.cnf"
        conf.write_text(
            "[ca]\ndefault_ca = vcli_ca\n"
            "[vcli_ca]\n"
            f"dir = {cls.base}\n"
            "new_certs_dir = $dir/newcerts\n"
            "database = $dir/index.txt\n"
            "serial = $dir/serial\n"
            "private_key = $dir/trusted-int.key\n"
            "certificate = $dir/trusted-int.pem\n"
            "default_md = sha256\n"
            "policy = local_policy\n"
            "x509_extensions = leaf\n"
            "[local_policy]\ncommonName = supplied\n"
            "[leaf]\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectAltName=IP:127.0.0.1\n"
        )
        openssl("ca", "-batch", "-config", conf,
                "-in", cls.base / f"{name}.csr",
                "-out", cls.base / f"{name}.pem",
                "-startdate", "20190101000000Z",
                "-enddate", "20200101000000Z")
        (cls.base / f"{name}-chain.pem").write_bytes(
            (cls.base / f"{name}.pem").read_bytes() +
            (cls.base / "trusted-int.pem").read_bytes()
        )

    @classmethod
    def _client_cert(cls):
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=VCLI test client",
                "-keyout", cls.base / "client.key",
                "-out", cls.base / "client.csr")
        ext = cls.base / "client.ext"
        ext.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=digitalSignature\n"
            "extendedKeyUsage=clientAuth\n"
        )
        openssl("x509", "-req", "-in", cls.base / "client.csr",
                "-CA", cls.base / "rotated.pem",
                "-CAkey", cls.base / "rotated.key", "-CAcreateserial",
                "-days", "3", "-sha256", "-extfile", ext,
                "-out", cls.base / "client.pem")

    @contextmanager
    def gateway(self, certificate="valid", *, intermediate=True, require_client=False):
        seen = []
        upstream_tls = ssl.create_default_context(cafile=os.environ["VAULT_CACERT"])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.command, self.path))
                upstream = request.Request(
                    VAULT + self.path,
                    headers={"X-Vault-Token": self.headers.get("X-Vault-Token", "")},
                    method="GET",
                )
                try:
                    with request.urlopen(upstream, context=upstream_tls, timeout=6) as resp:
                        status, body = resp.status, resp.read()
                except error.HTTPError as exc:
                    status, body = exc.code, exc.read()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, *_):
                pass

        srv = TlsServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        if require_client:
            tls.load_verify_locations(cafile=str(self.base / "rotated.pem"))
            tls.verify_mode = ssl.CERT_REQUIRED
        cert = (f"{certificate}-chain.pem" if intermediate
                else f"{certificate}.pem")
        tls.load_cert_chain(str(self.base / cert),
                            str(self.base / f"{certificate}.key"))
        srv.socket = tls.wrap_socket(srv.socket, server_side=True)
        task = Thread(target=srv.serve_forever, daemon=True)
        task.start()
        try:
            yield f"https://127.0.0.1:{srv.server_port}", seen, tls
        finally:
            srv.shutdown()
            srv.server_close()
            task.join(timeout=5)

    def execute(self, addr, *, ca="trusted", extra=None):
        override = {"VAULT_CACERT": str(self.base / f"{ca}.pem"),
                    "VAULT_SKIP_VERIFY": "false"}
        if extra:
            override.update(extra)
        out, history = invoke(f"cat {self.leaf}", addr=addr, env_extra=override)
        self.assertNotIn(ROOT_TOKEN, out.stdout + out.stderr + history)
        return out

    def test_01_intermediate_ca_chain_is_accepted_with_root_only(self):
        with self.gateway() as (addr, seen, _):
            out = self.execute(addr)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("tls-trust-chain-verified", out.stdout)
        self.assertEqual(seen, [("GET", "/v1/" + self.leaf)])

    def test_02_missing_intermediate_ca_rejected(self):
        with self.gateway(intermediate=False) as (addr, seen, _):
            out = self.execute(addr)
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("tls-trust-chain-verified", out.stdout + out.stderr)
        self.assertEqual(seen, [])

    def test_03_wrong_san_rejected_with_trusted_chain(self):
        with self.gateway("wrong-san") as (addr, seen, _):
            out = self.execute(addr)
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("tls-trust-chain-verified", out.stdout + out.stderr)
        self.assertEqual(seen, [])

    def test_04_expired_certificate_rejected_with_trusted_chain(self):
        with self.gateway("expired") as (addr, seen, _):
            out = self.execute(addr)
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("tls-trust-chain-verified", out.stdout + out.stderr)
        self.assertEqual(seen, [])

    def test_05_ca_rotation_requires_new_trust_anchor(self):
        with self.gateway() as (addr, seen, tls):
            first = self.execute(addr)
            tls.load_cert_chain(str(self.base / "rotated-server-chain.pem"),
                                str(self.base / "rotated-server.key"))
            rejected = self.execute(addr)
            accepted = self.execute(addr, ca="rotated")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn("tls-trust-chain-verified", rejected.stdout + rejected.stderr)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(len(seen), 2, seen)

    def test_06_mtls_refuses_missing_client_certificate(self):
        with self.gateway("rotated-server", require_client=True) as (addr, seen, _):
            out = self.execute(addr, ca="rotated")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(seen, [])

    def test_07_mtls_works_with_explicit_client_certificate_and_key(self):
        with self.gateway("rotated-server", require_client=True) as (addr, seen, _):
            out = self.execute(addr, ca="rotated", extra={
                "VAULT_CLIENT_CERT": str(self.base / "client.pem"),
                "VAULT_CLIENT_KEY": str(self.base / "client.key"),
            })
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("tls-trust-chain-verified", out.stdout)
        self.assertEqual(seen, [("GET", "/v1/" + self.leaf)])

    def test_08_mtls_rejects_incomplete_client_key_pair(self):
        with self.gateway("rotated-server", require_client=True) as (addr, seen, _):
            out = self.execute(addr, ca="rotated", extra={
                "VAULT_CLIENT_CERT": str(self.base / "client.pem"),
                "VAULT_CLIENT_KEY": "",
            })
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
