#!/usr/bin/env bash
# Full lifecycle acceptance of the REAL local playground against Vault TLS.
# CI runner must have no existing playground; refuse to destroy user data.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
[[ ! -e .playground && ! -L .playground ]] || {
    echo 'Refusing to reset an existing playground.' >&2
    exit 1
}
cleanup() { scripts/playground.sh down >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Hostile operator settings may NOT redirect the playground to an external
# Vault or turn off TLS verification.
export VAULT_ADDR=https://must-not-contact.invalid
export VAULT_TOKEN=external-operator-sentinel
export VAULT_SKIP_VERIFY=true
export VAULT_CACERT=/never/trust/this/ca
export VAULT_NAMESPACE=forbidden-external
scripts/playground.sh up
scripts/playground.sh status
scripts/playground.sh smoke

# Verify the documented manual workflow also operates on the real CLI.
(
    # shellcheck disable=SC1091
    source .playground/env.sh
    [[ $VAULT_ADDR == https://127.0.0.1:* ]]
    [[ $VAULT_CACERT == "$PWD/.playground/ca.pem" ]]
    [[ ! -n ${VAULT_SKIP_VERIFY:-} ]]
    output=$(printf 'cat kv1/demo/app\n' | ./vcli -n)
    grep -Fq 'FAKE-VCLI-PASSWORD' <<< "$output"
)

# Repeated up must not reseed or overwrite manually edited user data.
printf 'put kv1/demo/app {"user":"changed","password":"FAKE-PRESERVED"}\n' |
    scripts/playground.sh batch >/dev/null
scripts/playground.sh up
output=$(printf 'cat kv1/demo/app\n' | scripts/playground.sh batch)
grep -Fq 'FAKE-PRESERVED' <<< "$output"

# Reset is deliberately destructive and must restore the full catalog.
scripts/playground.sh reset
output=$(printf 'cat kv1/demo/app\n' | scripts/playground.sh batch)
grep -Fq 'FAKE-VCLI-PASSWORD' <<< "$output"
if grep -Fq 'FAKE-PRESERVED' <<< "$output"; then
    echo 'Reset failed to restore initial fake content.' >&2
    exit 1
fi
scripts/playground.sh smoke

scripts/playground.sh down
scripts/playground.sh down
[[ ! -e .playground && ! -L .playground ]] ||
    { echo 'Playground private state remains after down.' >&2; exit 1; }
trap - EXIT
printf '%s\n' 'Playground lifecycle acceptance PASS'
