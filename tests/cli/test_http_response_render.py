"""04-Q HTTP-02: contract for the production JSON renderer."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"

def renderer():
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^function vcli_emit_http_response\(\)\{.*?^\}", source, re.M | re.S)
    if match is None:
        raise AssertionError("Missing production JSON renderer")
    return match.group(0)

class HttpResponseRenderContracts(unittest.TestCase):
    def run_render(self, body, status="200", method="GET", data="", kv2="no", cas=""):
        with tempfile.TemporaryDirectory(prefix="vcli-http-json-") as tmp:
            path = Path(tmp) / "response"
            path.write_text(body, encoding="utf-8")
            script = renderer() + '\nvcli_emit_http_response "$FILE" "$CODE" "$METHOD" "$DATA" "$KV2" "$CAS"\n'
            env = os.environ.copy()
            env.update({"FILE": str(path), "CODE": status, "METHOD": method,
                        "DATA": data, "KV2": kv2, "CAS": cas})
            return subprocess.run(["bash", "-c", script], env=env,
                                  capture_output=True, text=True, timeout=8)

    def test_read_object(self):
        r = self.run_render('{"data":{"value":1}}\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"value": 1', r.stdout)

    def test_successful_reads_fail_closed_for_empty_nonobject_or_invalid_json(self):
        for method in ("GET", "LIST"):
            for body in ("", "null\n", "[]\n", "not-json\n"):
                with self.subTest(method=method, body=body):
                    r = self.run_render(body, method=method)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(r.stdout, "")
                    self.assertIn("invalid or empty JSON", r.stderr)

    def test_kv2_unwrap_and_raw(self):
        body = '{"data":{"data":{"password":"synthetic"},"metadata":{"version":1}}}'
        r = self.run_render(body, kv2="yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"password": "synthetic"', r.stdout)
        self.assertNotIn('"metadata":', r.stdout)
        raw = self.run_render(body, kv2="yes", cas="raw")
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertIn('"metadata":', raw.stdout)

    def test_root_listing(self):
        r = self.run_render('{"data":{"alpha":{},"beta":{}}}', data="-_list_root_-")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["alpha", "beta"])

    def test_error_http_body_is_preserved(self):
        r = self.run_render('{"errors":["denied"]}', status="403")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"denied"', r.stdout)

    def test_malformed_error_json_fails(self):
        r = self.run_render("not-json", status="403")
        self.assertNotEqual(r.returncode, 0)

    def test_response_cleanup_after_curl_is_single_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"^function _curl\(\)\{.*?^\}", source, re.M | re.S)
        self.assertIsNotNone(match)
        after_transport = match.group(0).split("    curl_status=$?\n", 1)[1]
        self.assertEqual(after_transport.count('rm -f "$response_file"'), 1)
        self.assertIn('vcli_emit_http_response "$response_file"', after_transport)
        self.assertIn('(( render_status == 0 )) || return 1', after_transport)

if __name__ == "__main__":
    unittest.main()
