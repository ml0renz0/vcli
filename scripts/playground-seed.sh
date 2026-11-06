#!/usr/bin/env bash
# Sourced by playground.sh. Never source from an untrusted working directory.
# Every value is synthetic: a generated self-signed TLS pair, a static
# structurally valid MIT keytab (NOT backed by any Kerberos KDC), and KV data.
# The names and JSON shapes match the real VCLI PKI/Kerberos integration tests.

seed_pem() {
    mkdir -m 700 -- "$STATE/fixtures"
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -sha256 \
        -subj '/CN=playground.example.test' \
        -addext 'subjectAltName=DNS:playground.example.test,DNS:alt.playground.example.test' \
        -keyout "$STATE/fixtures/web.key" \
        -out "$STATE/fixtures/web.crt" >/dev/null 2>&1
    openssl req -x509 -newkey rsa:2048 -nodes -days 3 -sha256 \
        -subj '/CN=expiring.playground.example.test' \
        -addext 'subjectAltName=DNS:expiring.playground.example.test' \
        -keyout "$STATE/fixtures/expiring.key" \
        -out "$STATE/fixtures/expiring.crt" >/dev/null 2>&1
    chmod 600 -- "$STATE/fixtures/"*
}

seed_keytab() {
    # MIT keytab v2, AES256 entry for HTTP/service.example.test@EXAMPLE.TEST.
    # All key bytes are public, fixed, synthetic (00..1f); NO KDC is created.
    # The exact binary matches the E2E keytab fixture's documented structure.
    printf '%s' \
        'BQIAAABZAAIADEVYQU1QTEUuVEVTVAAESFRUUAAUc2VydmljZS5leGFtcGxlLnRlc3QAAAABZVPxAAEAEgAgAAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=' |
        base64 --decode > "$STATE/fixtures/http.keytab"
    chmod 600 -- "$STATE/fixtures/http.keytab"
    klist -kt "$STATE/fixtures/http.keytab" |
        grep -Fq 'HTTP/service.example.test@EXAMPLE.TEST' ||
        die 'synthetic keytab failed native MIT Kerberos verification'
}

seed_kv() {
    local cert_json expiring_json broken_json encoded
    cert_json=$(jq -nc \
        --rawfile crt "$STATE/fixtures/web.crt" \
        --rawfile key "$STATE/fixtures/web.key" \
        '{web_crt:$crt,web_key:$key}')
    expiring_json=$(jq -nc \
        --rawfile crt "$STATE/fixtures/expiring.crt" \
        --rawfile key "$STATE/fixtures/expiring.key" \
        '{service_crt:$crt,service_key:$key}')
    broken_json=$(jq -nc \
        --rawfile crt "$STATE/fixtures/web.crt" \
        --rawfile key "$STATE/fixtures/expiring.key" \
        '{web_crt:$crt,web_key:$key}')
    encoded=$(base64 < "$STATE/fixtures/http.keytab" | tr -d '\n')

    # KV v1: flat credentials, nested JSON, cert/key pairs and MIT keytabs.
    api POST kv1/demo/app \
        '{"user":"demo","password":"FAKE-VCLI-PASSWORD"}' >/dev/null
    api POST kv1/demo/nested/config \
        '{"region":"lab","enabled":true,"limits":{"retries":3,"timeout":15},"roles":["reader","writer"]}' >/dev/null
    api POST kv1/demo/pki/web "$cert_json" >/dev/null
    api POST kv1/demo/pki/nested/service "$expiring_json" >/dev/null
    api POST kv1/demo/kerberos/http "$(jq -nc --arg kt "$encoded" \
        '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt}')" >/dev/null
    api POST kv1/demo/kerberos/nested/http "$(jq -nc --arg kt "$encoded" \
        '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt}')" >/dev/null

    # Deliberately broken fixtures live outside the healthy recursive folders,
    # so getallcertlist/getallkrblist can enumerate valid data successfully.
    api POST kv1/negative/pki/key-mismatch "$broken_json" >/dev/null
    api POST kv1/negative/pki/malformed \
        '{"web_crt":"NOT A CERT","web_key":"NOT A KEY"}' >/dev/null
    api POST kv1/negative/pki/missing-key \
        "$(jq -nc --rawfile crt "$STATE/fixtures/web.crt" '{web_crt:$crt}')" \
        >/dev/null
    api POST kv1/negative/kerberos/bad-base64 \
        '{"http_principal":"HTTP/service.example.test@EXAMPLE.TEST","http_keytab":"INVALID@@@"}' >/dev/null
    api POST kv1/negative/kerberos/unsafe-principal \
        "$(jq -nc --arg kt "$encoded" \
            '{http_principal:"../../escape@EXAMPLE.TEST",http_keytab:$kt}')" \
        >/dev/null

    # KV v2 history: two distinct versions of a fake database credential.
    api POST kv2/data/demo/payments/database \
        '{"data":{"username":"sandbox","password":"FAKE-VERSION-1"}}' >/dev/null
    api POST kv2/data/demo/payments/database \
        '{"data":{"username":"sandbox","password":"FAKE-VERSION-2"}}' >/dev/null
    api POST kv2/data/demo/catalog/api \
        '{"data":{"key":"FAKE-API-KEY","endpoint":"https://example.invalid"}}' >/dev/null

    # A nested KV v2 mount is not a path inside the regular kv2 mount.
    api POST team/config/data/demo/payments \
        '{"data":{"team":"demo","region":"local","feature_flags":{"tls":true}}}' \
        >/dev/null
}

seed_access() {
    vault_cli policy write vcli-playground-read - >/dev/null <<'HCL'
path "kv1/*" { capabilities = ["read", "list"] }
path "kv2/data/*" { capabilities = ["read"] }
path "kv2/metadata/*" { capabilities = ["read", "list"] }
path "team/config/data/*" { capabilities = ["read"] }
path "team/config/metadata/*" { capabilities = ["read", "list"] }
HCL
    vault_cli write auth/approle/role/vcli-playground-reader \
        token_policies=vcli-playground-read token_no_default_policy=true \
        >/dev/null
    local value
    value=$(vault_cli token create -policy=vcli-playground-read -orphan -field=token)
    write_private readonly-token "$value"
    value=$(vault_cli read -field=role_id \
        auth/approle/role/vcli-playground-reader/role-id)
    write_private role-id "$value"
    value=$(vault_cli write -f -field=secret_id \
        auth/approle/role/vcli-playground-reader/secret-id)
    write_private secret-id "$value"
}

seed_playground() {
    seed_mount kv1 1
    seed_mount kv2 2
    seed_mount team/config 2
    if ! vault_cli auth list -format=json | jq -e 'has("approle/")' >/dev/null; then
        vault_cli auth enable approle >/dev/null
    fi
    seed_pem
    seed_keytab
    seed_kv
    seed_access
}
