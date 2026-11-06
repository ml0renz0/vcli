"""Benchmark full VCLI batch-mode Vault reads with TLS validation.

Each sample starts a new Bash CLI process, connects over HTTPS and executes
'cat' against a dedicated secret in an ephemeral, throwaway CI Vault.

Results are informational across architectures; the conservative p95 guard
only rejects stalls or severe unexpected performance regressions.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from uuid import uuid4

from .test_vault import VAULT, ROOT_TOKEN, VCLI, api

SAMPLES = 10
WARMUP = 2
TIMEOUT_SECONDS = 12
P95_BUDGET_MS = 8000


def main() -> None:
    if (os.environ.get("VCLI_TEST_READONLY_TOKEN") is None
            or not VAULT.startswith("https://")
            or not os.environ.get("VAULT_CACERT")):
        raise SystemExit("FAIL: benchmark requires disposable Vault HTTPS fixture")

    leaf = f"vcli-bench-{uuid4().hex[:12]}/read"
    path = f"kv1/{leaf}"
    payload = {"fixture": "minimal-linux-real-vault", "answer": 42}
    api("POST", path, payload)
    try:
        with tempfile.TemporaryDirectory(prefix="vcli-vault-bench-") as home:
            env = os.environ.copy()
            env.update({
                "HOME": home, "TMPDIR": home, "TERM": "dumb",
                "VAULT_ADDR": VAULT, "VAULT_TOKEN": ROOT_TOKEN,
                "VAULT_CACERT": os.environ["VAULT_CACERT"],
                "VAULT_KV_VERSION": "1",
                "VAULT_KV_MOUNTS": "kv2,team/config",
            })
            env.pop("VAULT_SKIP_VERIFY", None)
            durations_ms: list[float] = []
            for i in range(SAMPLES + WARMUP):
                start = time.perf_counter_ns()
                out = subprocess.run(
                    [str(VCLI), "-n"], input=f"cat {path}\n",
                    env=env, capture_output=True, text=True,
                    timeout=TIMEOUT_SECONDS,
                )
                elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
                if out.returncode != 0:
                    raise SystemExit(
                        f"FAIL: Vault read returned {out.returncode}: "
                        f"{out.stderr[-450:]}"
                    )
                try:
                    actual = json.loads(out.stdout)["data"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise SystemExit("FAIL: Vault read returned non-KV response") from exc
                if actual != payload:
                    raise SystemExit("FAIL: Vault read returned incorrect secret")
                if ROOT_TOKEN in out.stdout + out.stderr:
                    raise SystemExit("FAIL: CLI leaked the ephemeral root token")
                if i >= WARMUP:
                    durations_ms.append(elapsed_ms)

            ordered = sorted(durations_ms)
            p95 = ordered[math.ceil(0.95 * SAMPLES) - 1]
            print(
                "VCLI live HTTPS Vault cat (fresh Bash per invocation): "
                f"n={SAMPLES}, median={statistics.median(ordered):.1f} ms, "
                f"p95={p95:.1f} ms, max={ordered[-1]:.1f} ms",
                flush=True,
            )
            if p95 > P95_BUDGET_MS:
                raise SystemExit(
                    f"FAIL: Vault read p95 {p95:.1f} ms exceeds generous "
                    f"CI stall limit {P95_BUDGET_MS} ms"
                )
    finally:
        api("DELETE", path)


if __name__ == "__main__":
    main()
