#!/usr/bin/env bash
# Bring up an ephemeral, throwaway Vault and execute contract/integration tests.
# Never point this script at production or reuse credentials from outside CI.
set -Eeuo pipefail

cd "$(dirname "$0")/../.."
: "${VCLI_TEST_VAULT_TAG:?Set a pinned HashiCorp Vault image tag}"

container="vcli-ci-vault-${RANDOM}-$$"
root_token='local-ci-root-only'
tls_dir=$(mktemp -d "${TMPDIR:-/tmp}/vcli-ci-tls.XXXXXX")
export VAULT_ADDR='https://127.0.0.1:8200'
export VAULT_TOKEN="$root_token"
export VAULT_CACERT="$tls_dir/vault-ca.pem"
unset VAULT_SKIP_VERIFY

cleanup() {
  status=$?
  if (( status != 0 )); then
    docker logs "$container" --tail 80 2>&1 |
      sed "s/$root_token/[REDACTED]/g" >&2 || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf -- "$tls_dir"
  exit "$status"
}
trap cleanup EXIT

docker run --detach --rm --name "$container" \
  --publish 127.0.0.1:8200:8200 \
  --env "VAULT_DEV_ROOT_TOKEN_ID=$root_token" \
  "hashicorp/vault:${VCLI_TEST_VAULT_TAG}" \
  server -dev -dev-tls -dev-tls-cert-dir=/tmp -dev-listen-address=0.0.0.0:8200 >/dev/null

ready=no
for ((attempt=1;attempt<=60;attempt++)); do
  # Vault itself generates the CA and serves HTTPS; no TLS proxy.
  if docker cp "$container:/tmp/vault-ca.pem" "$VAULT_CACERT" >/dev/null 2>&1 &&
     [[ -s "$VAULT_CACERT" ]] &&
     curl --fail --silent --show-error --connect-timeout 1 \
       --cacert "$VAULT_CACERT" \
       --header "X-Vault-Token: $root_token" "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1; then
    ready=yes
    break
  fi
  sleep 1
done
[[ $ready == yes ]] || { echo 'TLS Vault did not become healthy' >&2; exit 1; }

# Fail the fixture if the CA is accidentally trusted without opting in.
if curl --fail --silent "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1; then
  echo 'TLS fixture invalid: unknown CA unexpectedly trusted' >&2
  exit 1
fi

v() {
  docker exec \
    --env "VAULT_ADDR=https://127.0.0.1:8200" \
    --env "VAULT_CACERT=/tmp/vault-ca.pem" \
    --env "VAULT_TOKEN=$root_token" \
    "$container" vault "$@"
}

# Distinct v1/v2 mounts, including a nested v2 mount, to expose routing bugs.
v secrets enable -path=kv1 -version=1 kv
v secrets enable -path=kv2 -version=2 kv
v secrets enable -path=team/config -version=2 kv
v auth enable approle
v write auth/approle/role/ci-role token_policies=default

cat <<'HCL' | docker exec -i \
  --env 'VAULT_ADDR=https://127.0.0.1:8200' \
  --env 'VAULT_CACERT=/tmp/vault-ca.pem' \
  --env "VAULT_TOKEN=$root_token" "$container" vault policy write vcli-ci-ro -
path "kv1/*" { capabilities = ["read", "list"] }
path "kv2/data/*" { capabilities = ["read"] }
path "kv2/metadata/*" { capabilities = ["list"] }
path "team/config/data/*" { capabilities = ["read"] }
path "team/config/metadata/*" { capabilities = ["list"] }
HCL

# This token has no write/delete/create privileges.
export VCLI_TEST_READONLY_TOKEN="$(v token create -policy=vcli-ci-ro -orphan -field=token)"
python3 -m unittest -v tests.integration.test_vault tests.integration.test_operations tests.integration.test_kv2_advanced tests.integration.test_security tests.integration.test_fault_injection tests.integration.test_partial_failures tests.integration.test_cas_concurrency tests.integration.test_auth_lifecycle tests.integration.test_tls tests.integration.test_repl
