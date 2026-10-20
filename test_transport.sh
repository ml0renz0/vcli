#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/transport-isolated.sh"
urlencode(){ jq -nr --arg v "$1" '$v|@uri'; }
debug(){ :; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
protocol=https
vault_host=localhost
vault_port=8200
vault_token='this is my test token!'
VAULT_KV_MOUNTS=secret,team/stuff
VAULT_KV_VERSION=2
function curl(){
  local output='' method='' data='' url='' val prev=''
  local -a args=("$@")
  for ((i=0;i<${#args[@]};i++)); do
    val=${args[i]}
    case "$prev" in
       --output) output=$val;;
       --request) method=$val;;
       --data-binary) data=$val;;
       *) :;;
    esac
    prev=$val
  done
  url=${args[${#args[@]}-1]}
  local header
  IFS= read -r header
  [[ $header == "X-Vault-Token: $vault_token" ]] || { echo "token incorrect" >&2; return 1; }
  if [[ -n $data ]]; then
     [[ $data == @* && -f ${data#@} ]] || { echo "payload invalid" >&2; return 1; }
     jq -e '.data.foo == "bar"' "${data#@}" >/dev/null || { echo "bad v2 payload" >&2; return 1; }
  fi
  if [[ $method == 'GET' ]]; then
     [[ $url == 'https://localhost:8200/v1/secret/data/foo' ]] || { echo "Bad URL: $url" >&2; return 1; }
     echo '{"data":{"data":{"foo":"bar"},"metadata":{"version":2}}}' > "$output"
  elif [[ $method == 'LIST' ]]; then
     [[ $url == 'https://localhost:8200/v1/secret/metadata/folder' ]] || { echo "Bad URL: $url" >&2; return 1; }
     echo '{"data":{"keys":["one","two"]}}' > "$output"
  elif [[ $method == 'POST' ]]; then
     [[ $url == 'https://localhost:8200/v1/secret/data/foo' ]] || { echo "Bad URL: $url" >&2; return 1; }
     echo '{"data":{"version":2}}' > "$output"
  fi
  printf '200'
}
result=$(_curl 'secret/foo' '' GET)
printf '%s\n' "$result" | jq -e '.data.foo == "bar"' >/dev/null
[[ $(vcli_encode_endpoint '/auth/approle/role?list=true') == 'auth/approle/role?list=true' ]]
result=$(_curl 'secret/folder' '' LIST)
printf '%s\n' "$result" | jq -e '.data.keys | length == 2' >/dev/null
printf '{"foo":"bar"}\n' > "$TMP/test.json"
result=$(_curl 'secret/foo' "$TMP/test.json" POST)
printf '%s\n' "$result" | jq -e '.data.version == 2' >/dev/null
[[ ! -e $TMP/test.json ]] # original temp should be cleaned
printf 'transport smoke: OK\n'
