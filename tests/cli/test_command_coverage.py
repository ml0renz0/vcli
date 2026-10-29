"""Gate: every public vcli command must have an explicitly assigned E2E suite.

This is an inventory/ownership contract, not line or branch coverage.
Runtime behavior is validated by the real-Vault integration matrix.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "vcli"
INTEGRATION = ROOT / "tests" / "integration"

# Every entry must point to an E2E suite that actually names the command.
# Do not add a command to this map without adding an executable contract.
COVERED_BY = {
    "mount": "test_operations.py", "umount": "test_operations.py",
    "cd": "test_operations.py", "ls": "test_vault.py",
    "lsrole": "test_vault.py", "showrole": "test_vault.py",
    "renew-token": "test_auth_lifecycle.py",
    "lspol": "test_vault.py", "showpol": "test_vault.py",
    "tree": "test_operations.py", "find": "test_operations.py",
    "replace": "test_operations.py", "cptree": "test_operations.py",
    "rmtree": "test_operations.py", "backup": "test_operations.py",
    "restore": "test_operations.py", "cat": "test_vault.py",
    "cp": "test_vault.py", "getcert": "test_pki_kerberos.py",
    "getcertlist": "test_pki_kerberos.py",
    "getkrblist": "test_pki_kerberos.py",
    "getcertexpiration": "test_pki_kerberos.py",
    "getallcertlist": "test_pki_kerberos.py",
    "getallkrblist": "test_pki_kerberos.py",
    "getallcertexpiration": "test_pki_kerberos.py",
    "getsan": "test_pki_kerberos.py",
    "getprinc": "test_pki_kerberos.py",
    "certinfo": "test_pki_kerberos.py",
    "getkrb": "test_pki_kerberos.py",
    "krbinfo": "test_pki_kerberos.py",
    "mv": "test_vault.py", "put": "test_vault.py",
    "addsecret": "test_vault.py", "delsecret": "test_vault.py",
    "rm": "test_vault.py", "help": "test_repl.py",
    "history": "test_repl.py", "exit": "test_session_commands.py",
    "verbose": "test_session_commands.py",
    "enable-debug": "test_session_commands.py",
    "disable-debug": "test_session_commands.py",
    "enable-hexdump-debug": "test_session_commands.py",
    "disable-hexdump-debug": "test_session_commands.py",
}


def declared_commands() -> set[str]:
    source = SOURCE.read_text(encoding="utf-8")
    block = re.search(r"declare -A vcli_commands=\(\s*(.*?)\s*\)\s*declare -A vcli_commands_help=", source, re.S)
    if not block:
        raise AssertionError("Could not locate the public command registry")
    return set(re.findall(r"^\s*\[([^]]+)\]=(arg|noarg)\s*$", block.group(1), re.M))


class CommandCoverageGate(unittest.TestCase):
    def test_every_declared_command_has_a_suite(self):
        commands = {name for name, _ in declared_commands()}
        self.assertEqual(commands, set(COVERED_BY),
                         f"Unmapped commands: {commands - set(COVERED_BY)}; stale: {set(COVERED_BY) - commands}")

    def test_every_assigned_suite_names_the_command(self):
        for command, suite in COVERED_BY.items():
            with self.subTest(command=command, suite=suite):
                path = INTEGRATION / suite
                self.assertTrue(path.is_file(), str(path))
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, r"\bdef test_",
                                 f"{suite} has no executable test methods")
                self.assertRegex(text, r"(?<![\w-])" + re.escape(command) + r"(?![\w-])",
                                 f"{suite} does not reference {command!r}")


if __name__ == "__main__":
    unittest.main()
