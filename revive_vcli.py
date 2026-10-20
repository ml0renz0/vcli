#!/usr/bin/env python3
"""Patch the original ml0renz0/vcli (0.6.2) into a conservative revival build.

Usage: python3 revive_vcli.py /path/to/vcli
Makes /path/to/vcli.orig and updates /path/to/vcli. Requires only stdlib.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys


def must_replace(s: str, old: str, new: str, *, count: int = 1) -> str:
    seen = s.count(old)
    if seen != count:
        raise RuntimeError(f"Expected {count} occurrence(s), found {seen}: {old[:90]!r}")
    return s.replace(old, new)


def replace_function(s: str, name: str, source: str) -> str:
    pattern = rf"(?ms)^function {re.escape(name)}\(\)\{{\n.*?^\}}(?=\n)"
    result, occurrences = re.subn(pattern, lambda _: source.rstrip(), s)
    if occurrences != 1:
        raise RuntimeError(f"Expected exactly one function {name}, found {occurrences}")
    return result


def revive(s: str) -> str:
    if 'declare VERSION="0.6.2"' not in s:
        raise RuntimeError("Expected original vcli 0.6.2; no changes applied")
    s = must_replace(s, 'declare VERSION="0.6.2"', 'declare VERSION="0.6.3-revival"')
    s = must_replace(s, 'declare DEPENDENCIES="curl jq openssl klist bc"', 'declare DEPENDENCIES="curl jq mktemp timeout"')
    # No command was executed: batch exit must succeed without HTTP mutations.
    s = must_replace(s,
        'if [[ ! $http_code =~ ^2 ]]; then exit_status=1 ; fi',
        'if [[ -n $http_code && ! $http_code =~ ^2 ]]; then exit_status=1 ; fi')
    # Vault AppRole nowadays exposes token_policies; old policies is a fallback.
    s = must_replace(s, 'jq ".data .policies[]" -r < "$tmp"',
                     "jq -r '(.data.token_policies // .data.policies // [])[]' < \"$tmp\"")
    s = must_replace(s, 'declare DEBUGFILE="/tmp/vcli.debug"',
                     'declare DEBUGFILE="${VCLI_DEBUGFILE:-$HOME/.vcli.debug}"')
    s = must_replace(s, '    : > "$VCLISTORY"',
                     '    : > "$VCLISTORY"\n    chmod 600 "$VCLISTORY" 2>/dev/null || true')
    s = must_replace(s, 'declare TMP="/tmp"', 'declare TMP="${TMPDIR:-/tmp}"\n# Keep temporary files and generated exports private.\numask 077')
    old_colors = '''TAB=$(tput setaf 4; tput bold)
RED="$(tput setaf 1; tput bold)"
GREEN="$(tput setaf 2; tput bold)"
YELLOW="$(tput setaf 3)"
CYAN="$(tput setaf 6; tput bold)"
NONE="$(tput sgr0)"
BOLD="$(tput bold)"'''
    new_colors = '''# Non-interactive shells and minimal containers may lack TERM/tput.
TAB="" RED="" GREEN="" YELLOW="" CYAN="" NONE="" BOLD=""
if [[ -t 1 && -n ${TERM:-} && $TERM != dumb ]] && command -v tput >/dev/null 2>&1; then
    TAB=$(tput setaf 4; tput bold)
    RED=$(tput setaf 1; tput bold)
    GREEN=$(tput setaf 2; tput bold)
    YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6; tput bold)
    NONE=$(tput sgr0)
    BOLD=$(tput bold)
fi'''
    s = must_replace(s, old_colors, new_colors)
    # Preserve the old CLI's colorized history and key handling.
    s = s.replace('mktemp -p /dev/shm', 'mktemp -p "$TMP"').replace('mktemp -d -p /dev/shm', 'mktemp -d -p "$TMP"')

    s = replace_function(s, "new_version", '''function new_version(){
    # The original upstream release check is obsolete and adds network traffic.
    # Keep this function as a compatibility stub; a modern release process can
    # restore update checks later.
    return 1
}''')

    s = replace_function(s, "_run", '''# _run used eval on interpolated HTTP commands; replaced by safe curl arrays.
function _run(){
    echo "Internal error: legacy _run is no longer supported" >&2
    return 1
}''')

    # Explicit v2 mode because mount probing can require permissions some tokens
    # do not have. Supports comma-separated KV mount points, including nested ones.
    s = must_replace(s, '# simplified curl function', '''# Map legacy paths to KV v2 HTTP paths when opted in.
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
    path="${raw%%\\?*}"
    local -a segments
    IFS='/' read -r -a segments <<< "$path"
    for seg in "${segments[@]}"; do
        encoded=$(urlencode "$seg") || return 1
        if [[ -n $result ]]; then result+='/'; fi
        result+="$encoded"
    done
    # Preserve trailing / for root listing and folders.
    [[ $path == */ && -n $result ]] && result+='/';
    if [[ $raw == *\\?* ]]; then
        query="${raw#*\\?}"
        result+="?$query"
    fi
    printf '%s' "$result"
}

# simplified curl function''')

    s = replace_function(s, "_curl", r'''function _curl(){
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
}''')

    # No destructive test secret on startup, and avoid printing tokens in errors.
    probe = '''    # try to get vault health status
    _put secret/-_test_- '{ "key": "value" }'
    if ! _cat secret/-_test_- > /dev/null 2>&1 ; then
        echo -e "Cannot access vault's secrets!\\nCheck vault_token: '$vault_token'"
        exit 1
    else
        _rm secret/-_test_-
    fi
'''
    s = must_replace(s, probe, '''    # Never write or delete a secret merely to start vcli.
    # Vault permissions are checked when individual commands are executed.
''')

    # Replace envsubst hard dependency with a best-effort optional facility.
    s = must_replace(s, 'echo "$2" | envsubst > "$data"', '''if command -v envsubst >/dev/null 2>&1; then
        printf '%s\\n' "$2" | envsubst > "$data"
    else
        printf '%s\\n' "$2" > "$data"
    fi''')
    # Don't persist JSON payloads in history by default.
    s = must_replace(s, '        is_cmd "$cmd" || return 1', '''        is_cmd "$cmd" || return 1
        # Commands may contain passwords/certificates. Do not persist their data.
        if [[ ${VCLI_HISTORY_SECRETS:-false} != true &&
              $cmd =~ ^(put|addsecret|replace|delsecret|find)[[:space:]] ]]; then
            return 0
        fi''')

    # Use supported KV secrets engine terminology for new mounts.
    s = must_replace(s,
        r'echo "{ \"type\": \"generic\", \"description\": \"$url secret backend\" }" > "$data"',
        'jq -nc --arg d "$url secret backend" --arg v "${VAULT_KV_VERSION:-1}" \'{type:"kv",description:$d,options:{version:$v}}\' > "$data"' )

    # Modern Vault policy endpoints and JSON response formats.
    s = replace_function(s, "_lspol", '''function _lspol(){
    local tmp
    tmp=$(mktemp -p "$TMP" .vcli.policies.XXXXXX) || return 1
    if ! _curl /sys/policies/acl "" LIST > "$tmp"; then
        cat "$tmp" >&2; rm -f "$tmp"; return 1
    fi
    jq -r '(.data.keys // .keys // [])[]' "$tmp"
    rm -f "$tmp"
}''')
    s = replace_function(s, "_showpol", '''function _showpol(){
    local policy="$1" tmp
    if [[ -z $policy ]]; then
        echo "Error! Missing policy" >&2; return 1
    fi
    tmp=$(mktemp -p "$TMP" .vcli.policy.XXXXXX) || return 1
    if ! _curl "/sys/policies/acl/$policy" > "$tmp"; then
        cat "$tmp" >&2; rm -f "$tmp"; return 1
    fi
    jq -r '.data.policy // .policy // empty' "$tmp"
    rm -f "$tmp"
}''')

    # Normalize the VAULT_ADDR parser and CLI/environment precedence.
    start = '''    # Use VAULT_ADDR as the primary default as it is the most common setting.
    # If VAULT_ADDR is set it will overwrite VAULT_HOST and VAULT_PORT
    if [[ $VAULT_ADDR ]] && [[ $VAULT_ADDR =~ ^http[s]*://[a-zA-Z0-9.-]+:[0-9]+ ]]; then
        protocol=${VAULT_ADDR%%:*}
        local host_port=${VAULT_ADDR#*//}
        VAULT_HOST=${host_port%:*}
        VAULT_PORT=${host_port#*:}
    fi

    # Use environment variables VAULT_HOST VAULT_TOKEN and VAULT_PORT if defined
    vault_port=${VAULT_PORT:-"$vault_port"}
    vault_port=${vault_port:-"8200"} # Define Vault default port if not specified
    vault_host=${VAULT_HOST:-$vault_host}
    vault_token=${VAULT_TOKEN:-$vault_token}'''
    # Source uses ${VAULT_ADDR#*//}, verify text from connector before editing.
    if start not in s:
        start = start.replace('${VAULT_ADDR#*//}', '${VAULT_ADDR#*//}')
    # If the exact block is not found, locate by anchored comments.
    block_re = r'(?ms)^    # Use VAULT_ADDR as the primary default as it is the most common setting\.\n.*?^    vault_token=\$\{VAULT_TOKEN:-\$vault_token\}'
    addr_repl = '''    # VAULT_ADDR overrides VAULT_HOST / VAULT_PORT when configured.
    if [[ -n ${VAULT_ADDR:-} ]]; then
        local address="${VAULT_ADDR%/}"
        if [[ $address =~ ^(https?)://(\\[[^]]+\\]|[^:/]+)(:([0-9]+))?$ ]]; then
            protocol="${BASH_REMATCH[1]}"
            vault_host="${BASH_REMATCH[2]}"
            vault_port="${BASH_REMATCH[4]:-8200}"
        else
            echo "Invalid VAULT_ADDR: $VAULT_ADDR" >&2
            exit 1
        fi
    fi
    vault_port="${vault_port:-${VAULT_PORT:-8200}}"
    vault_host="${vault_host:-${VAULT_HOST:-}}"
    vault_token="${vault_token:-${VAULT_TOKEN:-}}"'''
    s, c = re.subn(block_re, lambda _: addr_repl, s)
    if c != 1:
        raise RuntimeError(f"VAULT_ADDR block: expected 1 occurrence, got {c}")
    return s


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = pathlib.Path(sys.argv[1])
    source = path.read_text(encoding='utf-8')
    updated = revive(source)
    temp = path.with_name(path.name + '.revival.tmp')
    temp.write_text(updated, encoding='utf-8')
    check = subprocess.run(['bash', '-n', str(temp)], capture_output=True, text=True)
    if check.returncode:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f'bash -n failed: {check.stderr}')
    backup = path.with_name(path.name + '.orig')
    if backup.exists():
        raise RuntimeError(f'Refusing to overwrite backup: {backup}')
    shutil.copy2(path, backup)
    temp.chmod(path.stat().st_mode)
    temp.replace(path)
    print(f'Updated {path}; original saved as {backup}')
    print('Verified Bash syntax with bash -n. Live Vault behavior NOT verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
