"""Opt-in recursive-operation benchmark against the disposable native-TLS Vault.

Enabled through VCLI_BENCH_RECURSIVE=1 in tests/integration/run.sh. No timing
thresholds: compare the same fixture and runner with baseline/candidate CLIs.
Never print credentials or secret payloads. No production Vault is permitted.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tarfile
import tempfile
import time
from uuid import uuid4

from .test_vault import VAULT, ROOT_TOKEN, VCLI, api

SIZES = tuple(int(x) for x in os.getenv("VCLI_BENCH_SIZES", "10,100").split(","))
REPEATS = int(os.getenv("VCLI_BENCH_REPEATS", "3"))
TIMEOUT = int(os.getenv("VCLI_BENCH_TIMEOUT", "300"))


def validate_fixture() -> None:
    if not (VAULT.startswith("https://127.0.0.1:")
            and os.getenv("VCLI_TEST_READONLY_TOKEN")
            and os.getenv("VAULT_CACERT")
            and os.getenv("VAULT_TOKEN") == ROOT_TOKEN):
        raise SystemExit("Refusing benchmark outside disposable HTTPS Vault")
    if not SIZES or any(n < 1 or n > 1000 for n in SIZES):
        raise SystemExit("VCLI_BENCH_SIZES must contain numbers from 1 to 1000")
    if REPEATS < 2 or REPEATS > 20:
        raise SystemExit("VCLI_BENCH_REPEATS must be within 2..20")


def run_cli(binary: Path, command: str, root: str) -> tuple[float, str, Path]:
    with tempfile.TemporaryDirectory(prefix="vcli-recursive-bench-") as home:
        env = os.environ.copy()
        env.update({
            "HOME": home, "TMPDIR": home, "TERM": "dumb",
            "VAULT_ADDR": VAULT, "VAULT_TOKEN": ROOT_TOKEN,
            "VAULT_CACERT": os.environ["VAULT_CACERT"],
            "VAULT_KV_VERSION": "1",
            "VAULT_KV_MOUNTS": "kv2,team/config",
        })
        env.pop("VAULT_SKIP_VERIFY", None)
        started = time.perf_counter_ns()
        result = subprocess.run(
            [str(binary), "-n"], input=command + "\n", env=env,
            text=True, capture_output=True, timeout=TIMEOUT,
        )
        ms = (time.perf_counter_ns() - started) / 1_000_000
        if result.returncode:
            raise AssertionError(
                f"{binary.name}: {command.split()[0]} exited {result.returncode}; "
                f"stderr={result.stderr[-300:]}"
            )
        if ROOT_TOKEN in result.stdout + result.stderr:
            raise AssertionError("Credential leaked by CLI output")
        if command.startswith("backup "):
            files = list(Path(home).glob("backup_vault*.tgz"))
            if len(files) != 1:
                raise AssertionError("Backup did not create exactly one archive")
            with tarfile.open(files[0], "r:gz") as archive:
                members = [m for m in archive if m.isfile()]
                if len(members) != int(root):
                    raise AssertionError(f"Backup count: {len(members)} != {root}")
        return ms, result.stdout, Path(home)


def report(label: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    print(
        f"{label}: n={len(samples)} median={statistics.median(samples):.1f}ms "
        f"p95={p95:.1f}ms max={max(samples):.1f}ms",
        flush=True,
    )


def run_set(mount: str, size: int, candidate: Path, baseline: Path | None) -> None:
    secrets = [f"tree/branch{i % 10:02d}/secret{i:04d}" for i in range(size)]
    for i, key in enumerate(secrets):
        api("POST", f"{mount}/{key}", {"marker": "matched" if i == 0 else "other", "index": i})

    binaries = [("candidate", candidate)]
    if baseline is not None:
        binaries.insert(0, ("baseline", baseline))

    for label, binary in binaries:
        for operation in ("tree", "find", "cptree", "backup"):
            samples = []
            for rep in range(REPEATS):
                target = f"{mount}/destination-{label}-{rep}"
                if operation == "tree":
                    cmd = f"tree {mount}/tree"
                elif operation == "find":
                    cmd = f'find {mount}/tree {{"marker":"matched","index":0}}'
                elif operation == "cptree":
                    cmd = f"cptree {mount}/tree {target}"
                else:
                    cmd = f"backup {mount}/tree"

                elapsed, stdout, _ = run_cli(binary, cmd, str(size))
                if operation == "tree" and "secret0000" not in stdout:
                    raise AssertionError("Tree output omitted first secret")
                if operation == "find" and "secret0000" not in stdout:
                    raise AssertionError("Find output omitted expected secret")
                if operation == "cptree":
                    check = api("GET", f"{target}/branch00/secret0000")
                    if check["data"]["index"] != 0:
                        raise AssertionError("Copied secret was not preserved")
                samples.append(elapsed)
            report(f"size={size} {label}/{operation}", samples)


def main() -> None:
    validate_fixture()
    candidate = Path(os.getenv("VCLI_BENCH_CANDIDATE", str(VCLI))).resolve()
    baseline_arg = os.getenv("VCLI_BENCH_BASELINE")
    baseline = Path(baseline_arg).resolve() if baseline_arg else None
    if not candidate.is_file() or (baseline is not None and not baseline.is_file()):
        raise SystemExit("Benchmark CLI executable not found")
    mount = f"vcli-benchmark-{uuid4().hex[:12]}"
    api("POST", f"sys/mounts/{mount}", {
        "type": "kv", "options": {"version": "1"},
        "description": "throwaway recursive benchmark",
    })
    try:
        for size in SIZES:
            print(f"Recursive benchmark ({size} secrets, {REPEATS} samples)", flush=True)
            run_set(mount, size, candidate, baseline)
    finally:
        api("DELETE", f"sys/mounts/{mount}")


if __name__ == "__main__":
    main()
