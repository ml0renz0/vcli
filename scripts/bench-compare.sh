#!/usr/bin/env bash
# Compare a prior vcli revision with the working Bash CLI on one throwaway Vault.
# Usage: scripts/bench-compare.sh <baseline-git-ref|none> [sizes] [repeats]
set -Eeuo pipefail
umask 077

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ $# -ge 1 && $# -le 3 ]] || {
  echo 'Usage: scripts/bench-compare.sh <baseline-git-ref|none> [10,100] [repeats]' >&2
  exit 2
}
command -v git >/dev/null && command -v docker >/dev/null || {
  echo 'git and local Docker are required' >&2; exit 2
}
[[ -x ./vcli ]] || { echo 'Missing executable ./vcli' >&2; exit 2; }
# "none" measures only the current candidate. A/B comparisons still
# require an explicit, intact historical vcli executable.
baseline=''
temp=''
if [[ $1 != none ]]; then
  commit=$(git rev-parse --verify --end-of-options "${1}^{commit}") || exit 2
  git cat-file -e "$commit:vcli" || {
    echo 'Selected revision does not contain vcli' >&2; exit 2
  }
  temp=$(mktemp -d "${TMPDIR:-/tmp}/vcli-bench-compare.XXXXXXXX")
  cleanup(){ rm -rf -- "$temp"; }
  trap cleanup EXIT
  git show "$commit:vcli" > "$temp/baseline"
  chmod 700 "$temp/baseline"
  baseline="$temp/baseline"
  echo "Comparing baseline ${commit:0:12} with working vcli on one disposable TLS Vault" >&2
else
  echo "Measuring working vcli only on one disposable TLS Vault" >&2
fi
VCLI_BENCH_BASELINE="$baseline" \
VCLI_BENCH_CANDIDATE="$PWD/vcli" \
VCLI_BENCH_ONLY=1 \
VCLI_BENCH_RECURSIVE=1 \
VCLI_TEST_VAULT_TAG="${VCLI_TEST_VAULT_TAG:-2.1.1}" \
VCLI_BENCH_SIZES="${2:-10,100}" \
VCLI_BENCH_REPEATS="${3:-4}" \
bash tests/integration/run.sh
