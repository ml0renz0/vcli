#!/usr/bin/env bash
# Disposable native macOS Vault TLS fixture; never connect to an external Vault.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/../.."

[[ $(uname -s) == Darwin && ${CI:-} == true ]] || {
    echo 'P-03 requires a disposable macOS CI runner' >&2; exit 2;
}
[[ ${VCLI_TEST_VAULT_TAG:-} == 2.1.1 ]] || {
    echo 'P-03 requires the pinned Vault 2.1.1 fixture' >&2; exit 2;
}
readonly expected_sha='95d100472b726d889ee380c9335191abdf5b3e6f3108cde48f4f962bfea4f009'
readonly archive='vault_2.1.1_darwin_arm64.zip'
readonly root_token='local-ci-root-only'

work=$(mktemp -d "${TMPDIR:-/tmp}/vcli-macos-vault.XXXXXXXX") || exit 1
server_pid=''
cleanup() {
    local result=$?
    trap - EXIT INT TERM
    if [[ -n $server_pid ]]; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    rm -rf -- "$work"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p -- "$work/bin"
curl --fail --silent --show-error --location --retry 3 \
    --connect-timeout 15 --max-time 240 \
    --output "$work/$archive" \
    "https://releases.hashicorp.com/vault/2.1.1/$archive"
printf '%s  %s\n' "$expected_sha" "$work/$archive" | shasum -a 256 -c -
unzip -q "$work/$archive" -d "$work/bin"
chmod 755 "$work/bin/vault"
export PATH="$work/bin:$PATH"
vault version | grep -F 'Vault v2.1.1' >/dev/null

# Vault's real TLS listener, local-only, generated CA (never skip verification).
unset VAULT_SKIP_VERIFY VAULT_CAPATH VAULT_NAMESPACE VAULT_HOST VAULT_PORT \
    VAULT_ROLE_ID VAULT_SECRET_ID
export VAULT_ADDR='https://127.0.0.1:8200'
export VAULT_TOKEN="$root_token"
export VAULT_CACERT="$work/vault-ca.pem"
VAULT_DEV_ROOT_TOKEN_ID="$root_token" vault server -dev -dev-tls \
    -dev-tls-cert-dir="$work" -dev-listen-address=127.0.0.1:8200 \
    >"$work/server.log" 2>&1 &
server_pid=$!

ready=no
for ((attempt=1;attempt<=60;attempt++)); do
    if [[ -s $VAULT_CACERT ]] &&
        curl --noproxy '*' --fail --silent --show-error \
             --connect-timeout 1 --cacert "$VAULT_CACERT" \
             "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1; then
        ready=yes
        break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
        echo 'Native TLS Vault exited prematurely' >&2; exit 1
    fi
    sleep 1
done
[[ $ready == yes ]] || { echo 'Native TLS Vault was not ready' >&2; exit 1; }
if curl --noproxy '*' --fail --silent --connect-timeout 1 \
   "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1; then
    echo 'Fixture unexpectedly trusts its self-signed CA' >&2; exit 1
fi

vault secrets enable -path=kv1 -version=1 kv >/dev/null
vault secrets enable -path=kv2 -version=2 kv >/dev/null
vault secrets enable -path=team/config -version=2 kv >/dev/null
vault auth enable approle >/dev/null
vault write auth/approle/role/ci-role token_policies=default >/dev/null

cat <<'HCL' | vault policy write vcli-ci-ro - >/dev/null
path "kv1/*" { capabilities = ["read", "list"] }
path "kv2/data/*" { capabilities = ["read"] }
path "kv2/metadata/*" { capabilities = ["list"] }
path "team/config/data/*" { capabilities = ["read"] }
path "team/config/metadata/*" { capabilities = ["list"] }
HCL
export VCLI_TEST_READONLY_TOKEN
VCLI_TEST_READONLY_TOKEN=$(vault token create -policy=vcli-ci-ro -orphan -field=token)
[[ -n $VCLI_TEST_READONLY_TOKEN ]] || exit 1

bash -n ./vcli
# Identical existing black-box E2E suites, independently checked via Vault API.
python3 -m unittest -v \
    tests.integration.test_vault \
    tests.integration.test_operations \
    tests.integration.test_security \
    tests.integration.test_tls \
    tests.integration.test_auth_lifecycle \
    tests.integration.test_session_commands \
    tests.integration.test_pki_kerberos.PkiKerberosContracts.test_01_getcert_exports_exact_matching_pem_with_private_permissions \
    tests.integration.test_pki_kerberos.PkiKerberosContracts.test_05_getsan_uses_actual_certificate_extensions \
    tests.integration.test_pki_kerberos.PkiKerberosContracts.test_07_getkrb_exports_exact_keytab_with_private_permissions
