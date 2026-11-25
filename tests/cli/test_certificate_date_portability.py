"""Test the production Bash X.509 expiry parser under GNU/BSD date."""
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"

class CertificateDatePortability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = VCLI.read_text(encoding="utf-8")
        match = re.search(r"^function vcli_cert_expiry_epoch\(\)\{.*?^\}", src,
                          re.MULTILINE | re.DOTALL)
        if match is None:
            raise AssertionError("Missing production Bash X509 date parser")
        cls.helper = match.group(0)

    def invoke(self, value):
        env = os.environ.copy()
        env["TEST_DATE"] = value
        return subprocess.run(["bash", "-c", self.helper +
                               '\nvcli_cert_expiry_epoch "$TEST_DATE"\n'],
                              env=env, text=True, capture_output=True, timeout=5)

    def test_expected_utc_epoch(self):
        samples = [
            ("Nov 19 15:00:00 2026 GMT", datetime(2026,11,19,15,tzinfo=timezone.utc)),
            ("Jan  2 03:04:05 2030 GMT", datetime(2030,1,2,3,4,5,tzinfo=timezone.utc)),
            ("Dec 31 23:59:59 2028 GMT", datetime(2028,12,31,23,59,59,tzinfo=timezone.utc)),
        ]
        for stamp, date in samples:
            with self.subTest(stamp=stamp):
                out = self.invoke("notAfter=" + stamp)
                self.assertEqual(out.returncode,0,out.stderr)
                self.assertEqual(int(out.stdout.strip()),int(date.timestamp()))

    def test_rejects_bad_prefix_timezone_and_shell_code(self):
        for stamp in ("Nov 19 15:00:00 2026 GMT",
                      "notAfter=Nov 19 15:00:00 2026 PST",
                      "notAfter=Nov 19 15:00:00 2026 GMT;echo INJECTED",
                      "notAfter=garbage", ""):
            with self.subTest(stamp=stamp):
                out=self.invoke(stamp)
                self.assertNotEqual(out.returncode,0)
                self.assertNotIn("INJECTED",out.stdout)

    def test_macos_uses_bsd_date_even_with_gnu_in_path(self):
        self.assertIn("/bin/date -u -j -f",self.helper)
        self.assertIn("LC_ALL=C date -u -d",self.helper)

if __name__=="__main__":
    unittest.main()
