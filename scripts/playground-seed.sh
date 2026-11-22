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

# A realistic, entirely synthetic multi-environment estate. Existing
# small fixtures remain stable for the functional smoke contracts.
seed_enterprise() {
    local env app payload encoded stem days
    encoded=$(base64 < "$STATE/fixtures/http.keytab" | tr -d '\n')
    for env in development staging production; do
        for app in payments identity catalog; do
            payload=$(jq -nc --arg env "$env" --arg app "$app" \
                '{username:($app+"_svc"),password:("FAKE-"+$env+"-"+$app),
                  host:($app+".db."+$env+".example.test"),port:5432}')
            api POST "kv2/data/enterprise/$env/$app/database/primary" \
                "$(jq -nc --argjson d "$payload" '{data:$d}')" >/dev/null
            api POST "kv2/data/enterprise/$env/$app/api/gateway" \
                "$(jq -nc --arg env "$env" --arg app "$app" \
                    '{data:{endpoint:("https://"+$app+"."+$env+".example.test"),api_key:("FAKE-"+$env+"-API-"+$app)}}')" >/dev/null
            api POST "kv2/data/enterprise/$env/$app/messaging/broker" \
                "$(jq -nc --arg env "$env" --arg app "$app" \
                    '{data:{username:($app+"_worker"),password:("FAKE-"+$env+"-BROKER-"+$app),tls:true}}')" >/dev/null
        done
        api POST "team/config/data/enterprise/$env/platform/feature-flags" \
            "$(jq -nc --arg env "$env" '{data:{environment:$env,features:{tls:true,metrics:true},replicas:3}}')" >/dev/null
        api POST "team/config/data/enterprise/$env/observability/collector" \
            "$(jq -nc --arg env "$env" '{data:{endpoint:("https://collector."+$env+".example.test"),token:"FAKE-TELEMETRY"}}')" >/dev/null
        # Separate genuine X.509 certificates per environment, with staggered
        # expiry dates. The keytab is intentionally a shared fixture identity.
        case "$env" in development) days=90;; staging) days=20;; production) days=3;; esac
        stem="enterprise_$env"
        openssl req -x509 -newkey rsa:2048 -nodes -days "$days" -sha256 \
            -subj "/CN=payments.$env.example.test" \
            -addext "subjectAltName=DNS:payments.$env.example.test,DNS:api.$env.example.test" \
            -keyout "$STATE/fixtures/$stem.key" \
            -out "$STATE/fixtures/$stem.crt" >/dev/null 2>&1
        chmod 600 -- "$STATE/fixtures/$stem.key" "$STATE/fixtures/$stem.crt"
        payload=$(jq -nc --arg stem "$stem" \
            --rawfile crt "$STATE/fixtures/$stem.crt" \
            --rawfile key "$STATE/fixtures/$stem.key" \
            '{($stem+"_crt"):$crt,($stem+"_key"):$key}')
        api POST "kv1/enterprise/pki/$env/payments/ingress" "$payload" >/dev/null
        payload=$(jq -nc --arg kt "$encoded" \
            '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt}')
        api POST "kv1/enterprise/kerberos/$env/shared/http" "$payload" >/dev/null
    done

    # Four exact-object matches across teams/locations and two non-matches.
    # The tour copies this branch before performing any destructive operation.
    local leaf
    for leaf in payments/eu/primary payments/us/primary identity/eu/primary catalog/eu/primary; do
        api POST "kv1/enterprise/legacy/$leaf" \
            '{"username":"shared_svc","password":"FAKE-LEGACY-OLD"}' >/dev/null
    done
    api POST kv1/enterprise/legacy/payments/eu/analytics \
        '{"username":"analytics","password":"FAKE-KEEP-ANALYTICS"}' >/dev/null
    api POST kv1/enterprise/legacy/catalog/us/backup \
        '{"username":"backup","password":"FAKE-KEEP-BACKUP"}' >/dev/null
}

# Stable manual reproduction fixtures, independent of the scale catalogue.
seed_flavours() (
    local f="$STATE/fixtures" encoded bad pair stem days
    encoded=$(base64 < "$f/http.keytab" | tr -d '\n')
    for stem in healthy warning critical; do
        case "$stem" in healthy) days=90;; warning) days=20;; critical) days=3;; esac
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
            -nodes -days "$days" -sha256 -subj "/CN=$stem.example.test" \
            -addext "subjectAltName=DNS:$stem.example.test,DNS:alt.$stem.example.test" \
            -keyout "$f/$stem.key" -out "$f/$stem.crt" >/dev/null 2>&1
    done
    # openssl ca supports explicit past validity rather than fake metadata.
    local ca_dir
    ca_dir=$(mktemp -d "$STATE/.expired-XXXXXXXX")
    trap 'rm -rf -- "$ca_dir"' EXIT
    (
        cd "$ca_dir"
        openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
            -subj /CN=expired.example.test -keyout "$f/expired.key" -out req.pem >/dev/null 2>&1
        touch index
        mkdir new
        printf '01\n' >serial
        cat >ca.cnf <<'CNF'
[ca]
default_ca=local
[local]
database=index
new_certs_dir=new
serial=serial
default_md=sha256
policy=pol
[pol]
commonName=supplied
[extensions]
subjectAltName=DNS:expired.example.test
CNF
        openssl ca -selfsign -batch -config ca.cnf -keyfile "$f/expired.key" \
            -in req.pem -startdate 20200101000000Z -enddate 20200102000000Z \
            -extensions extensions -notext -out "$f/expired.crt" >/dev/null 2>&1
    )
    chmod 600 -- "$f/"*.key "$f/"*.crt
    for stem in healthy warning critical expired; do
        pair=$(jq -nc --arg s "$stem" --rawfile crt "$f/$stem.crt" \
            --rawfile key "$f/$stem.key" '{($s+"_crt"):$crt,($s+"_key"):$key}')
        api POST "kv1/flavours/pki/valid/$stem" "$pair" >/dev/null
    done
    # A valid object with several pairs; negative pairs remain separate.
    api POST kv1/flavours/pki/valid/multiple \
        "$(jq -nc --rawfile a "$f/healthy.crt" --rawfile ak "$f/healthy.key" \
             --rawfile b "$f/critical.crt" --rawfile bk "$f/critical.key" \
             '{first_crt:$a,first_key:$ak,second_crt:$b,second_key:$bk}')" >/dev/null
    api POST kv1/flavours/pki/invalid/key-mismatch \
        "$(jq -nc --rawfile crt "$f/healthy.crt" --rawfile key "$f/critical.key" '{tls_crt:$crt,tls_key:$key}')" >/dev/null
    api POST kv1/flavours/pki/invalid/malformed '{"tls_crt":"NOT PEM","tls_key":"NOT PEM"}' >/dev/null
    api POST kv1/flavours/pki/invalid/missing-key \
        "$(jq -nc --rawfile crt "$f/healthy.crt" '{tls_crt:$crt}')" >/dev/null
    api POST kv1/flavours/pki/invalid/extra-key \
        "$(jq -nc --rawfile crt "$f/healthy.crt" --rawfile key "$f/healthy.key" '{tls_crt:$crt,tls_key:$key,extra_key:$key}')" >/dev/null

    pair=$(jq -nc --arg kt "$encoded" '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt}')
    api POST kv1/flavours/kerberos/valid/http "$pair" >/dev/null
    api POST kv1/flavours/kerberos/valid/multiple \
        "$(jq -nc --arg kt "$encoded" '{first_principal:"HTTP/service.example.test@EXAMPLE.TEST",first_keytab:$kt,second_principal:"HTTP/service.example.test@EXAMPLE.TEST",second_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/bad-base64 \
        '{"http_principal":"HTTP/service.example.test@EXAMPLE.TEST","http_keytab":"INVALID@@@"}' >/dev/null
    bad=$(printf 'not a keytab' | base64 | tr -d '\n')
    api POST kv1/flavours/kerberos/invalid/corrupt-keytab \
        "$(jq -nc --arg kt "$bad" '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/wrong-principal \
        "$(jq -nc --arg kt "$encoded" '{http_principal:"HTTP/other.example.test@EXAMPLE.TEST",http_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/prefix-principal \
        "$(jq -nc --arg kt "$encoded" '{http_principal:"HTTP/service.example.test@EXAMPLE",http_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/unsafe-principal \
        "$(jq -nc --arg kt "$encoded" '{http_principal:"../../escape@EXAMPLE.TEST",http_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/missing-keytab \
        '{"http_principal":"HTTP/service.example.test@EXAMPLE.TEST"}' >/dev/null
    api POST kv1/flavours/kerberos/invalid/extra-keytab \
        "$(jq -nc --arg kt "$encoded" '{http_principal:"HTTP/service.example.test@EXAMPLE.TEST",http_keytab:$kt,extra_keytab:$kt}')" >/dev/null
    api POST kv1/flavours/kerberos/invalid/shell-metachar \
        "$(jq -nc --arg kt "$encoded" '{http_principal:"$(echo SHOULD-NOT-EXECUTE)",http_keytab:$kt}')" >/dev/null

    # Exact JSON matching, scalar types, empty objects, collision fixtures.
    api POST kv1/flavours/json/exact '{"nested":{"b":2,"a":1},"value":"a  b"}' >/dev/null
    api POST kv1/flavours/json/different '{"nested":{"a":1,"b":2},"value":"a b"}' >/dev/null
    api POST kv1/flavours/json/types '{"string":"001","number":1,"float":1.5,"bool":true,"null":null,"array":[1,false,{"a":"b"}],"unicode":"Madrid — 日本","empty":{}}' >/dev/null
    api POST kv1/flavours/json/empty '{}' >/dev/null
    api POST kv1/flavours/collision/src/a '{"source":true}' >/dev/null
    api POST kv1/flavours/collision/src/z '{"source":true}' >/dev/null
    api POST kv1/flavours/collision/dest/z '{"sentinel":true}' >/dev/null
    for stem in kv2 team/config; do
        api POST "$stem/data/flavours/lifecycle/versioned" '{"data":{"revision":1}}' >/dev/null
        api POST "$stem/data/flavours/lifecycle/versioned" '{"data":{"revision":2}}' >/dev/null
        api POST "$stem/metadata/flavours/lifecycle/versioned" '{"cas_required":true}' >/dev/null
        api POST "$stem/data/flavours/lifecycle/soft-deleted" '{"data":{"previous":true}}' >/dev/null
        api POST "$stem/delete/flavours/lifecycle/soft-deleted" '{"versions":[1]}' >/dev/null
        api POST "$stem/data/flavours/lifecycle/destroyed" '{"data":{"previous":true}}' >/dev/null
        api POST "$stem/destroy/flavours/lifecycle/destroyed" '{"versions":[1]}' >/dev/null
    done
)

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
    seed_enterprise
    seed_flavours
    seed_access
}
