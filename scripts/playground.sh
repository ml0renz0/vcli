#!/usr/bin/env bash
# Disposable HashiCorp Vault playground for the real, self-contained Bash VCLI.
# Design adapted from the user's Secretwright playground: owned Docker state,
# a repeatable seed, private credentials, and a real-CLI acceptance tour.
set -Eeuo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
STATE="$ROOT/.playground"
COMPOSE_FILE="$ROOT/playground/compose.yaml"
VAULT_TAG="${VCLI_PLAYGROUND_VAULT_TAG:-2.1.1}"
OWNER=''
PROJECT=''
CONTAINER=''
ADDRESS=''

die() { printf 'playground: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
Usage: scripts/playground.sh up|status|shell|batch|smoke|reset|down [admin|readonly]

  up              Create/seed an ephemeral native-TLS Vault; repeatable
  status          Report loopback address without revealing credentials
  shell [role]    Explore with the REAL ./vcli interactive shell
  batch [role]    Pipe commands to REAL ./vcli -n
  smoke           Verify live TLS, KV1/KV2, PKI, keytab, ACL and Vault API
  reset           Destroy/recreate all example data and credentials
  down            Remove owned container, synthetic secrets and local state

  source .playground/env.sh    Run ./vcli directly against your playground
  scripts/playground.sh shell readonly    Read-only exploration

Requires local Docker + Compose v2, Bash, curl, jq, openssl, klist and GNU tools.
All data and keys are synthetic; NEVER copy secrets from production.
USAGE
}

require_tools() {
    local command endpoint
    for command in docker curl jq openssl klist base64 mktemp stat cmp grep tr; do
        command -v "$command" >/dev/null || die "missing dependency: $command"
    done
    docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required'
    if [[ -n ${DOCKER_CONTEXT:-} ]]; then
        endpoint=$(docker context inspect "$DOCKER_CONTEXT" \
            --format '{{.Endpoints.docker.Host}}') ||
            die 'cannot inspect Docker context'
    elif [[ -n ${DOCKER_HOST:-} ]]; then
        endpoint="$DOCKER_HOST"
    else
        endpoint=$(docker context inspect \
            --format '{{.Endpoints.docker.Host}}') ||
            die 'cannot inspect active Docker context'
    fi
    [[ $endpoint == unix://* ]] ||
        die 'playground requires a LOCAL Docker Unix socket'
    docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 ||
        die 'local Docker daemon is unavailable'
}

check_state() {
    [[ -d $STATE && ! -L $STATE ]] ||
        die 'missing or unsafe .playground state; use up'
    local mode uid
    mode=$(stat -c '%a' -- "$STATE") || return 1
    uid=$(stat -c '%u' -- "$STATE") || return 1
    [[ $mode == 700 && $uid == "$EUID" ]] ||
        die '.playground must be owned by this user and mode 0700'
}

read_private() {
    local path="$STATE/$1" attrs
    [[ -f $path && ! -L $path ]] ||
        die "missing or unsafe playground file: $1"
    attrs=$(stat -c '%u:%a:%h' -- "$path") || return 1
    [[ $attrs == "$EUID:600:1" ]] ||
        die "playground file must be owned 0600 with one link: $1"
    REPLY=$(< "$path")
    [[ -n $REPLY ]] || die "empty private file: $1"
}

write_private() {
    local path="$STATE/$1"
    [[ ! -e $path && ! -L $path ]] ||
        die "refusing to overwrite private file: $1"
    (set -C; printf '%s\n' "$2" > "$path") ||
        die "failed to create private file: $1"
    chmod 600 -- "$path"
}

load_owner() {
    check_state
    read_private owner
    OWNER="$REPLY"
    [[ $OWNER =~ ^[0-9a-f]{32}$ ]] || die 'invalid ownership marker'
    PROJECT="vcli-pg-$OWNER"
}

compose() {
    local token='uninitialized-playground'
    if [[ -f $STATE/root-token ]]; then
        read_private root-token
        token="$REPLY"
    fi
    VCLI_PG_ROOT_TOKEN="$token" VCLI_PG_OWNER="$OWNER" \
    VCLI_PG_VAULT_TAG="$VAULT_TAG" \
        docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" "$@"
}

owned_container() {
    local candidate label project_label
    candidate=$(compose ps --all --quiet vault) ||
        die 'failed to inspect playground container'
    [[ -n $candidate ]] || return 1
    label=$(docker inspect --format \
        '{{index .Config.Labels "io.vcli.playground.owner"}}' "$candidate") ||
        die 'failed to inspect playground owner'
    project_label=$(docker inspect --format \
        '{{index .Config.Labels "com.docker.compose.project"}}' "$candidate") ||
        die 'failed to inspect Compose project'
    [[ $label == "$OWNER" && $project_label == "$PROJECT" ]] ||
        die 'container ownership mismatch; refusing to operate'
    CONTAINER="$candidate"
}

running() {
    owned_container || return 1
    [[ $(docker inspect --format '{{.State.Running}}' "$CONTAINER") == true ]]
}

address_from_docker() {
    local binding port
    binding=$(compose port vault 8200) || return 1
    [[ $binding =~ ^127\.0\.0\.1:([0-9]{4,5})$ ]] ||
        die 'Vault must bind a loopback port exclusively'
    port="${BASH_REMATCH[1]}"
    ((port >= 1024 && port <= 65535)) || die 'invalid local Vault port'
    ADDRESS="https://127.0.0.1:$port"
}

vault_cli() {
    read_private root-token
    local token="$REPLY"
    docker exec -i --env "VAULT_TOKEN=$token" \
        --env 'VAULT_ADDR=https://127.0.0.1:8200' \
        --env 'VAULT_CACERT=/tmp/vault-ca.pem' \
        "$CONTAINER" vault "$@"
}

api() {
    local method="$1" path="$2" token
    shift 2
    read_private root-token
    token="$REPLY"
    local -a opts=(--fail --silent --show-error --connect-timeout 3
                   --max-time 10 --noproxy '*' --cacert "$STATE/ca.pem"
                   -H "X-Vault-Token: $token" -X "$method")
    if (($#)); then
        opts+=(-H 'Content-Type: application/json' --data "$1")
    fi
    curl "${opts[@]}" "$ADDRESS/v1/$path"
}

health() {
    curl --fail --silent --connect-timeout 2 --max-time 4 --noproxy '*' \
        --cacert "$STATE/ca.pem" "$ADDRESS/v1/sys/health" >/dev/null
}

wait_ready() {
    local attempt tmp
    for ((attempt=0;attempt<60;attempt++)); do
        tmp=$(mktemp "$STATE/.ca-XXXXXXXX") || return 1
        if docker cp "$CONTAINER:/tmp/vault-ca.pem" "$tmp" >/dev/null 2>&1 &&
           [[ -s $tmp ]]; then
            chmod 600 -- "$tmp"
            mv -f -- "$tmp" "$STATE/ca.pem"
            if health; then return 0; fi
        else
            rm -f -- "$tmp"
        fi
        sleep 1
    done
    die 'Vault TLS did not become healthy'
}

seed_mount() {
    local mount="$1" version="$2"
    if ! vault_cli secrets list -format=json |
        jq -e --arg k "$mount/" 'has($k)' >/dev/null; then
        vault_cli secrets enable -path="$mount" -version="$version" kv >/dev/null
    fi
}

write_environment() {
    local tmp
    tmp=$(mktemp "$STATE/.env-XXXXXXXX") || return 1
    {
        printf '%s\n' '# Generated by disposable VCLI playground; source in Bash.'
        printf '%s\n' 'unset VAULT_ADDR VAULT_TOKEN VAULT_CACERT VAULT_SKIP_VERIFY'
        printf '%s\n' 'unset VAULT_HOST VAULT_PORT VAULT_CAPATH VAULT_NAMESPACE'
        printf '%s\n' 'unset VAULT_CLIENT_CERT VAULT_CLIENT_KEY VAULT_AGENT_ADDR'
        printf 'export VAULT_ADDR=%q\n' "$ADDRESS"
        printf 'export VAULT_CACERT=%q\n' "$STATE/ca.pem"
        printf 'export VAULT_KV_VERSION=2 VAULT_KV_MOUNTS=%q\n' 'kv2,team/config'
        printf 'export VAULT_TOKEN="$(cat %q)"\n' "$STATE/root-token"
        printf 'export PATH=%q:"$PATH"\n' "$ROOT"
        printf '%s\n' 'export NO_PROXY=127.0.0.1,localhost'
        printf '%s\n' 'export no_proxy=127.0.0.1,localhost'
    } > "$tmp"
    chmod 600 -- "$tmp"
    mv -f -- "$tmp" "$STATE/env.sh"
}

# The catalog is maintained separately to keep lifecycle and demo data distinct.
# The seed script is part of this checked-out repository, never downloaded.
# shellcheck source=scripts/playground-seed.sh
source "$ROOT/scripts/playground-seed.sh"

up() (
    require_tools
    [[ $VAULT_TAG =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
        die 'use a fixed numeric Vault version tag'

    if [[ -e $STATE || -L $STATE ]]; then
        load_owner
        read_private ready
        [[ $REPLY == yes ]] || die 'incomplete playground state; run down'
        running || die 'playground stopped; run down, then up'
        address_from_docker
        health || die 'playground TLS health check failed'
        printf 'Playground already running at %s (existing changes preserved)\n' "$ADDRESS"
        return 0
    fi

    mkdir -m 700 -- "$STATE" || die 'cannot create playground state'
    OWNER=$(openssl rand -hex 16)
    PROJECT="vcli-pg-$OWNER"
    write_private owner "$OWNER"
    write_private root-token "$(openssl rand -hex 24)"
    mkdir -m 700 -- "$STATE/home" "$STATE/work"

    # Never remove an unknown container. Keep ownership state on errors when
    # Docker could still own a resource, so down can safely recover it.
    trap 'rc=$?; if ((rc != 0)); then
              compose down --volumes --remove-orphans >/dev/null 2>&1 || true
              if ! owned_container; then rm -rf -- "$STATE"; fi
          fi' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    compose up --detach --no-build >/dev/null || return 1
    running || die 'Vault container did not start'
    address_from_docker
    wait_ready

    # Never trust the ephemeral CA implicitly or permit VAULT_SKIP_VERIFY.
    if curl --fail --silent --max-time 3 --noproxy '*' \
        "$ADDRESS/v1/sys/health" >/dev/null 2>&1; then
        die 'Vault playground CA unexpectedly trusted without configuration'
    fi

    seed_playground
    write_environment
    write_private ready yes
    printf 'Playground ready with native TLS at %s\n' "$ADDRESS"
    printf 'Explore: scripts/playground.sh shell\n'
    printf 'Read-only: scripts/playground.sh shell readonly\n'
    printf 'Direct Bash: source .playground/env.sh && ./vcli\n'
    printf 'Catalog: playground/README.md\n'
)

current() {
    load_owner
    read_private ready
    [[ $REPLY == yes ]] || die 'playground is incompletely initialized'
    running || die 'Vault playground is stopped; use down and up'
    address_from_docker
    read_private ca.pem
    health || die 'Vault did not pass verified TLS health check'
}

status() {
    require_tools
    if [[ ! -e $STATE && ! -L $STATE ]]; then
        printf 'Playground not running\n'
        return 0
    fi
    current
    printf 'Playground running with verified TLS at %s\n' "$ADDRESS"
}

client_with() {
    local token="$1" ca="$2" work="$3"
    shift 3
    # Explicitly remove inherited production Vault variables, agents and
    # TLS bypass flags. The address is derived only from owned Docker state.
    env -i PATH="$PATH" HOME="$STATE/home" TMPDIR="$work" \
        LANG="${LANG:-C.UTF-8}" TERM="${TERM:-dumb}" \
        NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
        VAULT_ADDR="$ADDRESS" VAULT_TOKEN="$token" VAULT_CACERT="$ca" \
        VAULT_KV_VERSION=2 VAULT_KV_MOUNTS='kv2,team/config' \
        "$ROOT/vcli" "$@"
}

client() {
    local role="$1"
    shift
    require_tools
    current
    case "$role" in
        admin) read_private root-token ;;
        readonly) read_private readonly-token ;;
        *) die 'role must be admin or readonly' ;;
    esac
    client_with "$REPLY" "$STATE/ca.pem" "$STATE/work" "$@"
}

smoke() (
    require_tools
    current
    local tmp leaf output root_token readonly_token
    tmp=$(mktemp -d "$STATE/.smoke-XXXXXXXX") || return 1
    leaf="kv1/smoke-${RANDOM}-$$"
    trap 'api DELETE "$leaf" >/dev/null 2>&1 || true; rm -rf -- "$tmp"' EXIT

    read_private root-token
    root_token="$REPLY"
    read_private readonly-token
    readonly_token="$REPLY"

    printf 'put %s {"marker":"vcli-real"}\n' "$leaf" |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'CLI KV1 write failed'
    api GET "$leaf" | jq -e '.data.marker=="vcli-real"' >/dev/null ||
        die 'Vault API did not confirm CLI write'

    printf 'cat %s\n' "$leaf" |
        client_with "$readonly_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'read-only CLI read failed'
    jq -e '.data.marker=="vcli-real"' "$tmp/output" >/dev/null ||
        die 'CLI read result disagreed with Vault'

    if printf 'put %s {"marker":"forbidden"}\n' "$leaf" |
        client_with "$readonly_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'readonly client unexpectedly wrote'
    fi
    api GET "$leaf" | jq -e '.data.marker=="vcli-real"' >/dev/null ||
        die 'readonly attempt mutated secret'

    if printf 'cat %s\n' "$leaf" |
        client_with 'bad-playground-token' "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'invalid token unexpectedly succeeded'
    fi
    if printf 'cat %s\n' "$leaf" |
        client_with "$root_token" "$tmp/unknown-ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'unknown CA unexpectedly trusted'
    fi

    # Verify the other seeded types, including an actual two-version KV v2
    # history and an independent nested mount, via both CLI and Vault API.
    api GET kv2/metadata/demo/payments/database |
        jq -e '.data.current_version == 2' >/dev/null ||
        die 'KV v2 did not retain both synthetic revisions'
    printf 'cat kv2/demo/payments/database\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI KV v2 read failed'
    jq -e '.data.password=="FAKE-VERSION-2"' "$tmp/output" >/dev/null ||
        die 'VCLI did not return the latest KV v2 revision'
    api GET team/config/data/demo/payments |
        jq -e '.data.data.team=="demo" and .data.data.feature_flags.tls==true' \
        >/dev/null || die 'nested KV v2 mount was not seeded'
    printf 'cat team/config/demo/payments\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI nested KV v2 read failed'
    jq -e '.data.team=="demo"' "$tmp/output" >/dev/null ||
        die 'VCLI failed to route the nested KV v2 mount'
    printf 'cat kv1/demo/nested/config\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI nested JSON read failed'
    jq -e '.data.enabled==true and .data.limits.retries==3 and
           .data.roles==["reader","writer"]' "$tmp/output" >/dev/null ||
        die 'VCLI changed nested JSON types'

    # Verify the generated X.509 private key and raw MIT keytab are exported
    # exactly, with private permissions, not merely "success" printed by VCLI.
    printf 'getcert kv1/demo/pki/web\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI certificate export failed'
    cmp -s "$tmp/web.crt" "$STATE/fixtures/web.crt" ||
        die 'exported X.509 certificate differs from synthetic fixture'
    cmp -s "$tmp/web.key" "$STATE/fixtures/web.key" ||
        die 'exported private key differs from synthetic fixture'
    [[ $(stat -c '%a' "$tmp/web.key") == 600 ]] ||
        die 'private key export has unsafe mode'

    printf 'getkrb kv1/demo/kerberos/http\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI keytab export failed'
    cmp -s "$tmp/HTTP_service.example.test@EXAMPLE.TEST.keytab" \
        "$STATE/fixtures/http.keytab" ||
        die 'exported keytab differs from fixture'
    [[ $(stat -c '%a' "$tmp/HTTP_service.example.test@EXAMPLE.TEST.keytab") == 600 ]] ||
        die 'keytab export has unsafe mode'

    printf 'getsan kv1/demo/pki/web\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI SAN extraction failed'
    grep -Fq 'alt.playground.example.test' "$tmp/output" ||
        die 'certificate SAN was not returned'
    printf 'getprinc kv1/demo/kerberos/http\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'VCLI principal extraction failed'
    grep -Fq 'HTTP/service.example.test@EXAMPLE.TEST' "$tmp/output" ||
        die 'synthetic Kerberos principal was not returned'
    printf 'getallcertlist kv1/demo/pki\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'recursive certificate listing failed'
    grep -Fq 'expiring.playground.example.test' "$tmp/output" ||
        die 'nested expiring certificate missing from listing'
    printf 'getallkrblist kv1/demo/kerberos\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'recursive keytab listing failed'
    grep -Fq 'HTTP/service.example.test@EXAMPLE.TEST' "$tmp/output" ||
        die 'nested keytab missing from listing'
    printf 'getcertexpiration kv1/demo/pki/nested/service\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1 || die 'X.509 expiration inspection failed'
    grep -Fq 'days' "$tmp/output" ||
        die 'expiring certificate was not reported'

    # Authenticate with an actual AppRole issued in the throwaway Vault.
    local role_id secret_id
    read_private role-id
    role_id="$REPLY"
    read_private secret-id
    secret_id="$REPLY"
    env -i PATH="$PATH" HOME="$tmp" TMPDIR="$tmp" TERM=dumb \
        NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
        VAULT_ADDR="$ADDRESS" VAULT_CACERT="$STATE/ca.pem" \
        VAULT_ROLE_ID="$role_id" VAULT_SECRET_ID="$secret_id" \
        VAULT_KV_VERSION=2 VAULT_KV_MOUNTS='kv2,team/config' \
        "$ROOT/vcli" -n <<< 'cat kv1/demo/app' > "$tmp/output" 2>&1 ||
        die 'VCLI AppRole authentication failed'
    jq -e '.data.user=="demo"' "$tmp/output" >/dev/null ||
        die 'AppRole did not grant the expected read permission'

    # Negative fixtures must NOT export or overwrite any files.
    if printf 'getcert kv1/negative/pki/key-mismatch\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'mismatched X.509 key unexpectedly exported'
    fi
    if printf 'getkrb kv1/negative/kerberos/bad-base64\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'invalid keytab unexpectedly exported'
    fi
    if printf 'getcert kv1/negative/pki/malformed\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'malformed certificate was accepted'
    fi
    if printf 'getcert kv1/negative/pki/missing-key\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'missing certificate key was accepted'
    fi
    if printf 'getkrb kv1/negative/kerberos/unsafe-principal\n' |
        client_with "$root_token" "$STATE/ca.pem" "$tmp" -n \
        > "$tmp/output" 2>&1; then
        die 'pathlike Kerberos principal was accepted'
    fi
    if grep -Fq "$root_token" "$tmp/output"; then
        die 'root token leaked in CLI error output'
    fi
    printf 'Playground smoke PASS: TLS, KV, ACL, X.509, keytab and API verification\n'
)

down() {
    require_tools
    if [[ ! -e $STATE && ! -L $STATE ]]; then
        printf 'Playground already removed\n'
        return 0
    fi
    load_owner
    if owned_container; then
        compose down --volumes --remove-orphans >/dev/null ||
            die 'Docker cleanup failed; private state preserved'
        if owned_container; then
            die 'Docker still owns a container; private state preserved'
        fi
    else
        compose down --volumes --remove-orphans >/dev/null ||
            die 'Docker cleanup failed; private state preserved'
    fi
    rm -rf -- "$STATE"
    printf 'Playground and local synthetic credentials removed\n'
}

main() {
    local action="${1:-help}" role
    if (($#)); then shift; fi
    case "$action" in
        help|-h|--help) usage ;;
        up) (($# == 0)) || die 'up takes no arguments'; up ;;
        status) (($# == 0)) || die 'status takes no arguments'; status ;;
        shell|batch)
            (($# <= 1)) || die "$action accepts at most one role"
            role="${1:-admin}"
            if [[ $action == shell ]]; then
                client "$role"
            else
                client "$role" -n
            fi
            ;;
        smoke) (($# == 0)) || die 'smoke takes no arguments'; smoke ;;
        reset) (($# == 0)) || die 'reset takes no arguments'; down; up ;;
        down) (($# == 0)) || die 'down takes no arguments'; down ;;
        *) usage >&2; die "unknown action: $action" ;;
    esac
}
main "$@"
