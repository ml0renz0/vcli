"""Black-box tests for production Bash URL encoding (no Vault required)."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "vcli"


def production_functions():
    source = SOURCE.read_text(encoding="utf-8")
    found = []
    for name in ("urlencode", "vcli_encode_endpoint"):
        pattern = rf"^function {name}(?:\(\))?\s*\{{.*?^\}}"
        match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
        if not match:
            raise AssertionError(f"Production function {name} not found")
        found.append(match.group(0))
    return "\n\n".join(found)


class EndpointEncodingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = production_functions()

    def encode(self, path):
        cmd = self.functions + '\nvcli_encode_endpoint "$1"'
        result = subprocess.run(
            ["bash", "-c", cmd, "--", path],
            text=True, capture_output=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_endpoint_encoding_and_compatibility(self):
        examples = {
            "kv1/plain": "kv1/plain",
            "/kv2/data/deep/leaf": "kv2/data/deep/leaf",
            "team/config/folder/": "team/config/folder/",
            "kv1/a+b": "kv1/a%2Bb",
            "kv1/a%2Fb": "kv1/a%252Fb",
            "kv1/a#b": "kv1/a%23b",
            "kv1/café": "kv1/caf%C3%A9",
            "kv2/metadata/item?list=true": "kv2/metadata/item?list=true",
            "kv2/data/item?version=42": "kv2/data/item?version=42",
            "a//b///": "a//b///",
            "": "",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.encode(raw), expected)


if __name__ == "__main__":
    unittest.main()
