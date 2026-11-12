"""Opt-in recursive-operation benchmark against the disposable native-TLS Vault.

Enabled through VCLI_BENCH_RECURSIVE=1 in tests/integration/run.sh. No timing
thresholds: compare the same fixture and runner with baseline/candidate CLIs.
Never print credentials or secret payloads. No production Vault is permitted.
"""
from __future__ import annotations

import math
import re
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
    backend = os.getenv("VCLI_BENCH_STORAGE", "inmem")
    token = os.getenv("VAULT_TOKEN", "")
    ca = os.getenv("VAULT_CACERT", "")
    if not (re.fullmatch(r"https://127\.0\.0\.1:[0-9]{2,5}", VAULT)
            and os.getenv("VCLI_TEST_READONLY_TOKEN")
            and os.path.isfile(ca) and token):
        raise SystemExit("Refusing benchmark outside disposable HTTPS Vault")
    if backend == "inmem":
        if token != ROOT_TOKEN:
            raise SystemExit("Unexpected token for the ephemeral dev Vault")
    elif backend in ("raft-1", "raft-3"):
        if token == ROOT_TOKEN:
            raise SystemExit("Raft must use its own freshly initialized token")
        # Verify the actual leader and voters rather than trusting env flags.
        config = api("GET", "sys/storage/raft/configuration", token=token)
        peers = config["data"]["config"]["servers"]
        voters = int(backend[-1])
        if (len(peers) != voters or
                sum(bool(p.get("voter")) for p in peers) != voters or
                sum(bool(p.get("leader")) for p in peers) != 1):
            raise SystemExit("Raft peer/voter topology does not match benchmark profile")
    else:
        raise SystemExit("Unknown VCLI_BENCH_STORAGE")
    if not SIZES or len(set(SIZES)) != len(SIZES) or any(n < 1 or n > 1000 for n in SIZES):
        raise SystemExit("VCLI_BENCH_SIZES must contain distinct numbers from 1 to 1000")
    if REPEATS < 2 or REPEATS > 20:
        raise SystemExit("VCLI_BENCH_REPEATS must be within 2..20")


def run_cli(binary: Path, command: str, expected_count: int) -> tuple[float, str]:
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
        if env["VAULT_TOKEN"] in result.stdout + result.stderr:
            raise AssertionError("Credential leaked by CLI output")
        if command.startswith("backup "):
            files = list(Path(home).glob("backup_vault*.tgz"))
            if len(files) != 1:
                raise AssertionError("Backup did not create exactly one archive")
            with tarfile.open(files[0], "r:gz") as archive:
                members = [m for m in archive if m.isfile()]
                if len(members) != expected_count:
                    raise AssertionError(f"Backup count: {len(members)} != {expected_count}")
        return ms, result.stdout


def report(label: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    print(
        f"{label}: n={len(samples)} median={statistics.median(samples):.1f}ms "
        f"p95={p95:.1f}ms max={max(samples):.1f}ms",
        flush=True,
    )


def run_set(mount: str, size: int, candidate: Path, baseline: Path | None) -> None:
    # Isolate each cardinality: 100 -> 10 must really measure ten, not 100.
    source = f"{mount}/tree-size-{size}"
    for i in range(size):
        key = f"branch{i % 10:02d}/secret{i:04d}"
        api("POST", f"{source}/{key}", {
            "marker": "matched" if i == 0 else "other", "index": i,
        }, token=os.environ["VAULT_TOKEN"])

    for operation in ("tree", "find", "cptree", "backup"):
        samples = {"candidate": []}
        if baseline is not None:
            samples["baseline"] = []
        for rep in range(REPEATS):
            # Alternate A/B order on the same dataset to limit warm-cache
            # and runner drift; destination paths are unique per attempt.
            binaries = [("candidate", candidate)]
            if baseline is not None:
                binaries.append(("baseline", baseline))
                if rep % 2 == 0:
                    binaries.reverse()

            for label, binary in binaries:
                target = f"{mount}/destination-{size}-{label}-{rep}"
                if operation == "tree":
                    cmd = f"tree {source}/"
                elif operation == "find":
                    cmd = f'find {source} {{"marker":"matched","index":0}}'
                elif operation == "cptree":
                    cmd = f"cptree {source} {target}"
                else:
                    cmd = f"backup {source}"

                elapsed, stdout = run_cli(binary, cmd, size)
                if operation == "tree":
                    # A successful exit is not proof that the whole tree was read.
                    found = re.findall(r"secret[0-9]{4}", stdout)
                    if len(found) != size or len(set(found)) != size:
                        _, listing = run_cli(binary, f"ls {source}/", size)
                        direct = api("LIST", f"{source}/",
                                     token=os.environ["VAULT_TOKEN"])
                        safe = lambda text: text.replace(os.environ["VAULT_TOKEN"], "[REDACTED]")
                        raise AssertionError(
                            f"{label}: tree showed {len(found)} unique leaves, expected {size}; "
                            f"tree={safe(stdout)[:250]!r}, ls={safe(listing)[:250]!r}, "
                            f"api_count={len(direct.get('data', {}).get('keys', []))}"
                        )
                if operation == "find" and "secret0000" not in stdout:
                    raise AssertionError("Find output omitted expected secret")
                if operation == "cptree":
                    for index in (0, size - 1):
                        leaf = f"branch{index % 10:02d}/secret{index:04d}"
                        check = api("GET", f"{target}/{leaf}", token=os.environ["VAULT_TOKEN"])
                        if check["data"]["index"] != index:
                            raise AssertionError("Copied secret was not preserved")
                samples[label].append(elapsed)

        for label, durations in samples.items():
            report(f"size={size} {label}/{operation}", durations)


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
    }, token=os.environ["VAULT_TOKEN"])
    try:
        for size in SIZES:
            print(f"Recursive benchmark ({size} secrets, {REPEATS} samples)", flush=True)
            run_set(mount, size, candidate, baseline)
    finally:
        api("DELETE", f"sys/mounts/{mount}", token=os.environ["VAULT_TOKEN"])


if __name__ == "__main__":
    main()
