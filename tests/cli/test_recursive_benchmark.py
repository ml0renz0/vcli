"""Fast, Vault-free tests for the opt-in recursive benchmark methodology."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests.integration import bench_recursive as bench


class RecursiveBenchmarkDesignTests(TestCase):
    def test_cardinalities_are_isolated_and_pair_order_is_alternated(self):
        writes = []
        calls = []
        reported = []

        def fake_api(method, path, data=None, token=None):
            if method == "POST":
                writes.append(path)
                return {}
            if method == "GET":
                # run_set verifies the first and last destination copy.
                index = int(path.rsplit("secret", 1)[1])
                return {"data": {"index": index}}
            raise AssertionError((method, path))

        def fake_run(binary, command, expected_count):
            calls.append((binary.name, command, expected_count))
            if command.startswith("tree "):
                return 1.0, "\n".join(
                    f"secret{i:04d}" for i in range(expected_count)
                )
            if command.startswith("find "):
                return 1.0, "secret0000\n"
            return 1.0, "ok\n"

        with patch.object(bench, "api", side_effect=fake_api), \
             patch.object(bench, "run_cli", side_effect=fake_run), \
             patch.object(bench, "report",
                          side_effect=lambda label, samples: reported.append(label)), \
             patch.object(bench, "REPEATS", 2):
            # Deliberately descending order to expose size contamination.
            bench.run_set("fixture", 100, Path("/tmp/base"), Path("/tmp/ref"))
            bench.run_set("fixture", 10, Path("/tmp/base"), Path("/tmp/ref"))

        source_writes = [p for p in writes if "/tree-size-" in p]
        self.assertEqual(len(source_writes), 110)
        sizes = Counter(p.split("/")[1] for p in source_writes)
        self.assertEqual(sizes, {"tree-size-100": 100, "tree-size-10": 10})
        tree_calls = [c for c in calls if c[1].startswith("tree ")]
        self.assertEqual(
            [(name, command, n) for name, command, n in tree_calls],
            [
                ("ref", "tree fixture/tree-size-100", 100),
                ("base", "tree fixture/tree-size-100", 100),
                ("base", "tree fixture/tree-size-100", 100),
                ("ref", "tree fixture/tree-size-100", 100),
                ("ref", "tree fixture/tree-size-10", 10),
                ("base", "tree fixture/tree-size-10", 10),
                ("base", "tree fixture/tree-size-10", 10),
                ("ref", "tree fixture/tree-size-10", 10),
            ],
        )
        self.assertEqual(len(reported), 16)

    def test_rejects_incomplete_tree_output(self):
        def fake_api(method, path, data=None, token=None):
            return {}

        with patch.object(bench, "api", side_effect=fake_api), \
             patch.object(bench, "run_cli", return_value=(1.0, "secret0000")), \
             patch.object(bench, "REPEATS", 2):
            with self.assertRaisesRegex(AssertionError, "expected 3"):
                bench.run_set("fixture", 3, Path("/tmp/base"), None)


if __name__ == "__main__":
    import unittest
    unittest.main()
