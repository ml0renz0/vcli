#!/usr/bin/env bash
# Capture a real, local, seeded playground as an asciinema cast.
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$ROOT"
(($# <= 1)) || { echo "Usage: $0 [path/to/demo.cast]" >&2; exit 2; }
command -v asciinema >/dev/null || { echo 'Install asciinema first' >&2; exit 1; }
[[ -f .playground/env.sh && -f .playground/ready ]] || {
    echo 'Run scripts/playground.sh up first' >&2
    exit 1
}
profile=standard
if [[ -f .playground/profile ]]; then profile=$(<.playground/profile); fi
default="$ROOT/vcli-playground.cast"
if [[ $profile == saas ]]; then default="$ROOT/vcli-playground-saas.cast"; fi
output="${1:-$default}"
[[ $output == /* ]] || output="$ROOT/$output"
[[ $output == *.cast ]] || { echo 'Output must end in .cast' >&2; exit 2; }
mkdir -p -- "$(dirname -- "$output")"
command -v python3 >/dev/null || { echo 'Install python3 for the demo driver' >&2; exit 1; }
private=$(mktemp -d)
trap 'rm -rf -- "$private"' EXIT
export VCLI_DEMO_DONE="$private/done" VCLI_DEMO_OUTPUT="$output"
export ASCIINEMA_CONFIG_HOME="$private" TERM=xterm-256color COLUMNS=100 LINES=30
# CI has no controlling terminal: util-linux script supplies the PTY.
script -q -e -c \
  'stty rows 30 cols 100; asciinema rec --overwrite --title "vcli OG | Vault TLS playground" --command "bash scripts/playground-demo.sh" "$VCLI_DEMO_OUTPUT"' \
  /dev/null
[[ -f $VCLI_DEMO_DONE ]] || { rm -f -- "$output"; echo 'Interactive demo failed' >&2; exit 1; }
[[ -s $output ]] || { echo 'Recording is empty' >&2; exit 1; }
chmod 600 -- "$output"
printf 'Saved asciinema recording: %s\n' "$output"
