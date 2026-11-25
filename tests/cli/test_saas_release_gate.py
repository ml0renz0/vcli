"""Release gate contract: expensive SaaS runs only before candidate artifacts.

The ordinary PR matrix remains mandatory and includes real Vault TLS smoke/E2E.
The heavy 8640-secret job is reusable on release tags and manually callable.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def section(source: str, start: str, end: str) -> str:
    match = re.search(r"^" + re.escape(start) + r"\n(.*?)^(?=" +
                      re.escape(end) + r")", source, re.M | re.S)
    if match is None:
        raise AssertionError(f"Missing workflow section: {start}")
    return match.group(1)


class SaaSReleaseGateContracts(unittest.TestCase):
    def test_heavy_saas_never_auto_runs_on_pr_or_master(self):
        workflow = (WORKFLOWS / "playground-saas.yml").read_text()
        triggers = section(workflow, "on:", "permissions:")
        self.assertIn("workflow_call:", triggers)
        self.assertIn("workflow_dispatch:", triggers)
        self.assertNotRegex(triggers, r"(?m)^\s+(pull_request|push|schedule):")
        self.assertIn("8640-secret SaaS acceptance", workflow)
        self.assertIn("scripts/record-playground-demo.sh vcli-playground-saas.cast", workflow)
        self.assertIn("REST acceptance PASS: 8640 restored values", workflow)
        self.assertIn("Credential in cast", workflow)
        self.assertIn("retention-days: 7", workflow)

    def test_release_tag_requires_successful_saas_before_packaging(self):
        workflow = (WORKFLOWS / "ci.yml").read_text()
        self.assertIn("pull_request:", workflow)
        self.assertIn("needs: [static, integration, minimal, playground, macos]", workflow)
        saas = section(workflow, "  saas_release:", "  package_candidate:")
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v0.7.')", saas)
        self.assertIn("needs: gate", saas)
        self.assertIn("uses: ./.github/workflows/playground-saas.yml", saas)
        candidate = workflow.split("  package_candidate:", 1)[1]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v0.7.')", candidate)
        self.assertIn("needs: [gate, saas_release]", candidate)
        self.assertIn("environment: release", candidate)
        self.assertIn("sha256sum", candidate)
        self.assertNotIn("continue-on-error:", saas + candidate)

    def test_pr_smoke_remains_mandatory_without_saas_seed(self):
        workflow = (WORKFLOWS / "ci.yml").read_text()
        self.assertIn("name: Real Vault TLS playground acceptance", workflow)
        self.assertIn("bash tests/playground/smoke.sh", workflow)
        self.assertIn("test_saas_manifest", workflow)
        self.assertIn('test "$PLAYGROUND_RESULT" = success', workflow)

    def test_verified_tag_artifacts_feed_reports_and_pages(self):
        reports = (WORKFLOWS / "publish-reports.yml").read_text()
        pages = (WORKFLOWS / "publish-demos.yml").read_text()
        for source in (reports, pages):
            self.assertIn("head_repository.full_name == github.repository", source)
            self.assertIn("git ls-remote --tags origin", source)
            self.assertIn("refs/tags/v0.7.*", source)
            self.assertIn("head_sha", source)
            self.assertIn("vcli-playground-saas-asciinema", source)
        self.assertIn("vcli-saas-metrics", reports)
        self.assertIn("TRUSTED_RELEASE_TAG", reports)
        self.assertIn("ci.yml,playground-saas.yml", pages)
        self.assertIn("grep -Fxq \"$sha\"", pages)
        self.assertIn("github.event.workflow_run.event == 'push'", reports)
        self.assertIn("github.event.workflow_run.event == 'push'", pages)


if __name__ == "__main__":
    unittest.main()
