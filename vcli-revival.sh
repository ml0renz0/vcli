#!/usr/bin/env bash

# vcli internal variables
declare SCRIPTNAME
SCRIPTNAME="$(basename "$0")"
declare VERSION="0.6.3-revival"
declare VERBOSE=${VERBOSE:-"no"}
declare DEBUG=${DEBUG:-"no"}
declare DEBUG_HEXDUMP="no"
declare DEBUGFILE="${VCLI_DEBUGFILE:-$HOME/.vcli.debug}"
declare TMP="${TMPDIR:-/tmp}"
# Private temporary data and exported secret files.
umask 077
declare is_command cmd cmd_history restcmd _key len pos history_index
declare timeout="2" # timeout to verify connection to Vault
declare http_code
declare vault_host vault_port vault_token current_pwd

# History related vars
declare VCLISTORY="$HOME/.vclistory"
declare pri_suffix="_principal"
declare crt_suffix="_crt"
declare key_suffix="_key"
declare SEARCHIST ENDHIST ADDTMPCMD search_cmd
declare -a cmd_history tmp_history
declare cert_warning_threshold="30"
declare cert_critical_threshold="15"
# vcli dependencies
declare DEPENDENCIES="curl jq mktemp timeout"

# COLOURS
# Non-interactive shells and minimal containers may lack TERM/tput.
TAB="" RED="" GREEN="" YELLOW="" CYAN="" NONE="" BOLD=""
if [[ -t 1 && -n ${TERM:-} && $TERM != dumb ]] && command -v tput >/dev/null 2>&1; then
    TAB=$(tput setaf 4; tput bold)
    RED=$(tput setaf 1; tput bold)
    GREEN=$(tput setaf 2; tput bold)
    YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6; tput bold)
    NONE=$(tput sgr0)
    BOLD=$(tput bold)
fi

# URI encoder
function urlencode {
    jq -nr --arg v "$1" '$v|@uri'
}

# Check dependencies
function check_dependency(){
    local dependencies
    dependencies=$*
    for dependency in $dependencies; do
        if ! command -v "$dependency" >/dev/null; then
            echo "$SCRIPTNAME: command '$dependency' not found"
            exit 1
        fi
    done
}

function check_connectivity(){
    if ! timeout $timeout bash -c "</dev/tcp/$vault_host/$vault_port"; then
        echo "Cannot establish connection to $protocol://$vault_host:$vault_port"
        exit 1
    fi
}

# check vcli latest version
function new_version(){
    # The original upstream release check is obsolete and adds network traffic.
    # Keep this function as a compatibility stub; a modern release process can
    # restore update checks later.
    return 1
}

# Print prompt
function prompt(){
    if [[ $disable_prompt ]]; then
        return
    fi
    local COLOUR=$CYAN
    debug "prompt: http_code=$http_code"
    if [[ $http_code =~ ^2 ]]; then
        COLOUR=$GREEN
    elif [[ $http_code =~ ^3 ]]; then
        COLOUR=$YELLOW
    elif [[ $http_code =~ ^4 ]] || [[ $http_code =~ ^5 ]]; then
        COLOUR=$RED
    fi
    echo -n "[$COLOUR$http_code$NONE] $BOLD$SCRIPTNAME$TAB@$NONE$BOLD$vault_host$TAB:$NONE$BOLD$vault_port$current_pwd$TAB >$NONE "
}

function debug(){
    local msg="$*"
    if [[ $DEBUG == "yes" ]]; then
        echo -e "$msg" >> $DEBUGFILE
    fi
}

################################################# CLI FUNCTIONS #################################################

declare -A vcli_commands=(
                      [mount]=arg
                      [umount]=arg
                      [cd]=arg
                      [ls]=arg
                      [lsrole]=noarg
                      [showrole]=arg
                      [renew-token]=arg
                      [lspol]=noarg
                      [showpol]=arg
                      [tree]=arg
                      [find]=arg
                      [replace]=arg
                      [cptree]=arg
                      [rmtree]=arg
                      [backup]=arg
                      [restore]=arg
                      [cat]=arg
                      [cp]=arg
                      [getcert]=arg
                      [getcertlist]=arg
                      [getkrblist]=arg
                      [getcertexpiration]=arg
                      [getallcertlist]=arg
                      [getallkrblist]=arg
                      [getallcertexpiration]=arg
                      [getsan]=arg
                      [getprinc]=arg
                      [certinfo]=arg
                      [getkrb]=arg
                      [krbinfo]=arg
                      [mv]=arg
                      [put]=arg
                      [addsecret]=arg
                      [delsecret]=arg
                      [rm]=arg
                      [help]=arg
                      [history]=noarg
                      [exit]=noarg
                      [verbose]=noarg
                      [enable-debug]=noarg
                      [disable-debug]=noarg
                      [enable-hexdump-debug]=noarg
                      [disable-hexdump-debug]=noarg
                    )

declare -A vcli_commands_help=(
        [mount]="mount path/to/mount \n  creates mount $current_pwd/path/to/mount"
        [umount]="umount path/to/mount \n  destroys mount $current_pwd/path/to/mount"
        [cd]="cd path/to/secret \n  changes to directory $current_pwd/path/to/secret"
        [ls]="ls path/to/secret \n  lists paths and secrets on $current_pwd/path/to/secret"
        [lsrole]="lsrole \n  lists Vault roles"
        [showrole]="showrole myrole \n  shows Vault role named myrole"
        [renew-token]="renew-token [increment] \n  renews current Vault token (e.g. 45s, 5m)"
        [lspol]="lspol \n  lists Vault policies"
        [showpol]="showpol mypolicy \n  shows Vault policy named mypolicy"
        [tree]="tree path/to/secret \n  prints vault's secrets tree with $current_pwd/path/to/secret as its root"
        [find]="find path/to/search { \"key\": \"value\" } \n  finds vault's secrets that match the JSON data provided on path $current_pwd/path/to/search"
        [replace]="replace path/to/replace { \"key\": \"value\" } { \"newkey\": \"newvalue\" } \n  replaces vault's secrets under $current_pwd/path/to/replace that match the JSON data with the new JSON data provided"
        [cptree]="cptree path/to/secret path/to/other_secret \n  copies tree to destination path $current_pwd/path/to/other_secret"
        [rmtree]="rmtree [-f] path/to/secret \n  removes path $current_pwd/path/to/secret recursively. Use '-f' as first argument to avoid delete confirmation"
        [backup]="backup path/to/secrets \n  backs up Vault /path to $TMP folder"
        [restore]="restore /local/dir/file \n  restores Vault backup"
        [cat]="cat path/to/secret \n  shows contents of secret $current_pwd/path/to/secret"
        [cp]="cp path/to/secret path/to/other_secret \n  copies contents of path to path $current_pwd/path/to/other_secret"
        [getcert]="getcert path/to/secret \n  downloads certificate CRT and KEY stored in $current_pwd/path/to/secret"
        [getcertlist]="getcertlist path/to/certificates \n  recovers certs.list with certificates found in $current_pwd/path/to/certificates"
        [getkrblist]="getkrblist path/to/kerberos \n  recovers krb.list with principals found in $current_pwd/path/to/kerberos"
        [getcertexpiration]="getcertexpiration [-oo|-ow|-oc] path/to/certificate \n  shows expiration date for specified certificate $current_pwd/path/to/certificates\n  -oo: omit ok certs\n  -ow: omit certs in warning threshold ($cert_warning_threshold days)\n  -oc: omit certs in critical threshold ($cert_critical_threshold days)"
        [getallcertlist]="getallcertlist path/to/certificates \n  recovers certs.list with all certificates found under tree $current_pwd/path/to/certificates"
        [getallkrblist]="getallkrblist path/to/kerberos \n  recovers krb.list with all principals found under tree $current_pwd/path/to/kerberos"
        [getallcertexpiration]="getallcertexpiration [-oo|-ow|-oc] path/to/certificate \n  shows expiration date for all certificates found under tree $current_pwd/path/to/certificates\n  -oo: omit ok certs\n  -ow: omit certs in warning threshold ($cert_warning_threshold days)\n  -oc: omit certs in critical threshold ($cert_critical_threshold days)"
        [getsan]="getsan path/to/secret \n  shows certificate's SAN stored in $current_pwd/path/to/secret"
        [getprinc]="getprinc path/to/secret \n  shows pincipal stored in keytab $current_pwd/path/to/secret"
        [certinfo]="certinfo path/to/secret \n  shows certificate info stored in $current_pwd/path/to/secret"
        [getkrb]="getkrb path/to/secret \n  exports keytab file to $TMP/principal.keytab"
        [krbinfo]="krbinfo path/to/secret \n  shows principal & keytab info stored in $current_pwd/path/to/secret"
        [mv]="mv path/to/secret path/to/other_secret \n  moves contents of path to path $current_pwd/path/to/other_secret"
        [put]="put path/to/secret { \"key\": \"value\" } \n  adds json={ \"key\": \"value\" } to secret $current_pwd/path/to/secret"
        [addsecret]="addsecret path/to/secret { \"key2\": \"value2\" } \n  adds secret { \"key2\": \"value2\" } to existing secret $current_pwd/path/to/secret"
        [delsecret]="delsecret path/to/secret key \n  deletes key from secret"
        [rm]="rm path/to/secret \n  removes secret $current_pwd/path/to/secret"
        [help]="help [vcli_command] \n  shows this help or the specified's command"
        [history]="history \n  shows vcli history"
        [verbose]="verbose \n  toggle verbosity"
        [enable-debug]="enable-debug \n  enable debugging"
        [disable-debug]="disable-debug \n  disable debugging"
        [enable-hexdump-debug]="enable-hexdump-debug \n  enable input hexdump debugging"
        [disable-hexdump-debug]="disable-hexdump-debug \n  disable input hexdump debugging"
        [exit]="exit \n  exits vcli"
                              )

# return whether is a command or not
function is_cmd(){
    local cmd=${1%% *}
    local args=${1#* }
    if [[ $cmd == "$args" ]]; then unset args; fi
    local args
    #debug "IS_CMD1: cmd=$cmd args=$args"
    for command in "${!vcli_commands[@]}"; do
        #debug "IS_CMD2: command=$command, args=$args"
        #debug "IS_CMD3: $cmd =~ ^$command$"
        if [[ $cmd =~ ^$command$ ]]; then
            if [[ ${vcli_commands[$command]} = "noarg" ]]; then
                if [[ $args ]]; then
                    return 1
                else
                    #debug "IS_CMD4: MATCHED"
                    return 0
                fi
            fi
            #debug "IS_CMD4: MATCHED"
            return 0
        fi
    done
    return 1
}

# complete command
function completion(){
    debug "run completion"
    local -a tmparray
    local found _cmd _path _secret paths
    local -a commands=( ${!vcli_commands[@]} )
    IFS=' ' read -r _cmd _option _path <<< "${@}"
    if [[ ! $_path ]]; then
        _path=$_option
        unset _option
    fi
    debug "cmd: $_cmd, option: $_option, path: $_path"
    # complete command
    if [[ ! $_path ]]; then
        if ! is_cmd "$_cmd" || [[ ! $_cmd =~ \ $ ]]; then
            for c in "${commands[@]}"; do
                if [[ $c =~ ^$_cmd ]]; then
                    debug "found: $c"
                    tmparray+=( $c )
                fi
            done
            found=${#tmparray[@]}
            debug "commands_found $found: " "${tmparray[@]}"
            local tmpcmd_so_far cmd_so_far
            if [[ $found == 1 ]]; then
                cmd="${tmparray[0]} "
                debug "new_cmd cmd1: $cmd"
            elif [[ $found -gt 1 ]]; then
                echo
                for p in "${tmparray[@]}"; do
                    echo "$p"
                done
                local n=1
                local i=0
                # compare all found occurrences and build the common substring
                while true ; do
                    tmpcmd_so_far=${tmparray[$i]::$n}
                    debug "tmpcmd_so_far: $tmpcmd_so_far"
                    while [[ $i -lt ${#tmparray[@]} ]]; do
                        debug "cmdmatch: i=$i n=$n ${tmparray[$i]::$n} == $tmpcmd_so_far"
                        if [[ ${tmparray[$i]::$n} != "$tmpcmd_so_far" ]]; then
                            end="true"
                            break 2
                        fi
                        tmpcmd_so_far=${tmparray[$i]::$n}
                        let i++
                    done
                    if [[ ! $end ]]; then
                        cmd_so_far=$tmpcmd_so_far
                        debug "cmdsofar: $cmd_so_far"
                    fi
                    let n++
                    let i=0
                done
                cmd="$cmd_so_far"
                debug "new_cmd cmd+1: $cmd"
                unset tmparray tmpcmd_so_far cmd_so_far end
                prompt
                return 1
            fi
        fi
    # complete path
    else
        debug "searching path $_path"
        _secret=${_path##*/}
        debug "1 path: $_path, secret: $_secret"
        if [[ $_path != "$_secret" ]]; then
            _path=${_path%$_secret*}
        else
            unset _path
        fi
        debug "2 path: $_path, secret: $_secret"
        paths=$(_ls "-_disable_colors_-" "$_path" | tr -t '\n' ' ' | tr -s ' ')
        debug "DEBUG: $(_ls "-_disable_colors_-" "$_path" | tr -t '\n' ' ' | tr -s ' ')"

        backIFS=$IFS
        IFS=$' '
        for p in $paths; do
            if [[ $p =~ ^$_secret ]]; then
                debug "found path: $p"
                tmparray+=( $p )
            fi
        done
        IFS=$backIFS
        found=${#tmparray[@]}
        debug "paths_found $found: " "${tmparray[@]}"
        debug "cmd: $_cmd $_path"
        local tmpath_so_far path_so_far
        if [[ $found == 1 ]]; then
            cmd="$_cmd $_option $_path${tmparray[0]}"
            debug "new_cmd path1: $cmd"
        elif [[ $found -gt 1 ]]; then
            echo
            for p in "${tmparray[@]}"; do
                echo "$p" | sed -e "s/\//$CYAN\/$NONE/"
            done
            local n=1
            local i=0
            # compare all found occurrences and build the common substring
            while true ; do
                tmpath_so_far=${tmparray[$i]::$n}
                debug "tmpath_so_far: $tmpath_so_far"
                while [[ $i -lt ${#tmparray[@]} ]]; do
                    debug "pathmatch: i=$i n=$n ${tmparray[$i]::$n} == $tmpath_so_far"
                    if [[ ${tmparray[$i]::$n} != "$tmpath_so_far" ]]; then
                        end="true"
                        break 2
                    fi
                    tmpath_so_far=${tmparray[$i]::$n}
                    let i++
                done
                if [[ ! $end ]]; then
                    path_so_far=$tmpath_so_far
                    debug "pathsofar: $path_so_far"
                fi
                let n++
                let i=0
            done
            cmd="$_cmd $_option $_path$path_so_far"
            debug "new_cmd path+1: $cmd"
            unset tmparray tmpath_so_far path_so_far end
            prompt
            return 1
        fi
    fi
    unset tmparray found
}

# add $cmd to command history @cmd_history
function add_cmd(){
    local vclistory
    # If "$1" is a file then process it
    if [[ -r "$1" ]]; then
        mapfile -t vclistory < <(sed '1!G;h;$!d' "$1") # read history in reverse
        cmd_history=( "${vclistory[@]}" )
    else
        local cmd
        cmd="$(echo "$*" | sed -e "s/^[[:space:]]\+\(.*\)/\1/g" -e "s/[[:space:]]\+$//g" | tr -s ' ')"
        is_cmd "$cmd" || return 1
        # Commands may contain passwords/certificates. Do not persist their data.
        if [[ ${VCLI_HISTORY_SECRETS:-false} != true &&
              $cmd =~ ^(put|addsecret|replace|delsecret|find)[[:space:]] ]]; then
            return 0
        fi
        local -a new_history
        new_history=( "$cmd" )
        for old_cmd in "${cmd_history[@]}"; do
            if [[ $old_cmd != $"cmd" ]]; then
                new_history+=( "$old_cmd" )
            fi
        done
        cmd_history=( "${new_history[@]}" )
        debug "add_cmd cmd_history[0]: $cmd"
    fi
}

# UP: returns previous cmd in history
function get_prev_cmd(){
    if [[ ! $ADDTMPCMD ]]; then
        debug "get_prev add tmp_cmd: $cmd"
        add_cmd "$cmd"
        ADDTMPCMD="yes"
    fi
    if [[ $history_index -lt $((${#cmd_history[@]}-1)) ]]; then
        cmd="${cmd_history[$((++history_index))]}"
        debug "get_prev cmd_history[$((history_index))]: $cmd"

        # move the cursor to the beginning to print the command selected
        [[ $pos != 1 ]] && tput cub $((pos-1)) # move cursor left to the beginning

        # print the command found in history
        echo -n "$cmd"
        tput el # clean to the end of the line
        let pos=$((${#cmd}+1))
        let len=${#cmd}
    else
        debug "get_next reached oldest cmd: $cmd"
    fi
}

# DOWN: returns next cmd in history
function get_next_cmd(){
    if [[ $history_index -gt 0 ]]; then
        cmd="${cmd_history[$((--history_index))]}"
        debug "get_next cmd_history[$((history_index))]: $cmd"

        # move the cursor to the beginning to print the command selected
        [[ $pos != 1 ]] && tput cub $((pos-1)) # move cursor left to the beginning

        # print the command found in history
        echo -n "$cmd"
        tput el # clean to the end of the line
        let pos=$((${#cmd}+1))
        let len=${#cmd}
    elif [[ ! $ENDHIST ]]; then
        debug "get_next reached newest cmd: $cmd"
        unset ADDTMPCMD
        let history_index=-1
        cmd=""
        # move the cursor to the beginning to print the command selected
        [[ $pos != 1 ]] && tput cub $((pos-1)) # move cursor left to the beginning

        tput el # clean to the end of the line
        let pos=$((${#cmd}+1))
        let len=${#cmd}
        ENDHIST=yes
    fi
}

# returns last command's path
function get_lastcmd_arg(){
    if [[ "$(echo "${cmd_history[$((history_index+1))]}" | cut -d' ' -f2)" =~ ^- ]]; then
        echo "${cmd_history[$((history_index+1))]}" | cut -d' ' -f3
    else
        echo "${cmd_history[$((history_index+1))]}" | cut -d' ' -f2
    fi
}

# saves history to $HOME/.vclistory
function save_history(){
    : > "$VCLISTORY"
    chmod 600 "$VCLISTORY" 2>/dev/null || true
    for (( idx=${#cmd_history[@]}-1 ; idx>=0 ; idx-- )) ; do
        echo "${cmd_history[$idx]}" >> "$VCLISTORY"
    done
}

# loads history from $HOME/.vclistory
function load_history(){
    [[ ! -e $VCLISTORY ]] && return
    add_cmd "$VCLISTORY"
}

# loads history from $HOME/.vclistory
function print_history(){
    if [[ ${#cmd_history[@]} == 0 ]]; then
        echo "No history found"
        return
    fi
    local n=1
    local depth
    local historylines=${#cmd_history[@]}
    depth="$(( ${#historylines} - 1 ))"
    for (( idx=${#cmd_history[@]}-1 ; idx>=0 ; idx-- )) ; do
        npadded="$(printf %0"${depth}"d $n)"
        echo "#$npadded: ${cmd_history[$idx]}"
        let n++
    done
}

# return vcli commands found in history matching $cmd
function rotate_found_cmds(){
    debug "rotate_found_cmds"
    if [[ ! $search_cmd ]]; then
        search_cmd="$*"
    fi
    debug "search_next_from: $search_cmd, \$SEARCHIST: $SEARCHIST"
    if [[ ! $SEARCHIST ]]; then
        # starting vcli search mode. Build a tmp_history with all matching commands
        for (( idx=0 ; idx<=${#cmd_history[@]}-1 ; idx++ )) ; do
            if [[ "${cmd_history[$idx]}" =~ $search_cmd ]]; then
                tmp_history+=( "${cmd_history[$idx]}" )
            fi
            [[ ! $SEARCHIST ]] && SEARCHIST="0"
        done
        if (( ${#tmp_history[@]} == 0 )); then
            unset SEARCHIST search_cmd tmp_history
            return 1
        fi
        # print first command found
        # if another Ctrl+r is hit next item will be printed
        debug "\${#tmp_history}=${#tmp_history[@]}"
        debug "\${tmp_history[$SEARCHIST]}=\"${tmp_history[$SEARCHIST]}"\"
        cmd="${tmp_history[$SEARCHIST]}"
    else
        let SEARCHIST++
        if (( ${#tmp_history[@]} == 0 )); then
            unset SEARCHIST search_cmd tmp_history
            return 1
        fi
        SEARCHIST=$((SEARCHIST%(${#tmp_history[@]})))
        debug "\${#tmp_history}=${#tmp_history[@]}"
        debug "\${tmp_history[$SEARCHIST]}=\"${tmp_history[$SEARCHIST]}"\"
        cmd="${tmp_history[$SEARCHIST]}"
    fi
}
################################################# CLI FUNCTIONS #################################################


# Usage
function help(){
    cat <<EOH
Usage: $SCRIPTNAME -v <vault_host> -p <vault_port> -t <vault_token> [-i] [-n]
            -i: use insecure http
            -n: do not print prompt or commands. Useful when input is piped from stdin
       $SCRIPTNAME --version
EOH
}

# Print commands help
function commands_help(){
    local arg="$1"
    local _cmd
    local commands
    commands="${!vcli_commands[@]}"
    commands=${commands// /|}
    if [[ ! $arg ]]; then
        echo -e "Available commands:\n"
        backIFS=$IFS
        IFS=$'|'
        local _commands
        _commands=$(echo "$commands" | tr -t '|' '\n' | sort | tr -t '\n' '|')
        for command in $_commands; do
            echo -e "$command: $(commands_help "$command")\n"
        done
        IFS=$backIFS
        return
    fi
    for _cmd in "${!vcli_commands[@]}"; do
        if [[ "$_cmd" = "$arg" ]]; then
            echo -e "${vcli_commands_help[$_cmd]}"
            return 0
        fi
    done
    echo -e "unkown '$arg' vcli command. Available commands:"
    while read -r line ; do        echo "   $line"
    done< <(echo -e "${commands//|/\\n}") | sort
    return 1
}

# Check for given arguments
function check_arg(){
    local arg=$1
    local msg=$2
    if [[ ! $arg ]]; then
        echo -e "ERROR! No $msg provided\n"
        help ; exit 1
    fi
}

# save & clean & exit
function clean_n_exit(){
    save_history
    [[ ! $disable_prompt ]] && echo
    ## REMOVED: it seems this bug doesn't reproduce anymore ##
    #
    # reset terminal when running inside a container as local echo gets disabled (??)
    # if ! head -n 1 /proc/1/sched | grep -qE "(systemd|init)"; then
    #   reset
    # fi
    if [[ -n $http_code && ! $http_code =~ ^2 ]]; then exit_status=1 ; fi
    exit $exit_status
}

# Run command and prettyfy stdout
# _run used eval on interpolated HTTP commands; replaced by safe curl arrays.
function _run(){
    echo "Internal error: legacy _run is no longer supported" >&2
    return 1
}

# Map legacy paths to KV v2 HTTP paths when opted in.
# VAULT_KV_VERSION=2 VAULT_KV_MOUNTS=secret,teams/config
function vcli_is_kv2_path(){
    [[ ${VAULT_KV_VERSION:-1} == 2 ]] || return 1
    local path="/${1#/}" mount
    local -a mounts
    IFS=',' read -r -a mounts <<< "${VAULT_KV_MOUNTS:-secret}"
    for mount in "${mounts[@]}"; do
        mount="${mount#/}"; mount="${mount%/}"
        [[ -n $mount && $path == "/$mount/"* ]] && return 0
    done
    return 1
}
function vcli_kv2_metadata_path(){
    local path="/${1#/}" mount rest
    local -a mounts
    IFS=',' read -r -a mounts <<< "${VAULT_KV_MOUNTS:-secret}"
    for mount in "${mounts[@]}"; do
        mount="${mount#/}"; mount="${mount%/}"
        if [[ -n $mount && $path == "/$mount/"* ]]; then
            rest="${path#"/$mount/"}"
            printf '/%s/metadata/%s\n' "$mount" "$rest"
            return 0
        fi
    done
    return 1
}
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
            data/*|metadata/*|delete/*) printf '%s' "$path"; return;;
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

# Report partial application without pretending multi-request Vault changes are atomic.
function _vcli_partial_failure(){
    local operation="$1" completed="$2" total="$3" current="$4"
    if (( completed > 0 )); then
        printf 'vcli: PARTIAL %s completed=%d total=%d failed=%s; no rollback performed\n' \
            "$operation" "$completed" "$total" "$current" >&2
    else
        printf 'vcli: FAILED %s completed=0 total=%d failed=%s\n' \
            "$operation" "$total" "$current" >&2
    fi
    return 1
}

# Common read-only recursion for KV mounts; failures propagate to callers.
function _vcli_absolute_path(){
    local path="$1"
    [[ -n $path ]] || return 1
    [[ $path == /* ]] || path="${current_pwd%/}/$path"
    path="/${path#/}"
    while [[ $path == *'//'* ]]; do path="${path//\/\//\/}"; done
    path="${path%/}"
    [[ $path != *'/../'* && $path != *'/./'* && $path != */.. && $path != */. ]] || return 1
    printf '%s\n' "$path"
}
function _vcli_children(){
    local response
    response=$(_curl "${1%/}/" "" LIST) || return 1
    jq -e '(.data.keys | type) == "array"' <<< "$response" >/dev/null || return 1
    jq -r '.data.keys[]' <<< "$response"
}
function _vcli_leaves(){
    local root="$1" entries child
    entries=$(_vcli_children "$root") || return 1
    while IFS= read -r child; do
        [[ -n $child ]] || continue
        if [[ $child == */ ]]; then
            _vcli_leaves "${root%/}/${child%/}" || return 1
        else
            printf '%s\n' "${root%/}/$child"
        fi
    done <<< "$entries"
}

# AppRole login uses the same TLS verification as ordinary Vault calls.
# Never print role_id, secret_id or the resulting client token.
function _vcli_login_approle(){
    local role_id="${VAULT_ROLE_ID:-}" secret_id="${VAULT_SECRET_ID:-}" response_file payload_file code status
    [[ -n $role_id && -n $secret_id ]] || {
        echo "vcli: incomplete AppRole credentials" >&2; return 1;
    }
    response_file=$(mktemp -p "$TMP" .vcli.auth.XXXXXX) || return 1
    payload_file=$(mktemp -p "$TMP" .vcli.auth-data.XXXXXX) || {
        rm -f "$response_file"; return 1;
    }
    jq -cn --arg rid "$role_id" --arg sid "$secret_id"         '{role_id:$rid,secret_id:$sid}' > "$payload_file" || {
        rm -f "$response_file" "$payload_file"; return 1;
    }
    local -a args=(--silent --show-error --connect-timeout "${VCLI_CONNECT_TIMEOUT:-2}"
        --max-time "${VCLI_HTTP_TIMEOUT:-30}" --request POST
        --header "Content-Type: application/json" --data-binary "@$payload_file"
        --output "$response_file" --write-out '%{http_code}')
    case "${VAULT_SKIP_VERIFY:-false}" in 1|true|TRUE|yes|YES) args+=(--insecure);; esac
    [[ -n ${VAULT_CACERT:-} ]] && args+=(--cacert "$VAULT_CACERT")
    [[ -n ${VAULT_CAPATH:-} ]] && args+=(--capath "$VAULT_CAPATH")
    # Mutual TLS is opt-in; a half-configured pair must never silently
    # fall back to an unauthenticated client certificate handshake.
    if [[ -n ${VAULT_CLIENT_CERT:-} || -n ${VAULT_CLIENT_KEY:-} ]]; then
        if [[ -z ${VAULT_CLIENT_CERT:-} || -z ${VAULT_CLIENT_KEY:-} ]]; then
            echo "vcli: both VAULT_CLIENT_CERT and VAULT_CLIENT_KEY are required" >&2
            rm -f "$response_file"
            return 1
        fi
        args+=(--cert "$VAULT_CLIENT_CERT" --key "$VAULT_CLIENT_KEY")
    fi
    [[ -n ${VAULT_NAMESPACE:-} ]] && args+=(--header "X-Vault-Namespace: $VAULT_NAMESPACE")
    status=$(curl "${args[@]}" "$protocol://$vault_host:$vault_port/v1/auth/approle/login")
    code=$?
    rm -f "$payload_file"
    if (( code != 0 )) || [[ $status != 2* ]]; then
        rm -f "$response_file"
        echo "vcli: AppRole login failed" >&2
        return 1
    fi
    vault_token=$(jq -er '.auth.client_token | select(type=="string" and length>0)' "$response_file") || {
        rm -f "$response_file"
        echo "vcli: invalid AppRole response" >&2
        return 1
    }
    rm -f "$response_file"
}
function _renew_token(){
    local increment="${1:-}" payload response
    if [[ -n $increment && ! $increment =~ ^[0-9]+(s|m|h)?$ ]]; then
        echo "vcli: invalid renewal increment" >&2; return 1;
    fi
    payload=$(mktemp -p "$TMP" .vcli.renew.XXXXXX) || return 1
    if [[ -n $increment ]]; then
        jq -cn --arg value "$increment" '{increment:$value}' > "$payload"
    else
        printf '{}\n' > "$payload"
    fi
    response=$(_curl "/auth/token/renew-self" "$payload" POST) || {
        echo "vcli: token renewal failed" >&2; return 1;
    }
    jq -e '.auth.renewable == true' <<< "$response" >/dev/null || {
        echo "vcli: token is not renewable" >&2; return 1;
    }
    echo "Vault token renewed"
}

# simplified curl function
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
    local path="${1#/}" data="${2:-}" method="${3:-GET}" cas="${4:-}"
    local kv2=no mount rest
    if [[ ${VAULT_KV_VERSION:-1} == 2 ]]; then
        local mounts="${VAULT_KV_MOUNTS:-secret}"
        local -a mount_array
        IFS=',' read -r -a mount_array <<< "$mounts"
        for mount in "${mount_array[@]}"; do
            mount="${mount#/}"; mount="${mount%/}"
            [[ -z $mount || $path != "$mount/"* ]] && continue
            rest="${path#"$mount/"}"
            [[ $rest == data/* || $rest == metadata/* || $rest == delete/* ]] && break
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
    # Mutual TLS is opt-in; a half-configured pair must never silently
    # fall back to an unauthenticated client certificate handshake.
    if [[ -n ${VAULT_CLIENT_CERT:-} || -n ${VAULT_CLIENT_KEY:-} ]]; then
        if [[ -z ${VAULT_CLIENT_CERT:-} || -z ${VAULT_CLIENT_KEY:-} ]]; then
            echo "vcli: both VAULT_CLIENT_CERT and VAULT_CLIENT_KEY are required" >&2
            rm -f "$response_file"
            return 1
        fi
        args+=(--cert "$VAULT_CLIENT_CERT" --key "$VAULT_CLIENT_KEY")
    fi
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
            if [[ -n $cas ]]; then
                if ! jq -c --argjson cas "$cas" '{data: ., options: {cas: $cas}}' "$data" > "$payload_file"; then
                    rm -f "$response_file" "$payload_file"
                    return 1
                fi
            elif ! jq -c '{data: .}' "$data" > "$payload_file"; then
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
        # A write may have committed before its HTTP response was lost.
        # Never blindly retry non-idempotent operations on transport errors.
        if [[ $method == POST || $method == PUT || $method == PATCH || $method == DELETE ]]; then
            printf 'vcli: OUTCOME_UNKNOWN method=%s path=/%s; verify Vault state before retrying\n' "$method" "$path" >&2
        fi
        [[ $ignore_http_code == no ]] && cat "$response_file" >&2
        rm -f "$response_file"
        return 1
    fi
    if [[ $ignore_http_code == no ]]; then
        http_code="$status"
        # A successful HTTP status is not sufficient for a read. A truncated,
        # empty or non-object response must fail closed, not fall back to cat.
        if [[ $status == 2* && ( $method == GET || $method == LIST ) ]]; then
            if [[ ! -s $response_file ]] ||
               ! jq -e 'type == "object"' "$response_file" >/dev/null 2>&1; then
                echo "vcli: invalid or empty JSON from Vault" >&2
                rm -f "$response_file"
                return 1
            fi
        fi
        if [[ $data == '-_list_root_-' ]]; then
            jq -r '.data | keys[]' "$response_file" || {
                rm -f "$response_file"; return 1;
            }
        elif [[ -s $response_file ]]; then
            if [[ $kv2 == yes && $method == GET && $status == 2* && $cas != raw ]]; then
                jq 'if (.data.data | type) == "object" then .data = .data.data else . end' "$response_file" || {
                    rm -f "$response_file"; return 1;
                }
            else
                jq '.' "$response_file" || {
                    rm -f "$response_file"; return 1;
                }
            fi
        fi
    fi
    rm -f "$response_file"
    [[ $status == 2* || $disable_fail == yes ]]
}

# run command entered in CLI
function run_cmd(){
    local args
    local fullcmd="$*"
    args=${*#* }
    if [[ $fullcmd == "$args" ]]; then
        unset args
    fi
    backIFS=$IFS
    while IFS=$' ' read -r cmd arg1 arg2 arg3; do
        if [[ $arg1 == "!$" ]]; then
            arg1=$(get_lastcmd_arg)
        fi
        if ! is_cmd "$fullcmd"; then
            commands_help "$cmd"
            return 1
        fi
        local command_status=0
        case $cmd in
            cd)                     _cd "$arg1" ;;
            ls)                     _ls "$arg1" ;;
            lsrole)                 _lsrole ;;
            showrole)               _showrole "$arg1" ;;
            renew-token)            _renew_token "$arg1" ;;
            lspol)                  _lspol ;;
            showpol)                _showpol "$arg1" ;;
            tree)                   backIFS=$IFS; IFS=$' ' ; _tree "$arg1"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            find)                   backIFS=$IFS; IFS=$' ' ; _find "$arg1" "$arg2 $arg3"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            replace)                backIFS=$IFS; IFS=$' ' ; _replace "$arg1" "$arg2 $arg3"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            cptree)                 backIFS=$IFS; IFS=$' ' ; _cptree "$arg1" "$arg2"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            rmtree)                 backIFS=$IFS; IFS=$' ' ; _rmtree "$arg1" "$arg2" || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            backup)                 backIFS=$IFS; IFS=$' ' ; _backup "$arg1"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            restore)                backIFS=$IFS; IFS=$' ' ; _restore "$arg1"  || { IFS=$backIFS; return 1; }; IFS=$backIFS;;
            cat)                    _cat "$arg1" ;;
            put)                    _put "$arg1" "$arg2 $arg3" ;;
            addsecret)              _addsecret "$arg1" "$arg2 $arg3" ;;
            delsecret)              _delsecret "$arg1" "$arg2" ;;
            rm)                     _rm "$arg1" ;;
            cp)                     _cp "$arg1" "$arg2" ;;
            getcert)                _getcert "$arg1" ;;
            getsan)                 _getsan "$arg1" ;;
            getprinc)               _getprinc "$arg1" ;;
            getcertlist)            _getcertlist "$arg1" ;;
            getkrblist)             _getkrblist "$arg1" ;;
            getcertexpiration)      _getcertexpiration "$arg1" "$arg2" "$arg3" ;;
            getallcertlist)         _getallcertlist "$arg1" ;;
            getallkrblist)          _getallkrblist "$arg1" ;;
            getallcertexpiration)   _getallcertexpiration "$arg1" "$arg2" ;;
            certinfo)               _certinfo "$arg1" ;;
            getkrb)                 _getkrb "$arg1" ;;
            krbinfo)                _krbinfo "$arg1" ;;
            mv)                     _mv "$arg1" "$arg2" ;;
            mount)                  _mount "$args" ;;
            umount)                 _umount "$args" ;;
            history)                print_history ;;
            exit)                   save_history; exit ;;
            help)                   commands_help "$arg1" ;;
            enable-debug)           echo "debug enabled" ; DEBUG="yes";;
            disable-debug)          echo "debug disabled" ; DEBUG="no";;
            verbose)                if [[ $VERBOSE == "yes" ]]; then
                                        echo "verbose mode OFF"
                                        VERBOSE="no"
                                    else
                                        echo "verbose mode ON"
                                        VERBOSE="yes"
                                    fi ;;
            enable-hexdump-debug)   echo "hexdump debug enabled" ; DEBUG_HEXDUMP="yes";;
            disable-hexdump-debug)  echo "hexdump debug disabled" ; DEBUG_HEXDUMP="no";;
            \#*)                    return;; # ignore commented lines
            *)                      commands_help "$cmd" ; return 1 ;;
        esac
        command_status=$?
        debug "run_cmd: $cmd $arg1 $arg2"
        debug "run_cmd: http_code=$http_code"
        add_cmd "$cmd $arg1 $arg2 $arg3"
        if (( command_status != 0 )); then return "$command_status"; fi
    done< <(echo "$fullcmd")
}

# mount function
function _mount(){
    if [[ "$1" == "-i" ]]; then
        local ignore_http_code="true"
        shift
    fi
    local url="$*"
    local data
    if [[ ! $url ]]; then
        echo "mounted paths:"
        _ls /
    # mount $url
    else
        # get full path if relative reference
        if [[ ! "$url" =~ ^/ ]]; then
            url="/sys/mounts/$current_pwd$url"
        else
            url="/sys/mounts$url"
        fi
        data=$(mktemp -p "$TMP" -t .put.XXXX)
        jq -nc --arg d "$url secret backend" --arg v "${VAULT_KV_VERSION:-1}" '{type:"kv",description:$d,options:{version:$v}}' > "$data"
        if [[ $ignore_http_code ]]; then
            _curl -i "$url" "$data" "POST"
        else
            _curl "$url" "$data" "POST"
        fi
        [[ ${http_code:-} == 2* ]] || return 1
        echo "mounted '$url'"
    fi
}

# umount function
function _umount(){
    if [[ "$1" == "-i" ]]; then
        local ignore_http_code="true"
        shift
    fi
    local url="$*"
    if [[ ! $url ]]; then
        echo "umount needs an argument"
        _mount
    fi
    # get full path if relative reference
    if [[ ! "$url" =~ ^/ ]] && [[ ! "$url" =~ ^/sys/mounts ]]; then
        url="/sys/mounts/$current_pwd$url"
    elif [[ ! "$url" =~  ^/sys/mounts ]]; then
        url="/sys/mounts$url"
    fi
    if [[ $ignore_http_code ]]; then
        _curl -i "$url" "" "DELETE"
    else
        _curl "$url" "" "DELETE"
    fi
}

# cd function
function _cd(){
    local url="$1"
    if [[ $url == ".." ]]; then
        url=${current_pwd%*/}
        url=${url%/*}/
    elif [[ $url =~ ^/ ]]; then
        : # do nothing url is perfect as it is
    elif [[ $url ]]; then
        url="$current_pwd/$url/"
        url=${url//\/\//\/}
    elif [[ $current_pwd ]]; then
        url="$current_pwd/"
        url=${url//\/\//\/}
    else
        current_pwd="/"
    fi
    if [[ $url != "/" ]] && [[ "$1" != ".." ]] && ! _ls "$url" | grep -q "."; then
        echo "Error! '$url' not found"
    elif [[ $url != "/" ]]; then
        current_pwd="$url"
    else
        current_pwd=""
    fi
    debug "cd: cd $1 ; current_pwd=$current_pwd"
}

# ls function
function _ls(){
    local url tmp method
    if [[ $1 == "-_disable_colors_-" ]]; then
        local disable_colors="yes"
        shift
    fi
    if [[ "$1" == "-i" ]]; then
        local ignore_http_code="-i"
        shift
    fi
    if [[ ! $1 =~ ^/ ]]; then
        url="$current_pwd/$1"
    else
        url="$1"
    fi
    if [[ $url == '/' ]]; then
        if [[ $disable_colors ]]; then
            _cat /sys/mounts "-_list_root_-" | tr -d '"'
        else
            _cat /sys/mounts "-_list_root_-" | tr -d '"' | sed -e "s/\//$CYAN\/$NONE/"
        fi
        return
    elif [[ $url ]]; then
        url="$url"
        method="LIST"
    else
        url="$current_pwd"
        method="LIST"
    fi
    if [[ $disable_colors ]]; then
        tmp=$(mktemp -p "$TMP" -t ls.XXXX)
        _curl $ignore_http_code "$url" "" "$method" > "$tmp"
        jq '.data .keys[]' -r < "$tmp"
    else
        tmp=$(mktemp -p "$TMP" -t ls.XXXX)
        _curl $ignore_http_code "$url" "" "$method" > "$tmp"
        jq '.data .keys[]' -r < "$tmp" | sed -e "s/\//$CYAN\/$NONE/"
    fi
    rm -f "$tmp"
}

# lsrole list roles
function _lsrole(){
    local tmp
    tmp=$(mktemp -p "$TMP" -t lsrole.XXXX)
    _cat /auth/approle/role?list=true > "$tmp"
    jq '.data .keys[]' -r < "$tmp"
    rm "$tmp"
}

# showpol list role policies
function _showrole(){
    local funcname
    funcname="${FUNCNAME[0]}"
    local role="$1"
    local tmp
    if [[ ! $role ]]; then
        echo "Error! Missing role"
        commands_help "${funcname//_/}"
        return
    fi
    tmp=$(mktemp -p "$TMP" -t showrole.XXXX)
    _cat /auth/approle/role/"$role" > "$tmp"
    echo -n "policies: "
    jq -r '(.data.token_policies // .data.policies // [])[]' < "$tmp"
    rm "$tmp"
}

# lspol list policies
function _lspol(){
    local tmp
    tmp=$(mktemp -p "$TMP" .vcli.policies.XXXXXX) || return 1
    if ! _curl /sys/policies/acl "" LIST > "$tmp"; then
        cat "$tmp" >&2; rm -f "$tmp"; return 1
    fi
    jq -r '(.data.keys // .keys // [])[]' "$tmp"
    rm -f "$tmp"
}

# showpol list policy rules
function _showpol(){
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
}

function padecho(){
    local msg="$1"
    local depth="$2"
    local space="$3"
    local echospace="$4"
    # we print 2 spaces for every depth
    for (( i=${#space} ; i<$((depth*2)) ; i++ )) ; do
        # if is folder
        echo -n " "
    done
    msg=${msg/\/\//\/}
    msg=${msg//\//$CYAN\/$NONE}
    space=${space//+/$CYAN+$NONE}
    space=${space//\`/$CYAN\`$NONE}
    debug "${echospace}msg: $msg, depth: $depth ; space: \"$space\""
    echo "$space$msg"
}

# display the vault's secret tree
function _tree(){
    local url
    if [[ ! $1 =~ ^/ ]]; then
        url="$current_pwd$1"
    else
        url=${1//\/\//\/}
    fi
    local depth="$2"
    local space="$3"
    local echo_space="$4"
    # If no "$2" was passed then this iteration is the very first one, so depth=0
    if [[ ! $depth ]]; then
        depth=0
    # other wise we increment a depth as we go deeper in the structure
    else
        let depth++
    fi
    debug "${echo_space}tree: '$url', depth: $depth, space: \"$space\""
    # if $url is /
    padecho "$(basename "$url")/" "$depth" "$space" "$echo_space   "
    paths=$(_ls "-_disable_colors_-" "$url" | tr -t '\n' ' ' | tr -s ' ')
    for path in $paths; do
        debug "${echo_space}   depth: $depth => found: $paths"
        space=${space/+/ }
        if [[ $path =~ /$ ]]; then
            _tree "$url/$path" "$depth" "$space+ " "$echo_space   "
        else
            padecho "$path" "$((depth+1))" "$space\` " "$echo_space   "
        fi
    done
}

# find secret in the vault's tree
function _find(){
    local url
    if [[ ! $1 =~ ^/ ]]; then
        url="$current_pwd/$1"
    else
        url=${1//\/\//\/}
    fi
    shift
    local data
    data="$(echo "$@" | jq . -M -S | tr -t '\n' ' ' | tr -s ' ')"
    # if $url is /
    paths=$(_ls "-_disable_colors_-" "$url" | sed -e "s/,//" | tr -t '\n' ' ' | tr -s ' ')
    for path in $paths; do
        debug "${echo_space}find: '$url' => found: $paths"
        if [[ $path =~ /$ ]]; then
            _find "$url/$path" "$data"
        else
            secret=$(_cat "$url/$path" | jq -M -S ".data" | tr -t '\n' ' ' | tr -s ' ')
            debug "find: '$data' '$url'"
            if [[ $secret == "$data" ]]; then
                debug "find: $secret = $data!"
                padecho "$url/$path" "0" "" ""
            else
                debug "find: $secret == $data"
            fi
        fi
    done
}

# find in $1 and replace secret $2 with new secret $3
function _replace(){
    local root pairs expected replacement leaves leaf response
    root=$(_vcli_absolute_path "$1") || { echo "Invalid replace path" >&2; return 1; }
    pairs=$(printf '%s\n' "$2" | jq -sc 'if length == 2 and all(.[]; type == "object") then . else error("expected two objects") end') || return 1
    expected=$(jq -c '.[0]' <<< "$pairs") || return 1
    replacement=$(jq -c '.[1]' <<< "$pairs") || return 1
    leaves=$(_vcli_leaves "$root") || return 1
    while IFS= read -r leaf; do
        [[ -n $leaf ]] || continue
        local version=""
        if vcli_is_kv2_path "$leaf"; then
            response=$(_curl "$leaf" "" GET raw) || return 1
            version=$(jq -er '.data.metadata.version' <<< "$response") || return 1
            response=$(jq -c '{data: .data.data}' <<< "$response") || return 1
        else
            response=$(_cat "$leaf") || return 1
        fi
        if jq -e --argjson expected "$expected" '.data == $expected' <<< "$response" >/dev/null; then
            _put "$leaf" "$replacement" "$version" || return 1
            printf '%s\n' "$leaf"
        fi
    done <<< "$leaves"
}

# copy secret tree to another location
function _cptree(){
    local source dest leaves leaf relative response payload
    source=$(_vcli_absolute_path "$1") || { echo "Invalid source" >&2; return 1; }
    dest=$(_vcli_absolute_path "$2") || { echo "Invalid destination" >&2; return 1; }
    [[ $source != "$dest" && $dest != "$source/"* ]] || {
        echo "Destination must be outside source tree" >&2; return 1;
    }
    leaves=$(_vcli_leaves "$source") || return 1
    # Preflight *every* destination first. In KV v2 metadata survives soft
    # deletion, so checking /data alone could resurrect a deleted secret.
    local target probe mount rest metadata_path
    local -a kv_mounts=()
    if [[ ${VAULT_KV_VERSION:-1} == 2 ]]; then
        IFS=',' read -r -a kv_mounts <<< "${VAULT_KV_MOUNTS:-secret}"
    fi
    while IFS= read -r leaf; do
        [[ -n $leaf ]] || continue
        relative="${leaf#"$source"/}"
        target="$dest/$relative"
        metadata_path=""
        for mount in "${kv_mounts[@]}"; do
            mount="${mount#/}"; mount="${mount%/}"
            if [[ $target == "/$mount/"* ]]; then
                rest="${target#"/$mount/"}"
                metadata_path="/$mount/metadata/$rest"
                break
            fi
        done
        if [[ -n $metadata_path ]]; then
            probe="$metadata_path"
        else
            probe="$target"
        fi
        if _curl "$probe" >/dev/null; then
            echo "Destination already exists: $target" >&2
            return 1
        elif [[ $http_code != 404 ]]; then
            echo "Unable to verify destination: $target (HTTP $http_code)" >&2
            return 1
        fi
    done <<< "$leaves"
    local completed=0 total=0
    while IFS= read -r leaf; do [[ -n $leaf ]] && (( total+=1 )); done <<< "$leaves"
    while IFS= read -r leaf; do
        [[ -n $leaf ]] || continue
        relative="${leaf#"$source"/}"
        response=$(_cat "$leaf") || {
            _vcli_partial_failure cptree "$completed" "$total" "$leaf"; return 1;
        }
        payload=$(jq -ce '.data | select(type == "object")' <<< "$response") || {
            _vcli_partial_failure cptree "$completed" "$total" "$leaf"; return 1;
        }
        if vcli_is_kv2_path "$dest/$relative"; then
            _put "$dest/$relative" "$payload" 0
        else
            _put "$dest/$relative" "$payload"
        fi || {
            _vcli_partial_failure cptree "$completed" "$total" "$dest/$relative"; return 1;
        }
        (( completed+=1 ))
    done <<< "$leaves"
    printf "Copied tree from '%s' to '%s'\n" "$source" "$dest"
}

# removes recursively a vault path
function _rmtree(){
    local forced=no root leaves leaf answer
    if [[ ${1:-} == -f ]]; then forced=yes; shift; fi
    root=$(_vcli_absolute_path "${1:-}") || { echo "Missing or invalid tree path" >&2; return 1; }
    case "$root" in
        /|/sys|/sys/*|/auth|/auth/*|/identity|/identity/*|/cubbyhole|/cubbyhole/*)
            echo "Refusing to delete a reserved Vault path" >&2; return 1;;
    esac
    [[ ${root#/} == */* ]] || { echo "Refusing to delete a whole mount" >&2; return 1; }
    leaves=$(_vcli_leaves "$root") || return 1
    if [[ $forced != yes ]]; then
        [[ -r /dev/tty ]] || { echo "Use rmtree -f for batch mode" >&2; return 1; }
        read -r -p "Remove secrets beneath '$root'? (yes/no): " answer </dev/tty || return 1
        [[ $answer == yes ]] || return 1
    fi
    local completed=0 total=0
    while IFS= read -r leaf; do [[ -n $leaf ]] && (( total+=1 )); done <<< "$leaves"
    while IFS= read -r leaf; do
        [[ -n $leaf ]] || continue
        _rm "$leaf" || {
            _vcli_partial_failure rmtree "$completed" "$total" "$leaf"; return 1;
        }
        (( completed+=1 ))
    done <<< "$leaves"
    printf "Removed secrets beneath '%s'\n" "$root"
}

# backup vault
function _backup(){
    local root leaves leaf response payload dir archive relative
    root=$(_vcli_absolute_path "${1:-}") || { echo "Missing backup path" >&2; return 1; }
    [[ $root != / && ${root#/} == */* ]] || {
        echo "Backup requires a path within a secrets mount" >&2; return 1;
    }
    dir=$(mktemp -d -p "$TMP" .vcli-backup.XXXXXX) || return 1
    archive="$TMP/backup_vault_${root//\//-}.$(date +%s).tgz"
    if response=$(_cat "$root" 2>/dev/null); then
        leaves="$root"
    else
        leaves=$(_vcli_leaves "$root") || { rm -rf -- "$dir"; return 1; }
    fi
    while IFS= read -r leaf; do
        [[ -n $leaf ]] || continue
        response=$(_cat "$leaf") || { rm -rf -- "$dir"; return 1; }
        payload=$(jq -ce '.data | select(type == "object")' <<< "$response") || {
            rm -rf -- "$dir"; return 1;
        }
        relative="${leaf#/}"
        mkdir -p -- "$dir/$(dirname "$relative")" || { rm -rf -- "$dir"; return 1; }
        printf '%s\n' "$payload" > "$dir/$relative" || { rm -rf -- "$dir"; return 1; }
    done <<< "$leaves"
    if ! tar -czf "$archive" -C "$dir" .; then
        rm -f -- "$archive"
        rm -rf -- "$dir"
        return 1
    fi
    rm -rf -- "$dir"
    printf "Vault backup created: %s\n" "$archive"
}

# restore vault backup
function _restore(){
    local archive="${1:-}" listing entry relative payload types entry_type
    [[ -f $archive && -r $archive ]] || { echo "Missing backup file" >&2; return 1; }
    listing=$(tar -tzf "$archive") || return 1
    types=$(tar -tvzf "$archive") || return 1
    while IFS= read -r entry_type; do
        [[ -n $entry_type ]] || continue
        case "${entry_type:0:1}" in -|d) :;; *)
            echo "Unsupported archive member type" >&2; return 1;;
        esac
    done <<< "$types"
    # Validate every entry before writing anything; never extract to the local disk.
    while IFS= read -r entry; do
        [[ -n $entry ]] || continue
        relative="${entry#./}"
        case "$relative" in
            ""|.|*/) continue ;;
            /*|..|../*|*/../*|*/..|./*|*/./*|*/.)
                echo "Unsafe archive path: $entry" >&2; return 1;;
        esac
        case "$relative" in
            sys/*|auth/*|identity/*|cubbyhole/*)
                echo "Refusing administrative path" >&2; return 1;;
        esac
        [[ $relative == */* && $relative != *'//'* ]] || {
            echo "Invalid archive path: $entry" >&2; return 1;
        }
    done <<< "$listing"
    # Full preflight before any write: reject duplicate paths and non-object JSON.
    local -A seen_entries=()
    while IFS= read -r entry; do
        [[ -n $entry ]] || continue
        relative="${entry#./}"
        [[ -n $relative && $relative != . && $relative != */ ]] || continue
        if [[ -v seen_entries["$relative"] ]]; then
            echo "Duplicate archive member: $relative" >&2; return 1
        fi
        seen_entries["$relative"]=1
        payload=$(tar -xOzf "$archive" "$entry") || return 1
        jq -e 'type == "object"' <<< "$payload" >/dev/null || {
            echo "Invalid JSON object in archive: $relative" >&2; return 1;
        }
    done <<< "$listing"
    local completed=0 total=0
    while IFS= read -r entry; do
        relative="${entry#./}"
        [[ -n $relative && $relative != . && $relative != */ ]] && (( total+=1 ))
    done <<< "$listing"
    while IFS= read -r entry; do
        [[ -n $entry ]] || continue
        relative="${entry#./}"
        [[ -n $relative && $relative != . && $relative != */ ]] || continue
        payload=$(tar -xOzf "$archive" "$entry") || {
            _vcli_partial_failure restore "$completed" "$total" "$relative"; return 1;
        }
        _put "/$relative" "$payload" || {
            _vcli_partial_failure restore "$completed" "$total" "$relative"; return 1;
        }
        (( completed+=1 ))
    done <<< "$listing"
    printf "Vault backup restored: %s\n" "$archive"
}

# cat function
function _cat(){
    local funcname
    funcname="${FUNCNAME[0]}"
    if [[ "$1" == "-i" ]]; then
        local ignore_http_code="true"
        shift
    fi
    if [[ "$1" == "-_disable_-f_-" ]]; then
        disable_f="$1"
        shift
    fi
    local url="$1"
    if [[ ! $url =~ ^/ ]]; then
        url="$current_pwd$1"
    fi
    if [[ ! $url ]]; then
        echo "Error! Missing secret path"
        commands_help "${funcname//_/}"
        return
    fi
    debug "cat:  url=$url"
    local arg="$2"
    if [[ $ignore_http_code ]]; then
        _curl "$disable_f" -i "$url" "$arg"
    else
        _curl "$disable_f" "$url" "$arg"
    fi
}


# addsecret function
function _addsecret(){
    local url="$1" additions="$2" snapshot version merged current
    url=$(_vcli_absolute_path "$url") || return 1
    [[ -n $additions ]] || { echo "Missing secret data" >&2; return 1; }
    if vcli_is_kv2_path "$url"; then
        snapshot=$(_curl "$url" "" GET raw) || return 1
        version=$(jq -er '.data.metadata.version | select(type=="number")' <<< "$snapshot") || return 1
        current=$(jq -ce '.data.data | select(type=="object")' <<< "$snapshot") || return 1
    else
        snapshot=$(_cat "$url") || return 1
        current=$(jq -ce '.data | select(type=="object")' <<< "$snapshot") || return 1
        version=""
    fi
    merged=$(jq -cn --argjson cur "$current" --argjson add "$additions" '$cur + $add') || return 1
    _put "$url" "$merged" "$version"
}

# delsecret function
function _delsecret(){
    local url="$1" key="$2" snapshot version current updated
    url=$(_vcli_absolute_path "$url") || return 1
    [[ -n $key ]] || { echo "Missing key" >&2; return 1; }
    if vcli_is_kv2_path "$url"; then
        snapshot=$(_curl "$url" "" GET raw) || return 1
        version=$(jq -er '.data.metadata.version | select(type=="number")' <<< "$snapshot") || return 1
        current=$(jq -ce '.data.data | select(type=="object")' <<< "$snapshot") || return 1
    else
        snapshot=$(_cat "$url") || return 1
        current=$(jq -ce '.data | select(type=="object")' <<< "$snapshot") || return 1
        version=""
    fi
    updated=$(jq -cn --argjson obj "$current" --arg key "$key" '$obj | del(.[$key])') || return 1
    _put "$url" "$updated" "$version"
}

# cp function
function _cp(){
    local funcname
    funcname="${FUNCNAME[0]}"
    local url="$1"
    local dest="$2"
    if [[ ! $url =~ ^/ ]]; then
        url="$current_pwd$url"
    fi
    if [[ ! $dest =~ ^/ ]]; then
        dest="$current_pwd$dest"
    fi
    if [[ ! $url ]]; then
        echo "Error! Missing secret path"
        commands_help "${funcname//_/}"
        return
    fi
    if [[ ! $dest ]]; then
        echo "Error! Missing destination"
        commands_help "${funcname//_/}"
        return
    fi
    debug "cp: url=$url dest=$dest"
    secret=$(_cat "$url" | jq ".data" )
    debug "$secret"
    if vcli_is_kv2_path "$dest"; then
        _put "$dest" "$secret" 0
    else
        _put "$dest" "$secret"
    fi
}

# mv function
function _mv(){
    local source destination snapshot source_version payload dest_result latest latest_version
    source=$(_vcli_absolute_path "${1:-}") || return 1
    destination=$(_vcli_absolute_path "${2:-}") || return 1
    [[ $source != "$destination" ]] || { echo "Cannot move to same path" >&2; return 1; }

    # KV v1 has no version-specific delete; retain historical behavior.
    if ! vcli_is_kv2_path "$source"; then
        _cp "$source" "$destination" && _rm "$source"
        return $?
    fi

    # Snapshot the exact version to be copied, including nested KV2 mounts.
    snapshot=$(_curl "$source" "" GET raw) || return 1
    source_version=$(jq -er '.data.metadata.version | select(type=="number")' <<< "$snapshot") || return 1
    payload=$(jq -ce '.data.data | select(type=="object")' <<< "$snapshot") || return 1

    # Moving between engines is permitted; KV2 destination uses create-only CAS.
    if vcli_is_kv2_path "$destination"; then
        _put "$destination" "$payload" 0 || return 1
    else
        _put "$destination" "$payload" || return 1
    fi

    # After the copy, re-read the origin to detect an intervening writer.
    latest=$(_curl "$source" "" GET raw) || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    latest_version=$(jq -er '.data.metadata.version | select(type=="number")' <<< "$latest") || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    if [[ $latest_version != "$source_version" ]]; then
        echo "vcli: source changed during move; destination contains copied version" >&2
        _vcli_partial_failure mv 1 2 "$source"
        return 1
    fi

    # KV2 /delete only marks the given version, NEVER a later current version.
    # This cannot be made atomic with the destination write.
    local mount metadata path_file delete_path
    metadata=$(vcli_kv2_metadata_path "$source") || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    delete_path="${metadata/\/metadata\//\/delete\/}"
    path_file=$(mktemp -p "$TMP" .vcli-delete.XXXXXX) || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    printf '{"versions":[%s]}\n' "$source_version" > "$path_file"
    _curl "$delete_path" "$path_file" POST || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    # A newer version may have been created immediately before the deletion.
    # Detect it; never claim the whole move was successful in that case.
    local after after_version
    after=$(_curl "$metadata") || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    after_version=$(jq -er '.data.current_version | select(type=="number")' <<< "$after") || {
        _vcli_partial_failure mv 1 2 "$source"; return 1;
    }
    if [[ $after_version != "$source_version" ]]; then
        echo "vcli: source advanced during version-specific deletion" >&2
        _vcli_partial_failure mv 1 2 "$source"
        return 1
    fi
    printf "Moved version %s from '%s' to '%s'\n" "$source_version" "$source" "$destination"
}

# rm function
function _rm(){
    local path snapshot version metadata delete_path payload after after_version
    path=$(_vcli_absolute_path "${1:-}") || return 1
    if ! vcli_is_kv2_path "$path"; then
        _curl "$path" "" DELETE
        return $?
    fi
    # Read the live version, not the whole metadata history.
    snapshot=$(_curl "$path" "" GET raw) || return 1
    version=$(jq -er '.data.metadata.version | select(type=="number")' <<< "$snapshot") || return 1
    metadata=$(vcli_kv2_metadata_path "$path") || return 1
    delete_path="${metadata/\/metadata\//\/delete\/}"
    payload=$(mktemp -p "$TMP" .vcli-delete.XXXXXX) || return 1
    printf '{"versions":[%s]}\n' "$version" > "$payload"
    _curl "$delete_path" "$payload" POST || return 1
    after=$(_curl "$metadata") || return 1
    after_version=$(jq -er '.data.current_version | select(type=="number")' <<< "$after") || return 1
    if [[ $after_version != "$version" ]]; then
        printf 'vcli: source advanced during version-specific delete: %s\n' "$path" >&2
        return 1
    fi
}

# put function
function _put(){
    local cas="${3:-}"
    local funcname
    funcname="${FUNCNAME[0]}"
    local url="$1"
    if [[ ! $url =~ ^/ ]]; then
        url="$current_pwd$1"
    fi
    debug "put: url=$url"
    debug "data: data=$2"
    data=$(mktemp -p "$TMP" -t .put.XXXX)
    if command -v envsubst >/dev/null 2>&1; then
        printf '%s\n' "$2" | envsubst > "$data"
    else
        printf '%s\n' "$2" > "$data"
    fi
    if [[ ! $url ]]; then
        echo "Error! Missing secret path"
        commands_help "${funcname//_/}"
        return
    fi
    if [[ ! $data ]]; then
        echo "Error! Missing secret data"
        commands_help "${funcname//_/}"
        return
    fi
    url="${url/\/\//\/}"
    debug "$data"
    debug "uploading '$data' to '$url'"
    if [[ -z $cas ]] && vcli_is_kv2_path "$url"; then
        local metadata metadata_file
        metadata=$(vcli_kv2_metadata_path "$url") || return 1
        metadata_file=$(mktemp -p "$TMP" .vcli.metadata.XXXXXX) || return 1
        # Preserve _curl's HTTP status in this shell (not a subshell).
        if _curl "$metadata" > "$metadata_file"; then
            cas=$(jq -er '.data.current_version | select(type=="number")' "$metadata_file") || {
                rm -f "$metadata_file"; return 1;
            }
        elif [[ $http_code == 404 ]]; then
            cas=0
        else
            rm -f "$metadata_file"
            echo "vcli: unable to read KV v2 metadata before write" >&2
            return 1
        fi
        rm -f "$metadata_file"
    fi
    _curl "$url" "$data" "POST" "$cas"
}

# validate and show principals and keytab info
function _krbinfo(){
    local data stem principal
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_krb_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_principal")) | .[:-10]' <<< "$data")
    for stem in "${stems[@]}"; do
        principal=$(jq -er --arg k "${stem}_principal" '.[$k]' <<< "$data") || return 1
        printf 'principal: %s\n' "$principal" "$1"
    done
}

# download keytab to /tmp
function _vcli_krb_pairs(){
    local data="$1" stem principal encoded file
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_principal")) | .[:-10]' <<< "$data")
    (( ${#stems[@]} )) || return 1
    for stem in "${stems[@]}"; do
        [[ $stem =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $stem != *..* ]] || return 1
        principal=$(jq -er --arg k "${stem}_principal" '.[$k] | select(type=="string")' <<< "$data") || return 1
        [[ $principal =~ ^[A-Za-z0-9._/-]+@[A-Za-z0-9._-]+$ && $principal != *..* ]] || return 1
        encoded=$(jq -er --arg k "${stem}_keytab" '.[$k] | select(type=="string")' <<< "$data") || return 1
        file=$(mktemp -p "$TMP" .vcli-krb.XXXXXX) || return 1
        if ! printf '%s' "$encoded" | base64 --decode > "$file" 2>/dev/null ||
           ! klist -kt "$file" 2>/dev/null | grep -Fq "$principal"; then
            rm -f "$file"
            echo "vcli: invalid keytab or principal" >&2
            return 1
        fi
        rm -f "$file"
    done
    (( $(jq '[keys[] | select(endswith("_keytab"))] | length' <<< "$data") == ${#stems[@]} ))
}
function _getkrb(){
    local data stem principal encoded destination
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_krb_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_principal")) | .[:-10]' <<< "$data")
    local -a destinations=()
    for stem in "${stems[@]}"; do
        principal=$(jq -er --arg k "${stem}_principal" '.[$k]' <<< "$data") || return 1
        destinations+=("$TMP/${principal//\//_}.keytab")
    done
    _vcli_export_preflight "${destinations[@]}" || return 1
    for stem in "${stems[@]}"; do
        principal=$(jq -er --arg k "${stem}_principal" '.[$k]' <<< "$data") || return 1
        encoded=$(jq -er --arg k "${stem}_keytab" '.[$k]' <<< "$data") || return 1
        destination="$TMP/${principal//\//_}.keytab"
        (umask 077; printf '%s' "$encoded" | base64 --decode > "$destination") || return 1
        echo "Exported $destination"
    done
}

# validate that crt and key match and show certificate info
function _certinfo(){
    local data stem crt
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_cert_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    for stem in "${stems[@]}"; do
        crt=$(jq -er --arg k "${stem}_crt" '.[$k]' <<< "$data") || return 1
        printf '%s\n' "$crt" | openssl x509 -text -noout || return 1
    done
}

# print principal of kerberos secret
function _getprinc(){
    local data stem principal
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_krb_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_principal")) | .[:-10]' <<< "$data")
    for stem in "${stems[@]}"; do
        principal=$(jq -er --arg k "${stem}_principal" '.[$k]' <<< "$data") || return 1
        printf '%s\n' "$principal"
    done
}

# print CN and SAN of certificate secret
function _getsan(){
    local data stem crt
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_cert_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    for stem in "${stems[@]}"; do
        crt=$(jq -er --arg k "${stem}_crt" '.[$k]' <<< "$data") || return 1
        printf '%s\n' "$crt" | openssl x509 -noout -subject -ext subjectAltName || return 1
    done
}

# recover krb.list from certificates path
function _getkrblist(){
    local data stem principal
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_krb_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_principal")) | .[:-10]' <<< "$data")
    for stem in "${stems[@]}"; do
        principal=$(jq -er --arg k "${stem}_principal" '.[$k]' <<< "$data") || return 1
        printf '%s | %s\n' "$principal" "$1"
    done
}

# recover certs.list from certificates path
function _getcertlist(){
    local data stem crt sans
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_cert_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    for stem in "${stems[@]}"; do
        crt=$(jq -er --arg k "${stem}_crt" '.[$k]' <<< "$data") || return 1
        sans=$(printf '%s\n' "$crt" | openssl x509 -noout -ext subjectAltName 2>/dev/null) || return 1
        printf '%s | %s | %s\n' "$stem" "$sans" "$1"
    done
}

# returns expiration date for specified certificates secret
function _getcertexpiration(){
    local path="" option="" arg data stem crt end days now expiration
    for arg in "$@"; do
        case "$arg" in -oo|-ow|-oc) option="$arg";; -*) return 1;; "") ;; *) path="$arg";; esac
    done
    [[ -n $path ]] || return 1
    data=$(_vcli_pki_json "$path") || return 1
    _vcli_cert_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    now=$(date +%s)
    for stem in "${stems[@]}"; do
        crt=$(jq -er --arg k "${stem}_crt" '.[$k]' <<< "$data") || return 1
        end=$(printf '%s\n' "$crt" | openssl x509 -noout -enddate 2>/dev/null) || return 1
        expiration=$(date -d "${end#notAfter=}" +%s) || return 1
        days=$(( (expiration-now)/86400 ))
        [[ $option == -oo && $days -gt 30 ]] && continue
        [[ $option == -ow && $days -le 30 && $days -gt 7 ]] && continue
        [[ $option == -oc && $days -le 7 ]] && continue
        printf '%s (%s): %d days to renew certificate\n' "$stem" "$path" "$days"
    done
}

# recovers krb.list with all keytabs found under the specified folder
function _getallkrblist(){
    local root leaves path
    root=$(_vcli_absolute_path "${1:-}") || return 1
    leaves=$(_vcli_leaves "$root") || return 1
    while IFS= read -r path; do
        [[ -n $path ]] || continue
        local data
        data=$(_vcli_pki_json "$path") || return 1
        jq -e 'any(keys[]; endswith("_principal"))' <<< "$data" >/dev/null || continue
        _getkrblist "$path" || return 1
    done <<< "$leaves"
}

# recovers cert.list with all certificates found under the specified folder
function _getallcertlist(){
    local root leaves path
    root=$(_vcli_absolute_path "${1:-}") || return 1
    leaves=$(_vcli_leaves "$root") || return 1
    while IFS= read -r path; do
        [[ -n $path ]] || continue
        local data
        data=$(_vcli_pki_json "$path") || return 1
        jq -e 'any(keys[]; endswith("_crt"))' <<< "$data" >/dev/null || continue
        _getcertlist "$path" || return 1
    done <<< "$leaves"
}


# shows all certificate's expiration dates
function _getallcertexpiration(){
    local root leaves path option=""
    if [[ ${1:-} == -o* ]]; then option="$1"; shift; fi
    root=$(_vcli_absolute_path "${1:-}") || return 1
    [[ ${2:-} == -o* ]] && option="$2"
    leaves=$(_vcli_leaves "$root") || return 1
    while IFS= read -r path; do
        [[ -n $path ]] || continue
        local data
        data=$(_vcli_pki_json "$path") || return 1
        jq -e 'any(keys[]; endswith("_crt"))' <<< "$data" >/dev/null || continue
        _getcertexpiration "$path" "$option" || return 1
    done <<< "$leaves"
}

# download certificates crt and key
function _vcli_pki_json(){
    local response
    response=$(_cat "$1") || return 1
    jq -ce '.data | select(type=="object")' <<< "$response"
}
function _vcli_cert_pairs(){
    local data="$1" stem crt key certpub keypub
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    (( ${#stems[@]} > 0 )) || { echo "vcli: no certificates" >&2; return 1; }
    for stem in "${stems[@]}"; do
        [[ $stem =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ && $stem != .* && $stem != *..* ]] || return 1
        crt=$(jq -er --arg k "${stem}_crt" '.[$k] | select(type=="string")' <<< "$data") || return 1
        key=$(jq -er --arg k "${stem}_key" '.[$k] | select(type=="string")' <<< "$data") || return 1
        certpub=$(printf '%s\n' "$crt" | openssl x509 -pubkey -noout 2>/dev/null) || return 1
        keypub=$(printf '%s\n' "$key" | openssl pkey -pubout 2>/dev/null) || return 1
        [[ $certpub == "$keypub" ]] || { echo "vcli: certificate/private key mismatch" >&2; return 1; }
    done
    (( $(jq '[keys[] | select(endswith("_key"))] | length' <<< "$data") == ${#stems[@]} )) || return 1
}
function _vcli_export_preflight(){
    local path
    for path in "$@"; do
        [[ ! -e $path && ! -L $path ]] || {
            echo "vcli: export destination already exists" >&2; return 1;
        }
    done
}
function _getcert(){
    local data stem crt key
    data=$(_vcli_pki_json "${1:-}") || return 1
    _vcli_cert_pairs "$data" || return 1
    local -a stems
    mapfile -t stems < <(jq -r 'keys[] | select(endswith("_crt")) | .[:-4]' <<< "$data")
    local -a destinations=()
    for stem in "${stems[@]}"; do destinations+=("$TMP/$stem.crt" "$TMP/$stem.key"); done
    _vcli_export_preflight "${destinations[@]}" || return 1
    for stem in "${stems[@]}"; do
        crt=$(jq -er --arg k "${stem}_crt" '.[$k]' <<< "$data") || return 1
        key=$(jq -er --arg k "${stem}_key" '.[$k]' <<< "$data") || return 1
        (umask 077; printf '%s\n' "$crt" > "$TMP/$stem.crt" &&
           printf '%s\n' "$key" > "$TMP/$stem.key") || return 1
        echo "Exported $stem.crt and $stem.key"
    done
}

# main function
function main(){
    # First of all: check command dependencies
    check_dependency "$DEPENDENCIES"
    local msg info

    # Return vcli version and quit
    if [[ "$*" == "--version" ]]; then
        msg="vcli v$VERSION"
        if info=$(new_version 2>&1); then
            msg+=" (update available)\n$info"
        fi
        echo -e "$msg"
        exit
    fi

    local tmpmsg
    protocol="https"
    while getopts "v:p:t:hin" opt; do
        case $opt in
            v) vault_host=$OPTARG ;;       ### GLOBAL VARIABLES ###
            p) vault_port=$OPTARG ;;       ### GLOBAL VARIABLES ###
            t) vault_token=$OPTARG ;;      ### GLOBAL VARIABLES ###
            n) disable_prompt=yes ;;
            i) protocol="http";;
            h) help; exit 0 ;;
            *) help; exit 2 ;;
        esac
    done

    # check for new versions
    if new_version && [[ ! $disable_prompt ]]; then sleep 2 ; echo; fi

    # VAULT_ADDR overrides VAULT_HOST / VAULT_PORT when configured.
    if [[ -n ${VAULT_ADDR:-} ]]; then
        local address="${VAULT_ADDR%/}"
        if [[ $address =~ ^(https?)://(\[[^]]+\]|[^:/]+)(:([0-9]+))?$ ]]; then
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
    vault_token="${vault_token:-${VAULT_TOKEN:-}}"

    # Check for correct arguments
    check_arg "$vault_host" "Vault host"
    if [[ -z $vault_token ]]; then
        _vcli_login_approle || exit 1
    fi
    check_arg "$vault_token" "Vault token"

    # Check connectivity
    check_connectivity

    # BEGIN
    # initialize variables
    len=0
    pos=1
    history_index=-1

    # Never write or delete a secret merely to start vcli.
    # Vault permissions are checked when individual commands are executed.

    # load vcli history
    load_history

    # Show vault's health
    [[ ! $disable_prompt ]] && _cat "-_disable_-f_-" sys/health

    # print prompt
    prompt

    # Batch mode is line-oriented: do not use the interactive one-byte reader.
    # Propagate failed CLI command status even when no HTTP request was sent.
    if [[ ${disable_prompt:-} == yes ]]; then
        local batch_line batch_result=0
        while IFS= read -r batch_line || [[ -n $batch_line ]]; do
            [[ -z ${batch_line//[[:space:]]/} ]] && continue
            if ! run_cmd "$batch_line"; then batch_result=1; fi
        done
        save_history
        exit "$batch_result"
    fi

    # Ctrl+C clears the editing buffer without terminating the session.
    trap 'cmd=""; len=0; pos=1; history_index=-1; printf "\\n"; prompt' INT

    # Begin iteration
    IFS=
    while true; do
        lastkey=$_key
        # read key and process
        if ! read -r -sN1 _key; then
            # An interrupted read is recoverable; EOF should exit.
            if [[ -t 0 ]]; then
                continue
            fi
            clean_n_exit
        fi
        case $_key in
            $'\x1b')
                read -rsn1 -t 0.001 _key # flush read
                if [[ $_key == "[" ]]; then # CURSORS & SPECIAL CHARS
                    read -rsn1 -t 0.001 _key # flush read again
                    case $_key in
                        $'\x41') # up cursor: older history
                            get_prev_cmd
                        ;;
                        $'\x42') # down cursor: newer history
                            get_next_cmd
                        ;;
                        $'\x43') # right cursor
                            if [[ $pos -lt $((len+1)) ]]; then # if anything was typed allow to move cursor left
                                tput cuf 1 # move cursor right
                                let pos+=1 # increment cursor position
                                debug "pos: $pos right"
                            fi
                        ;;
                        $'\x44') # left cursor
                            if [[ $pos -gt 1 ]]; then # if anything was typed and position>0 allow to move cursor left
                                tput cub 1 # move cursor left
                                let pos-=1 # decrement cursor position
                                debug "pos: $pos left"
                            fi
                        ;;
                        $'\x46') # END
                                debug "END pressed"
                                [[ $len != 0 ]] && tput cuf $((len-pos+1)) # move cursor right to the end
                                let pos=len+1
                        ;;
                        $'\x48') # HOME
                                debug "HOME pressed"
                                [[ $len != 0 ]] && tput cub $((pos-1)) # move cursor left to the beginning
                                let pos=1
                        ;;
                    esac
                fi
                unset ENDHIST
            ;;
            $'\x0a') # ENTER
                unset ENDHIST
                is_command="true"
                debug "current_pwd=$current_pwd"
                [[ ! $disable_prompt ]] && echo
                if [[ $cmd ]] && [[ ! "$cmd" =~ ^[[:space:]]+$ ]]; then
                    cmd=$(echo "$cmd" | sed -e "s/^[[:space:]]\+\(.*\)/\1/g" -e "s/[[:space:]]\+$//g" | tr -s ' ')
                    if ! run_cmd "$cmd"; then exit_status=1; fi
                fi
            ;;
            $'\x7f') # BACKSPACE
                unset ENDHIST
                if [[ $len != 0 ]] && [[ $pos -gt 1 ]]; then # if anything was typed and position>0 allow to delete
                    echo -en "\b \b"
                    let len-=1
                    let pos-=1
                    tmpmsg="${#cmd}: \"$cmd\""
                    # remove $_key in $((pos-1))
                    cmd="${cmd:0:$((pos-1))}${cmd:$((pos)):${#cmd}}"
                    tput el # clean to the end of the line
                    restcmd="${cmd:$((pos-1)):${#cmd}}"
                    echo -n "$restcmd"
                    # reposition cursor after backspace only if we moved chars
                    if [[ ${#restcmd} != 0 ]]; then tput cub ${#restcmd} ; fi
                    debug "$tmpmsg => ${#cmd}: \"$cmd\""
                    debug "pos: $pos, len: $len"
                fi
            ;;
            $'\x7e') # ERASE
                unset ENDHIST
                if [[ $len != 0 ]] && [[ $pos -lt $((len+1)) ]]; then # if anything was typed and position>0 allow to delete
                    tput ech 1 # erase 1 character at $pos
                    let len-=1
                    tmpmsg="${#cmd}: \"$cmd\""
                    cmd=${cmd:0:$((pos-1))}${cmd:${pos}:$((${#cmd}-1))}
                    tput el # clean to the end of the line
                    #cmd=${cmd::-1} # delete last character
                    restcmd="${cmd:$((pos-1)):$((${#cmd}))}"
                    echo -n "$restcmd"
                    # reposition cursor after backspace only if we moved chars
                    if [[ ${#restcmd} != 0 ]]; then tput cub ${#restcmd} ; fi
                    debug "$tmpmsg => ${#cmd}: \"$cmd\""
                    debug "pos: $pos, len: $len"
                fi
            ;;
            $'\x09') # tab completions
                completion "$cmd"
                exit_status=$?

                if [[ $exit_status == 0 ]]; then
                    # move the cursor to the beginning to print the command selected
                    [[ $pos != 1 ]] && tput cub $((pos-1)) # move cursor left to the beginning

                    # print the command found in history
                    len=0
                    pos=1
                    tput el # clean to the end of the line
                    echo -n "$cmd"
                    tput el # clean to the end of the line
                    let len=${#cmd}
                    let pos=len+1
                else
                    # print the command found in history
                    len=0
                    pos=1
                    tput el # clean to the end of the line
                    echo -n "$cmd"
                    tput el # clean to the end of the line
                    let len=${#cmd}
                    let pos=len+1
                fi
            ;;
            $'\x12') # Ctrl+r
                if [[ ! $cmd ]]; then
                    # clean line
                    echo -e "\nvcli hint: hit Ctrl+r when typing command to search the vcli history"
                    print_history
                    is_command="true"
                else
                    rotate_found_cmds "$cmd"
                    debug "search_cmd: $search_cmd, \$SEARCHIST: $SEARCHIST"


                    # move the cursor to the beginning to print the command selected
                    [[ $pos != 1 ]] && tput cub $((pos-1)) # move cursor left to the beginning

                    # print the command found in history
                    echo -n "$cmd"

                    tput el # clean to the end of the line
                    let pos=$((${#cmd}+1))
                    let len=${#cmd}
                fi
            ;;
            
            $'\x15') # Ctrl+U
                cmd=""
                len=0
                pos=1
                printf '\r'
                prompt
                tput el
            ;;
            $'\x04') # Ctrl+D
                clean_n_exit
            ;;
            $'')
                clean_n_exit
            ;;
            $'\x20'|*) # NORMAL CHARS
                if [[ ! $disable_prompt ]]; then
                    tput ich 1 # insert chars rather than overwriting them
                    echo -n "$_key" #| hexdump -C
                fi
                let len+=1
                let pos+=1
                tmpmsg="${#cmd}: \"$cmd\""
                # insert $_key in $pos within $cmd string
                cmd="${cmd:0:$((pos-2))}$_key${cmd:$((pos-2)):${#cmd}}"
                debug " => ${#cmd}: \"$cmd\""
                debug "pos: $pos, len: $len"
            ;;
        esac
        # Exit history search mode
        if [[ "$_key" != "$lastkey" ]] && [[ "$_key" != $'\x12' ]]; then
            debug "exiting history search mode"
            unset SEARCHIST search_cmd tmp_history
        fi
        [[ $DEBUG_HEXDUMP == "yes" ]] && debug "\nhexdump: \n $(echo -n "$_key" | hexdump -C)"
        # print prompt if commadn was run
        if [[ $is_command ]]; then prompt ; unset is_command cmd pos len ; history_index=-1 ; pos=1; fi
    done < /dev/stdin
}

main "$@"
