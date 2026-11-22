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
        certs = {state: {state + "_crt": state, state + "_key": "FAKE"}
                 for state in ("healthy", "warning", "critical", "expired")}
        rows = [row for tenant in seed.TENANTS
                for row in seed.records(tenant, certs)]
        self.assertEqual(len(rows), 8640)
        self.assertEqual(len({(mount, path) for mount, path, _, _ in rows}), 8640)
        self.assertEqual(Counter(m for m, _, _, _ in rows),
                         {"kv1": 1728, "kv2": 5184, "team/config": 1728})
        self.assertEqual(sum(old is not None for _, _, _, old in rows), 1728)
        self.assertEqual(sum(value == seed.SHARED for _, _, value, _ in rows), 576)
        self.assertEqual(sum("http_keytab" in value for _, _, value, _ in rows), 288)
        self.assertEqual(sum(any(key.endswith("_crt") for key in value)
                             for _, _, value, _ in rows), 288)
        self.assertEqual({state: sum(state + "_crt" in v for _, _, v, _ in rows)
                          for state in certs},
                         {"healthy": 128, "warning": 64, "critical": 64, "expired": 32})
        self.assertEqual(len({v["http_principal"] for _, _, v, _ in rows
                              if "http_principal" in v}), 288)
        self.assertEqual(len({v["http_keytab"] for _, _, v, _ in rows
                              if "http_keytab" in v}), 288)


if __name__ == "__main__":
    unittest.main()
