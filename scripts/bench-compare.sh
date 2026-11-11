#!/usr/bin/env bash
# Compare a prior vcli revision with the working Bash CLI on one throwaway Vault.
# Usage: scripts/bench-compare.sh <baseline-git-ref> [sizes] [repeats]
set -Eeuo pipefail
umask 077

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ $# -ge 1 && $# -le 3 ]] || {
  echo 'Usage: scripts/bench-compare.sh <baseline-git-ref> [10,100] [repeats]' >&2
  exit 2
}
command -v git >/dev/null && command -v docker >/dev/null || {
  echo 'git and local Docker are required' >&2; exit 2
}
[[ -x ./vcli ]] || { echo 'Missing executable ./vcli' >&2; exit 2; }
# Resolve a commit rather than allowing an arbitrary git-show path expression.
commit=$(git rev-parse --verify --end-of-options "${1}^{commit}") || exit 2
git cat-file -e "$commit:vcli" || {
  echo 'Selected revision does not contain vcli' >&2; exit 2
}
temp=$(mktemp -d "${TMPDIR:-/tmp}/vcli-bench-compare.XXXXXXXX")
cleanup(){ rm -rf -- "$temp"; }
trap cleanup EXIT
git show "$commit:vcli" > "$temp/baseline"
chmod 700 "$temp/baseline"
echo "Comparing baseline ${commit:0:12} with working vcli on one disposable TLS Vault" >&2
VCLI_BENCH_BASELINE="$temp/baseline" \
VCLI_BENCH_CANDIDATE="$PWD/vcli" \
VCLI_BENCH_ONLY=1 \
VCLI_BENCH_RECURSIVE=1 \
VCLI_TEST_VAULT_TAG="${VCLI_TEST_VAULT_TAG:-2.1.1}" \
VCLI_BENCH_SIZES="${2:-10,100}" \
VCLI_BENCH_REPEATS="${3:-4}" \
bash tests/integration/run.sh
