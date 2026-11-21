#!/usr/bin/env bash
# Real Vault TLS; all displayed secrets are seeded dummy fixtures.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -f .playground/env.sh ]] || { echo "Start playground: scripts/playground.sh up" >&2; exit 1; }
title(){
    printf '\033[1;36m\n%s\n\033[0m' "$1"
    sleep 0.3
}
tour(){
    local role="$1" cmd="$2"
    printf '\n\033[1;32mvcli (%s) >\033[0m %s\n' "$role" "$cmd"
    printf '%s\n' "$cmd" | scripts/playground.sh batch "$role"
    sleep 0.4
}
title 'vcli OG | Playground con Vault 2.1.1 y TLS real'
printf 'Solo datos ficticios: sin conexión a producción\n'
scripts/playground.sh status
tour readonly 'ls kv1/demo/'
tour readonly 'tree kv1/demo/nested'
tour readonly 'cat kv1/demo/nested/config'
tour readonly 'cat kv2/demo/payments/database'
tour readonly 'cat team/config/demo/payments'
tour readonly 'getsan kv1/demo/pki/web'
tour readonly 'getprinc kv1/demo/kerberos/http'
tour admin 'lsrole'
tour admin 'showrole vcli-playground-reader'
tour admin 'showpol vcli-playground-read'
title 'Autorización: operación de escritura denegada'
printf '\n\033[1;32mvcli (readonly) >\033[0m put kv1/demo/app {"x":"DENIED"}\n'
if printf 'put kv1/demo/app {"x":"DENIED"}\n' | scripts/playground.sh batch readonly; then
    echo 'ERROR: el token readonly pudo escribir' >&2
    exit 1
fi
printf '\033[1;33mEscritura denegada correctamente.\033[0m\n'
title 'Fin de la demo | vcli Bash contra Vault TLS real'
