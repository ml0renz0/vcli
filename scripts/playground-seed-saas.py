#!/usr/bin/env python3
"""Additional synthetic multi-tenant catalogue; no dependency of vcli itself.

Called only by playground.sh after ownership, loopback and TLS verification.
All writes use the three existing KV mounts, with bounded parallelism.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import stat
import sys
from urllib.parse import urlsplit

TENANTS = tuple(f"tenant-{i:03d}" for i in range(1, 33))
ENVS = ("development", "staging", "production")
REGIONS = ("eu-west", "us-east", "ap-south")
SERVICES = ("payments", "identity", "catalog", "billing", "search", "notifications")
SHARED = {"username": "legacy_gateway", "password": "FAKE-SHARED-OLD"}
MOUNTS = ("kv1", "kv2", "team/config")


def private(path):
    attrs = path.lstat()
    if (not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid()
            or stat.S_IMODE(attrs.st_mode) != 0o600 or attrs.st_nlink != 1):
        raise RuntimeError("Unsafe private playground file")
    return path.read_text().strip()


def endpoint(mount, relative):
    return mount + ("/" if mount == "kv1" else "/data/") + relative


def fake(*parts):
    return "FAKE-" + hashlib.sha256("/".join(parts).encode()).hexdigest()[:24]


def records(tenant, certificates, keytab):
    """Yield (mount, relative path, current object, previous object or None)."""
    for env in ENVS:
        for region in REGIONS:
            for svc in SERVICES:
                path = f"saas/{tenant}/{env}/{region}/{svc}"
                ident = (tenant, env, region, svc)
                host = ".".join((svc, tenant, region, env, "example.test"))
                db = {"username": svc + "_rw", "password": fake(*ident, "v2"),
                      "host": "db." + host, "port": 5432, "database": svc,
                      "tls": {"required": True, "mode": "verify-full"},
                      "replicas": ["replica-1." + host, "replica-2." + host]}
                yield "kv2", path + "/database/postgres/primary", db, {
                    **db, "password": fake(*ident, "v1")}
                yield "kv2", path + "/integrations/api/gateway", {
                    "endpoint": "https://api." + host,
                    "api_key": fake(*ident, "api"), "scopes": ["read", "write"]}, None
                yield "kv2", path + "/messaging/kafka/producer", {
                    "brokers": ["kafka-1." + host + ":9093", "kafka-2." + host + ":9093"],
                    "sasl": {"mechanism": "SCRAM-SHA-512", "username": svc,
                             "password": fake(*ident, "kafka")}}, None
                yield "team/config", path + "/deployment/runtime/config", {
                    "tenant": tenant, "environment": env, "region": region,
                    "replicas": 3 if env == "production" else 1,
                    "feature_flags": {"audit": True, "canary": env == "staging"},
                    "limits": {"cpu": "500m", "memory": "512Mi"},
                    "optional_endpoint": None}, None
                if svc == "identity":
                    yield "kv1", path + "/legacy/kerberos/http", {
                        "http_principal": "HTTP/service.example.test@EXAMPLE.TEST",
                        "http_keytab": keytab}, None
                elif svc == "payments":
                    yield "kv1", path + "/legacy/pki/ingress", certificates[env], None
                else:
                    value = (dict(SHARED) if svc in ("billing", "catalog") else
                             {"username": svc + "_worker",
                              "password": fake(*ident, "legacy")})
                    yield "kv1", path + "/legacy/credentials/service", value, None


def seed(state, address):
    info = urlsplit(address)
    if (info.scheme != "https" or info.hostname != "127.0.0.1"
            or not info.port or info.path or info.query or info.fragment
            or info.username or info.password):
        raise RuntimeError("SaaS seed only permits owned HTTPS loopback")
    token = private(state / "root-token")
    private(state / "ca.pem")
    context = ssl.create_default_context(cafile=str(state / "ca.pem"))
    certificates = {}
    for env in ENVS:
        stem = "enterprise_" + env
        certificates[env] = {
            stem + "_crt": (state / "fixtures" / (stem + ".crt")).read_text(),
            stem + "_key": (state / "fixtures" / (stem + ".key")).read_text()}
    keytab = base64.b64encode((state / "fixtures" / "http.keytab").read_bytes()).decode()

    def tenant_worker(tenant):
        conn = http.client.HTTPSConnection("127.0.0.1", info.port, context=context, timeout=30)
        try:
            def post(path, payload):
                conn.request("POST", "/v1/" + path, json.dumps(payload),
                             {"X-Vault-Token": token, "Content-Type": "application/json"})
                response = conn.getresponse()
                response.read()
                if response.status not in (200, 204):
                    raise RuntimeError(f"Vault seed HTTP {response.status}: {path}")

            count = revisions = 0
            for mount, path, value, previous in records(tenant, certificates, keytab):
                if previous is not None:
                    post(endpoint(mount, path), {"data": previous, "options": {"cas": 0}})
                    revisions += 1
                payload = value if mount == "kv1" else {
                    "data": value, "options": {"cas": int(previous is not None)}}
                post(endpoint(mount, path), payload)
                count += 1

            # One AppRole per tenant, isolated at ALL three mount layouts.
            # No tenant auth credentials are emitted or persisted in the seed.
            name = "saas-" + tenant
            acl = "\n".join(
                f'path "{prefix}/saas/{tenant}/*" {{ capabilities = [{caps}] }}'
                for prefix, caps in (
                    ("kv1", '"read", "list"'), ("kv2/data", '"read"'),
                    ("kv2/metadata", '"read", "list"'), ("team/config/data", '"read"'),
                    ("team/config/metadata", '"read", "list"')))
            post("sys/policies/acl/" + name, {"policy": acl})
            post("auth/approle/role/" + name, {
                "token_policies": [name], "token_no_default_policy": True,
                "token_ttl": "30m", "token_max_ttl": "1h"})
            return count, revisions
        finally:
            conn.close()

    # Persistent TLS connection per tenant; bounded concurrency keeps local
    # Vault responsive and avoids thousands of curl/jq process launches.
    counts = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for tenant, result in zip(TENANTS, pool.map(tenant_worker, TENANTS)):
            counts.append(result)
            print(f"Seeded {tenant}: {result[0]} live secrets", flush=True)
    live = sum(c[0] for c in counts)
    revisions = sum(c[1] for c in counts)
    if (live, revisions) != (8640, 1728):
        raise RuntimeError("Unexpected SaaS seed counts")
    print(f"SaaS seed complete: {live} live secrets, {revisions} prior revisions, 32 tenant AppRoles")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: playground-seed-saas.py .playground HTTPS_LOOPBACK")
    state = Path(sys.argv[1])
    mode = state.lstat()
    if (state.resolve() != Path(__file__).resolve().parents[1] / ".playground"
            or not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid()
            or stat.S_IMODE(mode.st_mode) != 0o700):
        raise SystemExit("Refusing unowned playground")
    seed(state, sys.argv[2])
