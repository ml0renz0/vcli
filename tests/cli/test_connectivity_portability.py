"""P-05g: startup must reuse curl's transport, not GNU timeout or /dev/tcp.

These isolated tests exercise the production Bash function; the live TLS
integration test verifies its behavior against the throwaway Vault.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


def shell_function(name: str) -> str:
    code = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^function " + re.escape(name) + r"\(\)\{.*?^\}",
                      code, re.M | re.S)
    if not match:
        raise AssertionError(f"Missing production function {name}")
    return match.group(0)


class ConnectivityPortability(unittest.TestCase):
    def test_timeout_is_no_longer_required_or_executed(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn('declare DEPENDENCIES="curl jq mktemp"', text)
        self.assertNotRegex(text, r"(?m)^\s*(?:command\s+)?timeout\s")
        self.assertNotIn("</dev/tcp/", shell_function("check_connectivity"))

    def invoke(self, rc: int):
        with tempfile.TemporaryDirectory(prefix="vcli-health-contract-") as folder:
            capture = Path(folder) / "call"
            source = shell_function("check_connectivity")
            # _curl is stubbed only in this unit contract. The E2E suite
            # invokes the real production _curl against Vault TLS.
            script = r'''
protocol=https
vault_host=127.0.0.1
vault_port=8200
_curl(){
  printf '%s\n' "$*" > "$CAPTURE"
  return "$RC"
}
check_connectivity
'''
            env = os.environ.copy()
            env.update({"CAPTURE": str(capture), "RC": str(rc)})
            out = subprocess.run(["bash", "-c", source + "\n" + script],
                                 capture_output=True, text=True, env=env, timeout=5)
            return out, capture.read_text().strip()

    def test_readonly_health_probe_accepts_any_http_status(self):
        # _curl returns success for HTTP 2xx/4xx/5xx with the -_disable_-f_- flag.
        out, call = self.invoke(0)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(call, "-i -_disable_-f_- /sys/health  GET")
        self.assertEqual(out.stdout + out.stderr, "")

    def test_transport_or_tls_failure_is_not_swallowed(self):
        out, call = self.invoke(60)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(call, "-i -_disable_-f_- /sys/health  GET")
        self.assertIn("Cannot establish connection", out.stderr)
        self.assertNotIn("local-ci-root-only", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
