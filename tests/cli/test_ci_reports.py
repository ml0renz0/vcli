"""Contracts for the offline reporting renderer and trusted workflow."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "render-ci-reports.py"


def load():
    spec = importlib.util.spec_from_file_location("render_ci_reports", MODULE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ReportContracts(unittest.TestCase):
    def test_job_graph_is_measured_and_excludes_skipped_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "jobs.json"
            target = Path(tmp) / "chart.svg"
            source.write_text(json.dumps({"jobs": [
                {"name": "Vault TLS", "conclusion": "success",
                 "started_at": "2026-11-22T13:00:00Z",
                 "completed_at": "2026-11-22T13:02:00Z"},
                {"name": "Skipped", "conclusion": "skipped",
                 "started_at": "2026-11-22T13:00:00Z",
                 "completed_at": "2026-11-22T13:10:00Z"},
            ]}), encoding="utf-8")
            load().render_jobs(source, target)
            content = target.read_text(encoding="utf-8")
            self.assertIn("Vault TLS", content)
            self.assertIn("120.0 s", content)
            self.assertNotIn("Skipped", content)

    def test_saas_refuses_failed_metrics_and_renders_verified_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "metrics.json"
            target = Path(tmp) / "chart.svg"
            data = {"profile": "saas", "status": "failed",
                    "operations": [{"case": "full restore", "seconds": 3.25}]}
            source.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load().render_saas(source, target)
            data["status"] = "passed"
            source.write_text(json.dumps(data), encoding="utf-8")
            load().render_saas(source, target)
            self.assertIn("full restore", target.read_text(encoding="utf-8"))
            self.assertIn("3.2 s", target.read_text(encoding="utf-8"))

    def test_absent_data_never_becomes_fabricated_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "jobs.json"
            source.write_text('{"jobs":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load().render_jobs(source, Path(tmp) / "chart.svg")

    def test_publishing_is_gated_by_success_and_default_branch(self):
        workflow = (ROOT / ".github/workflows/publish-reports.yml").read_text()
        self.assertIn("workflow_run:", workflow)
        self.assertIn("conclusion == 'success'", workflow)
        self.assertIn("head_branch == github.event.repository.default_branch", workflow)
        self.assertIn("gh run download", workflow)
        self.assertIn("[skip ci]", workflow)


if __name__ == "__main__":
    unittest.main()
