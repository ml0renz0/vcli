"""Prevent accidental E2E coverage drift between Ubuntu and minimal Linux.

The Vault integration runner has two execution paths: host Ubuntu and the
Alpine/Debian client containers. Both must invoke the same test modules.
This is a static contract: it does not replace running the suites in CI.
"""
from __future__ import annotations

from pathlib import Path
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "integration" / "run.sh"

MANDATORY = (
    "test_vault",
    "test_operations",
    "test_kv2_advanced",
    "test_security",
    "test_fault_injection",
    "test_partial_failures",
    "test_cas_concurrency",
    "test_auth_lifecycle",
    "test_tls",
    "test_tls_advanced",
    "test_pki_kerberos",
    "test_repl",
    "test_session_commands",
)


def test_modules() -> list[list[str]]:
    lines = RUNNER.read_text(encoding="utf-8").splitlines()
    commands = []
    prefix = "python3 -m unittest -v "
    for index, line in enumerate(lines):
        if prefix not in line:
            continue
        command = line.split(prefix, 1)[1].strip()
        while command.endswith("\\"):
            index += 1
            if index >= len(lines):
                raise ValueError("Unterminated multiline E2E command")
            command = command[:-1] + " " + lines[index].strip()
        commands.append(command.split())
    return commands


class VaultMatrixParity(unittest.TestCase):
    def test_minimal_and_ubuntu_run_same_complete_suite(self):
        invocations = test_modules()
        self.assertEqual(
            len(invocations), 2,
            "Expect exactly one E2E invocation per host and minimal path",
        )
        self.assertEqual(
            invocations[0], invocations[1],
            "Alpine/Debian E2E suites diverged from Ubuntu",
        )
        expected = [f"tests.integration.{name}" for name in MANDATORY]
        self.assertEqual(
            invocations[0], expected,
            "Do not silently drop or reorder the mandatory integration modules",
        )


    def test_label_flags_are_explicit_and_never_run_on_synchronize(self):
        workflow = (RUNNER.parents[2] / ".github" / "workflows" / "benchmark-labels.yml").read_text()
        self.assertIn("types: [opened, reopened, synchronize, labeled]", workflow)
        self.assertIn("name: Provision benchmark flag labels", workflow)
        self.assertIn("issues: write", workflow)
        self.assertIn("github.event.action != 'labeled'", workflow)
        self.assertIn("gh api --method POST", workflow)
        for label in ("bench:inmem", "bench:ab-inmem", "bench:raft-1", "bench:raft-3"):
            self.assertIn("github.event.label.name == '" + label + "'", workflow)
            self.assertIn("          " + label + "|", workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("bash scripts/bench-compare.sh", workflow)
        self.assertIn("none 10,100 4", workflow)
        self.assertIn("0b69ae8e7393ffea0f7b80b07b7d601526e2918f 10,100 4", workflow)
        self.assertIn("if [[ \"$BENCH_LABEL\" == 'bench:ab-inmem' ]]", workflow)
        self.assertNotIn("pull_request_target:", workflow)
    def test_macos_safety_job_is_mandatory_and_uses_gnu_runtime(self):
        workflow = (RUNNER.parents[2] / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("name: Native macOS offline safety contracts", workflow)
        self.assertIn("runs-on: macos-15", workflow)
        self.assertIn("brew install bash coreutils jq", workflow)
        self.assertIn("$(brew --prefix coreutils)/libexec/gnubin", workflow)
        for command in ("ln", "mv", "stat", "mktemp"):
            self.assertIn(command + " --version", workflow)
        for test in ("test_backup_publication", "test_local_file_safety",
                     "test_approle_security", "test_session_tempfiles"):
            self.assertIn("tests.cli." + test, workflow)
        self.assertIn("needs: [static, integration, minimal, playground, macos]", workflow)
        self.assertIn('test "$MACOS_RESULT" = success', workflow)
        self.assertIn("Native macOS offline safety and CLI contracts", workflow)

    def test_macos_live_vault_is_pinned_loopback_and_required(self):
        workflow = (RUNNER.parents[2] / ".github" / "workflows" / "ci.yml").read_text()
        runner = RUNNER.parents[1] / "integration" / "run-macos.sh"
        content = runner.read_text(encoding="utf-8")
        self.assertIn("Native macOS Vault 2.1.1 TLS E2E (no Docker)", workflow)
        self.assertIn("bash tests/integration/run-macos.sh", workflow)
        self.assertIn("tests/integration/run-macos.sh", workflow)
        self.assertIn("brew install bash coreutils jq openssl@3 krb5", workflow)
        self.assertIn("needs: [static, integration, minimal, playground, macos]", workflow)
        self.assertIn("releases.hashicorp.com/vault/2.1.1/", content)
        self.assertIn("95d100472b726d889ee380c9335191abdf5b3e6f3108cde48f4f962bfea4f009", content)
        self.assertIn("shasum -a 256 -c -", content)
        self.assertIn("vault server -dev -dev-tls", content)
        self.assertIn("VAULT_ADDR='https://127.0.0.1:8200'", content)
        self.assertIn("trap cleanup EXIT", content)
        self.assertIn("VCLI_TEST_READONLY_TOKEN", content)
        for name in ("test_vault", "test_operations", "test_kv2_advanced",
                     "test_security", "test_fault_injection", "test_partial_failures",
                     "test_cas_concurrency", "test_tls", "test_auth_lifecycle",
                     "test_session_commands"):
            self.assertIn("tests.integration." + name, content)
        self.assertNotIn("docker ", content.lower())
        self.assertNotIn("VAULT_SKIP_VERIFY=true", content)

    def test_macos_runs_every_mandatory_linux_e2e_module_in_order(self):
        mac = (RUNNER.parent / "run-macos.sh").read_text(encoding="utf-8")
        self.assertEqual(mac.count("python3 -m unittest -v "), 1)
        lines = mac.splitlines()
        start = next(i for i, line in enumerate(lines)
                     if "python3 -m unittest -v " in line)
        fragments = []
        for line in lines[start:]:
            fragment = line.strip()
            if not fragment:
                break
            continued = fragment.endswith(chr(92))
            fragments.extend(fragment.rstrip(chr(92)).split())
            if not continued:
                break
        self.assertEqual(fragments[:4], ["python3", "-m", "unittest", "-v"])
        self.assertEqual(fragments[4:], test_modules()[0],
                         "macOS must run the same 13 unmodified E2E suites as Linux")

    def test_manual_benchmark_does_not_repeat_full_vault_matrix(self):
        workflow = (RUNNER.parents[2] / ".github" / "workflows" / "ci.yml").read_text()
        condition = "!(github.event_name == 'workflow_dispatch' && inputs.recursive_benchmark)"
        self.assertEqual(workflow.count("if: ${{ " + condition + " }}"), 3)
        self.assertIn(
            "if: ${{ always() && " + condition + " }}", workflow
        )
        self.assertIn("name: Paired recursion benchmark (one disposable TLS Vault)", workflow)
        self.assertIn("needs: static", workflow)
        self.assertIn("bash scripts/bench-compare.sh", workflow)
        for profile in ("inmem", "raft-1", "raft-3"):
            self.assertIn("'" + profile + "'", workflow)
        self.assertIn("tests/integration/run-raft.sh", workflow)
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("exec bash tests/integration/run-raft.sh", runner)

        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("if [[ ${VCLI_BENCH_ONLY:-0} == 1 ]]; then", runner)
        self.assertIn("python3 -m tests.integration.bench_recursive", runner)



if __name__ == "__main__":
    unittest.main()
