"""End-to-end TLS tests: VCLI -> TLS proxy -> real ephemeral Vault.

The TLS proxy is only a test harness; VCLI still exercises its real curl path
against a real Vault backend. The negative test must fail closed.
"""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import subprocess
import tempfile
from threading import Thread
import unittest
from uuid import uuid4

from .test_vault import api, invoke


class ForwardToVault(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def forward(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(size) if size else None
        headers = {key: val for key, val in self.headers.items()
                   if key.lower() not in ("host", "connection", "transfer-encoding")}
        upstream = HTTPConnection("127.0.0.1", 8200, timeout=5)
        try:
            upstream.request(self.command, self.path, body=payload, headers=headers)
            response = upstream.getresponse()
            data = response.read()
            self.send_response(response.status)
            for key, val in response.getheaders():
                if key.lower() not in ("connection", "transfer-encoding", "content-length"):
                    self.send_header(key, val)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            upstream.close()

    do_GET = forward
    do_POST = forward
    do_LIST = forward
    do_DELETE = forward


class VaultTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="vcli-ci-tls-")
        cls.addClassCleanup(cls.workspace.cleanup)
        cert = Path(cls.workspace.name) / "local-ci.crt"
        key = Path(cls.workspace.name) / "local-ci.key"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-sha256", "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ], capture_output=True, check=True, timeout=20)
        cls.cert = cert
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ForwardToVault)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        cls.httpd.socket = tls.wrap_socket(cls.httpd.socket, server_side=True)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)
        worker = Thread(target=cls.httpd.serve_forever, daemon=True)
        worker.start()
        cls.addr = f"https://localhost:{cls.httpd.server_port}"
        cls.path = f"kv1/tls-{uuid4().hex[:12]}"
        api("POST", cls.path, {"through": "valid-tls"})

    def test_tls_unknown_ca_must_fail(self):
        result, _ = invoke(f"cat {self.path}", addr=self.addr)
        self.assertNotEqual(result.returncode, 0,
                            "The client unexpectedly accepted an untrusted CA")
        self.assertNotIn("valid-tls", result.stdout + result.stderr)

    def test_tls_trusted_ca_succeeds(self):
        result, _ = invoke(f"cat {self.path}", addr=self.addr,
                           env_extra={"VAULT_CACERT": str(self.cert)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("valid-tls", result.stdout)

    def test_explicit_skip_verify_works_only_when_set(self):
        result, _ = invoke(f"cat {self.path}", addr=self.addr,
                           env_extra={"VAULT_SKIP_VERIFY": "true"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("valid-tls", result.stdout)


if __name__ == "__main__":
    unittest.main()
