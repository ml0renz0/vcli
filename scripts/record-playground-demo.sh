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
output="${1:-$ROOT/vcli-playground.cast}"
[[ $output == /* ]] || output="$ROOT/$output"
[[ $output == *.cast ]] || { echo 'Output must end in .cast' >&2; exit 2; }
mkdir -p -- "$(dirname -- "$output")"
export TERM=xterm-256color COLUMNS=100 LINES=30
# CI has no controlling terminal: util-linux script supplies the PTY.
script -q -e -c \
  "asciinema rec --overwrite --idle-time-limit 1 --title 'vcli OG | Vault TLS playground' --command 'bash scripts/playground-demo.sh' '$output'" \
  /dev/null
[[ -s $output ]] || { echo 'Recording is empty' >&2; exit 1; }
chmod 600 -- "$output"
printf 'Saved asciinema recording: %s\n' "$output"
