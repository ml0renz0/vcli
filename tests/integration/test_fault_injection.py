"""HTTPS fault injection contracts for vcli (black-box).

The normal integration suite talks to real Vault. This isolated fixture is
intentionally a hostile HTTP peer for response codes and broken framing which
Vault's dev-mode endpoint cannot be configured to produce deterministically.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import ssl
import subprocess
import tempfile
from threading import Thread
from time import sleep
import unittest

from .test_vault import ROOT_TOKEN, VAULT, api, invoke


class _FaultServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FaultContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("VCLI_TEST_READONLY_TOKEN") is None or not VAULT.startswith("https://"):
            raise RuntimeError("Only run in the real Vault TLS integration job")
        cls.temp = tempfile.TemporaryDirectory(prefix="vcli-fault-certs-")
        cls.cert = Path(cls.temp.name) / "ca.pem"
        cls.key = Path(cls.temp.name) / "server.key"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-nodes", "-days", "1", "-sha256", "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(cls.key), "-out", str(cls.cert),
        ], check=True, capture_output=True, timeout=15)
        cls.token = "fault-fixture-sentinel-token"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def hostile_https(self, *, code=200, body=b'{"data":{"value":"safe"}}',
                      mode="normal", delay=0):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()
            def do_LIST(self):
                self.respond()
            def do_POST(self):
                self.respond()
            def do_DELETE(self):
                self.respond()
            def respond(self):
                content_len = int(self.headers.get("Content-Length", "0"))
                if content_len:
                    self.rfile.read(content_len)
                seen.append((self.command, self.path))
                if delay:
                    sleep(delay)
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    if mode == "truncated":
                        self.send_header("Content-Length", str(len(body) + 999))
                    else:
                        self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass
                if mode == "truncated":
                    self.close_connection = True
            def log_message(self, fmt, *args):
                pass

        server = _FaultServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(self.cert), keyfile=str(self.key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}", seen
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def command(self, command, server, *, timeout="2"):
        output, history = invoke(
            command,
            token=self.token,
            addr=server,
            env_extra={
                "VAULT_CACERT": str(self.cert),
                "VCLI_HTTP_TIMEOUT": timeout,
                "VCLI_CONNECT_TIMEOUT": "1",
            },
        )
        self.assertNotIn(self.token, output.stdout + output.stderr + history)
        return output

    def test_01_http_429_write_not_retried_or_reported_success(self):
        with self.hostile_https(code=429, body=b'{"errors":["rate limited"]}') as (url, seen):
            out = self.command('put kv1/fault-limit {"value":1}', url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(seen, [("POST", "/v1/kv1/fault-limit")])

    def test_02_http_500_read_reports_failure(self):
        with self.hostile_https(code=500, body=b'{"errors":["internal"]}') as (url, seen):
            out = self.command("cat kv1/fault-server", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(seen, [("GET", "/v1/kv1/fault-server")])

    def test_03_http_503_write_not_reported_success(self):
        with self.hostile_https(code=503, body=b'{"errors":["unavailable"]}') as (url, seen):
            out = self.command('put kv1/fault-unavailable {"value":1}', url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)

    def test_04_http_200_malformed_json_read_fails_closed(self):
        with self.hostile_https(code=200, body=b'NOT-A-JSON-RESPONSE') as (url, seen):
            out = self.command("cat kv1/fault-invalid-json", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("NOT-A-JSON-RESPONSE", out.stdout)

    def test_05_http_200_empty_read_is_not_a_valid_secret(self):
        with self.hostile_https(code=200, body=b'') as (url, seen):
            out = self.command("cat kv1/fault-empty-response", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)

    def test_06_truncated_http_body_fails_closed(self):
        with self.hostile_https(code=200, body=b'{"data":{"value":1}}',
                                mode="truncated") as (url, seen):
            out = self.command("cat kv1/fault-truncated", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)

    def test_07_server_timeout_is_bounded_and_nonzero(self):
        with self.hostile_https(code=200, body=b'{"data":{"value":1}}', delay=2.4) as (url, seen):
            out = self.command("cat kv1/fault-timeout", url, timeout="1")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)

    def test_08_auth_rejection_does_not_expose_token(self):
        with self.hostile_https(code=403, body=b'{"errors":["permission denied"]}') as (url, seen):
            out = self.command("cat kv1/fault-unauthorized", url)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
