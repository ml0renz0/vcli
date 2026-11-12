#!/usr/bin/env bash
# Disposable TLS Raft cluster for benchmark-only runs (1 or 3 voters).
set -Eeuo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
case "${VCLI_BENCH_STORAGE:-}" in
  raft-1) nodes=1 ;; raft-3) nodes=3 ;;
  *) echo 'Expected raft-1 or raft-3' >&2; exit 2 ;;
esac
[[ ${VCLI_BENCH_ONLY:-0} == 1 && ${VCLI_BENCH_RECURSIVE:-0} == 1 ]] || exit 2
: "${VCLI_TEST_VAULT_TAG:?Pin a Vault version}"
[[ $VCLI_TEST_VAULT_TAG =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]] || exit 2
for tool in docker openssl jq curl; do command -v "$tool" >/dev/null || exit 2; done
[[ -z ${DOCKER_HOST:-} || $DOCKER_HOST == unix://* ]] || exit 2
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
  endpoint=$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')
else
  endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
fi
[[ $endpoint == unix://* ]] || { echo 'Local Docker required' >&2; exit 2; }
tmp=$(mktemp -d "${TMPDIR:-/tmp}/vcli-raft-bench.XXXXXXXX")
network="vcli-raft-$(openssl rand -hex 8)"
containers=()
cleanup(){
  local code=$?
  trap - EXIT
  for node in "${containers[@]}"; do docker rm -f "$node" >/dev/null 2>&1 || true; done
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf -- "$tmp"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -m 700 "$tmp/config"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -sha256   -subj '/CN=vcli-raft-test-ca' -addext 'basicConstraints=critical,CA:TRUE'   -keyout "$tmp/ca.key" -out "$tmp/config/ca.pem" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes -sha256 -subj '/CN=vault-1'   -keyout "$tmp/config/server.key" -out "$tmp/server.csr" >/dev/null 2>&1
cat > "$tmp/server.ext" <<'SAN'
subjectAltName = DNS:vault-1,DNS:vault-2,DNS:vault-3,IP:127.0.0.1
extendedKeyUsage = serverAuth
keyUsage = digitalSignature, keyEncipherment
SAN
openssl x509 -req -sha256 -days 1 -in "$tmp/server.csr"   -CA "$tmp/config/ca.pem" -CAkey "$tmp/ca.key" -CAcreateserial   -extfile "$tmp/server.ext" -out "$tmp/config/server.pem" >/dev/null 2>&1
for ((i=1; i<=nodes; i++)); do
  mkdir -m 700 "$tmp/data-$i"
  cat > "$tmp/config/node-$i.hcl" <<HCL
disable_mlock = true
api_addr = "https://vault-$i:8200"
cluster_addr = "https://vault-$i:8201"
storage "raft" {
  path = "/vault/data"
  node_id = "vault-$i"
}
listener "tcp" {
  address = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_cert_file = "/vault/config/server.pem"
  tls_key_file = "/vault/config/server.key"
}
HCL
done
docker network create "$network" >/dev/null
launch(){
  local i=$1 name="$network-vault-$1"
  local -a expose=()
  # Each node needs its own private loopback endpoint for API unseal.
  expose=(--publish '127.0.0.1::8200')
  docker run --detach --name "$name"     --user "$(id -u):$(id -g)" --network "$network"     --network-alias "vault-$i" "${expose[@]}"     --mount "type=bind,source=$tmp/config,target=/vault/config,readonly"     --mount "type=bind,source=$tmp/data-$i,target=/vault/data"     --entrypoint vault "hashicorp/vault:$VCLI_TEST_VAULT_TAG"     server -config="/vault/config/node-$i.hcl" >/dev/null
  containers+=("$name")
}
v(){
  local i=$1; shift
  docker exec --env 'VAULT_ADDR=https://127.0.0.1:8200'     --env 'VAULT_CACERT=/vault/config/ca.pem'     --env "VAULT_TOKEN=${VAULT_TOKEN:-}"     "$network-vault-$i" vault "$@"
}
wait_node(){
  local i=$1 status
  for ((retry=0; retry<60; retry++)); do
    if [[ $(docker inspect --format '{{.State.Running}}' "$network-vault-$i") != true ]]; then
      echo "Raft container $i exited before readiness" >&2
      break
    fi
    status=$(v "$i" status -format=json 2>/dev/null) || true
    if jq -e '(.initialized | type)=="boolean"' <<< "$status" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "Raft node $i failed startup (status and container logs):" >&2
  docker inspect --format '{{.State.Status}}: exit={{.State.ExitCode}}' \
    "$network-vault-$i" >&2 || true
  docker logs "$network-vault-$i" --tail 35 >&2 || true
  return 1
}
unseal(){
  local i=$1 binding
  binding=$(docker port "$network-vault-$i" 8200/tcp)
  [[ $binding =~ ^127[.]0[.]0[.]1:[0-9]+$ ]] || {
    echo 'Unseal endpoint is not loopback-only' >&2
    return 1
  }
  # Read the Shamir key from the private JSON file; never put it in argv.
  jq -nc --slurpfile initialized "$tmp/init.json" \
    '{key: $initialized[0].unseal_keys_b64[0]}' |
    curl --silent --show-error --fail --cacert "$tmp/config/ca.pem" \
      --header 'Content-Type: application/json' --data-binary @- \
      "https://$binding/v1/sys/unseal" |
    jq -e '.sealed == false' >/dev/null
}
launch 1
wait_node 1
v 1 operator init -key-shares=1 -key-threshold=1 -format=json > "$tmp/init.json"
unseal 1
export VAULT_TOKEN
VAULT_TOKEN=$(jq -er '.root_token' "$tmp/init.json")
export VAULT_CACERT="$tmp/config/ca.pem"
port=$(docker port "$network-vault-1" 8200/tcp)
[[ $port =~ ^127[.]0[.]0[.]1:[0-9]+$ ]] || exit 1
export VAULT_ADDR="https://$port"
unset VAULT_SKIP_VERIFY
if curl -fsS --max-time 2 "$VAULT_ADDR/v1/sys/health" >/dev/null 2>&1; then
  echo 'Untrusted CA unexpectedly accepted' >&2; exit 1
fi
for ((i=2; i<=nodes; i++)); do
  launch "$i"
  wait_node "$i"
  # The Raft join API expects PEM *contents*, not the certificate path.
  # --rawfile puts the CA in the HTTPS body, never in argv or shell logs.
  binding=$(docker port "$network-vault-$i" 8200/tcp)
  [[ $binding =~ ^127[.]0[.]0[.]1:[0-9]+$ ]] || exit 1
  joined=no
  for attempt in 1 2 3; do
    if jq -nc --rawfile ca "$tmp/config/ca.pem" \
      '{leader_api_addr:"https://vault-1:8200", leader_ca_cert:$ca}' |
      curl --silent --show-error --fail --cacert "$tmp/config/ca.pem" \
        --header 'Content-Type: application/json' --data-binary @- \
        "https://$binding/v1/sys/storage/raft/join" >/dev/null; then
      joined=yes
      break
    fi
    sleep 2
  done
  if [[ $joined != yes ]]; then
    echo "Raft follower $i could not join the leader" >&2
    docker logs "$network-vault-1" --tail 45 2>&1 |
      sed "s/${VAULT_TOKEN}/[REDACTED]/g" >&2 || true
    docker logs "$network-vault-$i" --tail 45 2>&1 |
      sed "s/${VAULT_TOKEN}/[REDACTED]/g" >&2 || true
    exit 1
  fi
  unseal "$i"
done
ready=no
for ((retry=0; retry<90; retry++)); do
  peers=$(v 1 operator raft list-peers -format=json 2>/dev/null) || true
  if jq -e --argjson n "$nodes"     '(.data.config.servers|length)==$n and
     all(.data.config.servers[]; .voter==true) and
     ([.data.config.servers[]|select(.leader==true)]|length)==1'     <<< "$peers" >/dev/null 2>&1; then ready=yes; break; fi
  sleep 1
done
[[ $ready == yes ]] || { echo 'Raft quorum not stable' >&2; exit 1; }
curl -fsS --cacert "$VAULT_CACERT" "$VAULT_ADDR/v1/sys/health" >/dev/null
export VCLI_TEST_READONLY_TOKEN
VCLI_TEST_READONLY_TOKEN=$(v 1 token create -policy=default -orphan -field=token)
# Use the same known KV1 mount as the normal integration matrix.
v 1 secrets enable -path=kv1 -version=1 kv >/dev/null
echo "Raft fixture ready: $nodes voters (TLS, disk)" >&2
python3 -m tests.integration.bench_recursive
