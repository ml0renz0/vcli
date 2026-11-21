"""Portable binary keytab decoding: GNU and Apple BSD base64 share -d."""
from __future__ import annotations

import base64
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

VCLI = Path(__file__).resolve().parents[2] / "vcli"


class Base64DecoderPortability(unittest.TestCase):
    def test_production_decode_uses_common_short_option(self):
        source = VCLI.read_text(encoding="utf-8")
        # Both PKI/Kerberos pipelines decode binary data, including NUL bytes.
        self.assertEqual(source.count("base64 -d"), 2)
        self.assertNotIn("base64 --decode", source)

    def test_host_base64_decodes_binary_keytab_like_bytes_exactly(self):
        binary = b"\x05\x02\x00\x00\x00\x0euser@EXAMPLE\x00\xff\xfe\x10"
        encoded = base64.b64encode(binary)
        program = shutil.which("base64")
        self.assertIsNotNone(program)
        result = subprocess.run([program, "-d"], input=encoded,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, binary)

    @unittest.skipUnless(sys.platform == "darwin", "Apple system decoder only")
    def test_apple_bsd_base64_binary_roundtrip_without_homebrew(self):
        binary = b"\x05\x02\x00\x00\x01\xff\x80\x00keytab\x00"
        encoded = base64.b64encode(binary)
        native = Path("/usr/bin/base64")
        self.assertTrue(native.exists())
        result = subprocess.run([str(native), "-d"], input=encoded,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, binary)


if __name__ == "__main__":
    unittest.main()
