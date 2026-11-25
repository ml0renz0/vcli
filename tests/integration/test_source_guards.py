#!/usr/bin/env python3
"""Fail-closed structural checks; integration tests are the behavioral authority."""
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
required = {
    "KV v2 routing": 'function vcli_kv_path()',
    "TLS opt-out explicit": 'VAULT_SKIP_VERIFY',
    "Vault custom CA": 'VAULT_CACERT',
    "safe curl invocation": 'curl "${args[@]}"',
    "no JSON secret history by default": 'VCLI_HISTORY_SECRETS',
    "modern ACL endpoints": '/sys/policies/acl',
}
for label, needle in required.items():
    if needle not in source:
        raise SystemExit(f"FAIL {label}: missing {needle}")
for forbidden in ('_put secret/-_test_-', '-k -L -X$method', 'eval "$*"'):
    if forbidden in source:
        raise SystemExit(f"FAIL forbidden legacy pattern: {forbidden}")
# Detect accidental duplicated tails after the Bash entry point.
if source.count('main "$@"') != 1 or not source.rstrip().endswith('main "$@"'):
    raise SystemExit('FAIL executable must end at its single main "$@"')
print('Source security invariants: OK')
