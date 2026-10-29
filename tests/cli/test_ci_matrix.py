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


if __name__ == "__main__":
    unittest.main()
