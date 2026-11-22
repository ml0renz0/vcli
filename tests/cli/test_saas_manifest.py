"""Validate the extra profile's generator without Docker or network."""
import importlib.util
from collections import Counter
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "seed_saas", Path(__file__).resolve().parents[2] / "scripts/playground-seed-saas.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SaaSManifest(unittest.TestCase):
    def test_full_catalogue_counts_unique_paths_and_variety(self):
        certs = {env: {"dummy_crt": env, "dummy_key": "FAKE"} for env in seed.ENVS}
        rows = [row for tenant in seed.TENANTS
                for row in seed.records(tenant, certs, "synthetic-keytab")]
        self.assertEqual(len(rows), 8640)
        self.assertEqual(len({(mount, path) for mount, path, _, _ in rows}), 8640)
        self.assertEqual(Counter(m for m, _, _, _ in rows),
                         {"kv1": 1728, "kv2": 5184, "team/config": 1728})
        self.assertEqual(sum(old is not None for _, _, _, old in rows), 1728)
        self.assertEqual(sum(value == seed.SHARED for _, _, value, _ in rows), 576)
        self.assertEqual(sum("http_keytab" in value for _, _, value, _ in rows), 288)
        self.assertEqual(sum("dummy_crt" in value for _, _, value, _ in rows), 288)


if __name__ == "__main__":
    unittest.main()
