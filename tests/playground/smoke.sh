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

# Verify the expanded estate independently of the interactive recording.
for env in development staging production; do
    for app in payments identity catalog; do
        result=$(printf 'ls kv2/enterprise/%s/%s\n' "$env" "$app" | scripts/playground.sh batch readonly)
        for directory in api database messaging; do
            grep -Fq "$directory/" <<< "$result" || { echo "Missing enterprise fixture" >&2; exit 1; }
        done
    done
done
result=$(printf 'find kv1/enterprise/legacy {"username":"shared_svc","password":"FAKE-LEGACY-OLD"}\n' |
    scripts/playground.sh batch readonly)
[[ $(grep -c '/primary' <<< "$result") == 4 ]] || { echo "Missing four rotation fixtures" >&2; exit 1; }

# Opt-in recording, always before destroying the private Vault state.
if [[ -n ${VCLI_PLAYGROUND_CAST:-} ]]; then
    scripts/record-playground-demo.sh "$VCLI_PLAYGROUND_CAST"
    for private in root-token readonly-token role-id secret-id; do
        secret=$(< ".playground/$private")
        if [[ -n $secret ]] && grep -Fq -- "$secret" "$VCLI_PLAYGROUND_CAST"; then
            echo "Refusing to publish an asciicast containing private credentials" >&2
            exit 1
        fi
    done
    python3 - "$VCLI_PLAYGROUND_CAST" <<'PY'
import json
import pathlib
import re
import sys
lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
header = json.loads(lines[0])
assert header["version"] in (2, 3)
events = [json.loads(line) for line in lines[1:]]
assert len(events) >= 10
assert "idle_time_limit" not in header, "recording must preserve real timing"
combined = "".join(item[2] for item in events if item[1] == "o")
combined = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\([A-Za-z])", "", combined)
markers = [
    "scripts/playground.sh shell readonly",
    "FAKE-VCLI-PASSWORD",  # checked after the 403 by the driver
    "scripts/playground.sh shell admin",
    "vcli-playground-reader",
    "exit 0",
]
position = 0
for marker in markers:
    position = combined.index(marker, position) + len(marker)
assert "[403]" in combined
for marker in ("FAKE-ROTATED", "FAKE-KEEP-ANALYTICS", "Vault backup created:",
               "Vault backup restored:", "Removed secrets beneath", "Moved version",
               "Vault token renewed", "payments.production.example.test"):
    assert marker in combined, f"missing demo result: {marker}"
assert "[404]" in combined
assert "vcli (readonly) >" not in combined and "vcli (admin) >" not in combined
for name in ("root-token", "readonly-token", "role-id", "secret-id"):
    secret = pathlib.Path(".playground", name).read_text().strip()
    assert not secret or secret not in combined, "private credentials in decoded output"
print("Validated asciinema:", len(events), "terminal events")
PY
fi

scripts/playground.sh down
scripts/playground.sh down
[[ ! -e .playground && ! -L .playground ]] ||
    { echo 'Playground private state remains after down.' >&2; exit 1; }
trap - EXIT
printf '%s\n' 'Playground lifecycle acceptance PASS'
