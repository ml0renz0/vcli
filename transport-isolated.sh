#!/usr/bin/env bash
# Map legacy paths to KV v2 HTTP paths when opted in.
# VAULT_KV_VERSION=2 VAULT_KV_MOUNTS=secret,teams/config
function vcli_kv_path(){
    local path="${1#/}" method="$2" mount rest
    VCLI_KV2_ACTIVE=no
    if [[ ${VAULT_KV_VERSION:-1} != 2 ]]; then
        printf '%s' "$path"
        return
    fi
    local mounts="${VAULT_KV_MOUNTS:-secret}"
    local -a mount_array
    IFS=',' read -r -a mount_array <<< "$mounts"
    for mount in "${mount_array[@]}"; do
        mount="${mount#/}"; mount="${mount%/}"
        if [[ -z $mount || $path != "$mount/"* ]]; then continue; fi
        rest="${path#"$mount/"}"
        case "$rest" in
            data/*|metadata/*) printf '%s' "$path"; return;;
        esac
        case "$method" in
            LIST) printf '%s/metadata/%s' "$mount" "$rest" ;;
            GET|POST|PUT|PATCH|DELETE) printf '%s/data/%s' "$mount" "$rest" ;;
            *) printf '%s' "$path"; return;;
        esac
        # This function can be used in command substitutions; the caller must
        # separately decide whether this is a KV v2 request.
        return
    done
    printf '%s' "$path"
}

# Encode path components but do not encode / or the query delimiter ?.
function vcli_encode_endpoint(){
    local raw="${1#/}" path query seg result="" encoded
    path="${raw%%\?*}"
    local -a segments
    IFS='/' read -r -a segments <<< "$path"
    for seg in "${segments[@]}"; do
        encoded=$(urlencode "$seg") || return 1
        if [[ -n $result ]]; then result+='/'; fi
        result+="$encoded"
    done
    # Preserve trailing / for root listing and folders.
    [[ $path == */ && -n $result ]] && result+='/';
    if [[ $raw == *\?* ]]; then
        query="${raw#*\?}"
        result+="?$query"
    fi
    printf '%s' "$result"
}


function _curl(){
    local ignore_http_code=no disable_fail=no
    while :; do
        case "${1:-}" in
            -i) ignore_http_code=yes; shift;;
            -_disable_-f_-) disable_fail=yes; shift;;
            '') shift; break;;
            *) break;;
        esac
    done
    local path="${1#/}" data="${2:-}" method="${3:-GET}"
    local kv2=no mount rest
    if [[ ${VAULT_KV_VERSION:-1} == 2 ]]; then
        local mounts="${VAULT_KV_MOUNTS:-secret}"
        local -a mount_array
        IFS=',' read -r -a mount_array <<< "$mounts"
        for mount in "${mount_array[@]}"; do
            mount="${mount#/}"; mount="${mount%/}"
            [[ -z $mount || $path != "$mount/"* ]] && continue
            rest="${path#"$mount/"}"
            [[ $rest == data/* || $rest == metadata/* ]] && break
            case "$method" in GET|POST|PUT|PATCH|DELETE|LIST)
                kv2=yes; path=$(vcli_kv_path "$path" "$method");;
            esac
            break
        done
    fi

    local encoded url response_file payload_file="" payload="" status curl_status
    encoded=$(vcli_encode_endpoint "$path") || return 1
    url="$protocol://$vault_host:$vault_port/v1/$encoded"
    response_file=$(mktemp -p "$TMP" .vcli.response.XXXXXX) || return 1
    local -a args=(--silent --show-error --connect-timeout "${VCLI_CONNECT_TIMEOUT:-2}"
                  --max-time "${VCLI_HTTP_TIMEOUT:-30}" --request "$method"
                  --output "$response_file" --write-out '%{http_code}'
                  --header @- --header 'Content-Type: application/json')
    case "${VAULT_SKIP_VERIFY:-false}" in 1|true|TRUE|yes|YES) args+=(--insecure);; esac
    [[ -n ${VAULT_CACERT:-} ]] && args+=(--cacert "$VAULT_CACERT")
    [[ -n ${VAULT_CAPATH:-} ]] && args+=(--capath "$VAULT_CAPATH")
    [[ -n ${VAULT_NAMESPACE:-} ]] && args+=(--header "X-Vault-Namespace: $VAULT_NAMESPACE")

    if [[ -n $data && $data != '-_list_root_-' ]]; then
        if [[ ! -f $data ]]; then
            echo "vcli: request payload file missing: $data" >&2
            rm -f "$response_file"
            return 1
        fi
        payload="$data"
        if [[ $kv2 == yes && ( $method == POST || $method == PUT || $method == PATCH ) ]]; then
            payload_file=$(mktemp -p "$TMP" .vcli.payload.XXXXXX) || return 1
            if ! jq -c '{data: .}' "$data" > "$payload_file"; then
                rm -f "$response_file" "$payload_file"
                return 1
            fi
            payload="$payload_file"
        fi
        args+=(--data-binary "@$payload")
    fi
    debug "request: $method $url"
    # Supply the Vault token over stdin, not as a process-list argument.
    # --header @- is supported by modern curl versions.
    status=$(printf 'X-Vault-Token: %s\n' "$vault_token" | curl "${args[@]}" "$url")
    curl_status=$?
    [[ -n $payload_file ]] && rm -f "$payload_file"
    [[ -n $data && $data != '-_list_root_-' && -f $data ]] && rm -f "$data"
    if (( curl_status != 0 )); then
        http_code="000"
        [[ $ignore_http_code == no ]] && cat "$response_file" >&2
        rm -f "$response_file"
        return 1
    fi
    if [[ $ignore_http_code == no ]]; then
        http_code="$status"
        if [[ $data == '-_list_root_-' ]]; then
            jq -r '.data | keys[]' "$response_file" 2>/dev/null || true
        elif [[ -s $response_file ]]; then
            if [[ $kv2 == yes && $method == GET && $status == 2* ]]; then
                jq 'if (.data.data | type) == "object" then .data = .data.data else . end' "$response_file"
            else
                jq '.' "$response_file" 2>/dev/null || cat "$response_file"
            fi
        fi
    fi
    rm -f "$response_file"
    [[ $status == 2* || $disable_fail == yes ]]
}
