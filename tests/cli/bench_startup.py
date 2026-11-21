"""Small reproducible VCLI startup benchmark for CI environments.

Report performance, not cross-machine relative rankings. The conservative
maximum-runtime guard catches stalls without making CI sensitive to noise.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

VCLI = Path(__file__).resolve().parents[2] / "vcli"
SAMPLES = 12
TIMEOUT_SECONDS = 5
P95_BUDGET_MS = 2500


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vcli-startup-") as home:
        env = os.environ.copy()
        for name in ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_HOST", "VAULT_PORT",
                     "VAULT_ROLE_ID", "VAULT_SECRET_ID", "VAULT_CACERT"):
            env.pop(name, None)
        env.update({"HOME": home, "TMPDIR": home, "TERM": "dumb"})
        durations_ms = []
        for i in range(SAMPLES + 2):
            start = time.perf_counter_ns()
            result = subprocess.run(
                [str(VCLI), "--version"],
                env=env,
                cwd=home,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
            duration_ms = (time.perf_counter_ns() - start) / 1_000_000
            if result.returncode != 0 or "vcli v" not in result.stdout:
                raise SystemExit(
                    f"FAIL startup benchmark run {i}: {result.returncode}: "
                    f"{result.stderr[-400:]}"
                )
            if i >= 2:  # exclude startup warmup iterations
                durations_ms.append(duration_ms)

        ordered = sorted(durations_ms)
        p95_ms = ordered[math.ceil(0.95 * len(ordered)) - 1]
        median_ms = statistics.median(durations_ms)
        print(
            "VCLI startup --version (no Vault): "
            f"n={len(ordered)}, median={median_ms:.1f} ms, "
            f"p95={p95_ms:.1f} ms, max={ordered[-1]:.1f} ms"
        )
        if p95_ms > P95_BUDGET_MS:
            raise SystemExit(
                f"FAIL: p95 startup {p95_ms:.1f} ms exceeds "
                f"conservative CI budget {P95_BUDGET_MS} ms"
            )


if __name__ == "__main__":
    main()
