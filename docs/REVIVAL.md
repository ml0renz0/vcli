# VCLI 0.6.3-revival — conservative compatibility patch

This is **not VCLI 2.0**. It patches the original Bash 0.6.2 source from
https://github.com/ml0renz0/vcli, preserving the REPL, command names, and
most of the existing implementation. The repository is currently archived;
this package does **not** change anything on GitHub.

## Apply locally

```bash
git clone https://github.com/ml0renz0/vcli.git
cd vcli
python3 /path/to/vcli-revival/revive_vcli.py ./vcli
bash -n ./vcli
```

The patcher expects *exactly* version 0.6.2 and checks source anchors; it
fails without modifying the original if any key anchor differs. It saves the
unmodified script to `vcli.orig` and checks Bash syntax before replacing the
working file. No Python runtime is needed to **run** VCLI, only to apply the
one-time patch.

## Try read-only first

```bash
export VAULT_ADDR='https://vault.example.com:8200'
export VAULT_TOKEN='...' # obtain securely, don't paste into shell history
# If secret/ is a KV v2 mount:
export VAULT_KV_VERSION=2
export VAULT_KV_MOUNTS=secret
./vcli
```

For multiple KV v2 mounts use e.g. `VAULT_KV_MOUNTS=secret,team/config`.
You can use `./vcli -n` for batch mode. Commands and JSON payload shapes
remain as close as possible to the original, including `put`, `cat`, `ls`.

**TLS:** verification is now **on** by default. Set `VAULT_CACERT` (or
`VAULT_CAPATH`) for an internal CA. `VAULT_SKIP_VERIFY=true` is an explicit
unsafe workaround for non-production testing only. `VAULT_NAMESPACE` sets
`X-Vault-Namespace` for Vault Enterprise.

## What changes

- Replaces HTTP `eval` with shell argument arrays and real `curl` invocations.
  The token is supplied via stdin to `curl --header @-`, rather than being
  passed as a command-line argument.
- Removes `curl -k` as the default, removes blind redirect following, and
  retains HTTP error responses for debugging.
- Avoids the write/delete test-secret operation at startup. Read-only tokens
  can start the shell.
- Adds **opt-in** KV v2 path rewriting and read/write shape conversion while
  leaving KV v1 unmodified by default. `DELETE` on KV v2 remains a soft delete.
- Uses `/sys/policies/acl` instead of deprecated policy paths.
- Uses `kv` instead of `generic` for new mounts, retaining KV v1 as the
  default creation mode unless you set `VAULT_KV_VERSION=2`.
- Uses `$TMPDIR` (default `/tmp`) instead of requiring `/dev/shm`; sets
  restrictive `umask 077` for temporary/secret exports.
- Makes OpenSSL, klist, bc and envsubst optional **at startup**. Special
  certificate and Kerberos commands may still need the respective tools;
  variable expansion in `put` needs `envsubst`, otherwise the text is written
  literally.
- Stops recording **new** commands with secret-bearing JSON payloads into
  `.vclistory` by default; opt-in with `VCLI_HISTORY_SECRETS=true`. Existing
  history entries are left alone and should be cleaned up if sensitive.
- Makes colors optional in non-TTY environments, fixes URL encoding of `?`
  so query strings such as `?list=true` survive, and disables obsolete GitHub
  release polling at startup.

## Tests run for this package

```bash
python3 test_patcher.py
bash test_transport.sh
python3 test_http_transport.py
```

The first test transforms a **synthetic** original-0.6.2 fixture and runs
`bash -n`; the second tests the transport using a Bash curl stub; the third
uses a local HTTP server and **real curl** for KV v1/v2 GET, LIST, POST,
AppRole list query, and Vault health. These tests have passed in the
construction environment. They are **not** tests against a running Vault
server or a full patched copy of the original 2,300-line script.

## Known limitations / follow-up items

1. KV v2 is manually configured (`VAULT_KV_VERSION=2` and `VAULT_KV_MOUNTS`);
   per-mount auto-discovery and mixed KV v1/v2 mount configurations are not
   implemented. Do not enable KV v2 mode for a KV v1 mount.
2. KV v2 `rm` is a soft-delete, so `rmtree` does not purge metadata/versions.
   Test backup/restore, tree traversal, and recursive operations before
   applying them to important data.
3. `addsecret` and `delsecret` are legacy read-modify-write operations; KV v2
   CAS race protection is **not** implemented. Avoid concurrent updates.
4. Legacy certificate and Kerberos utilities are unchanged except for
   private temp files. They still require OpenSSL/klist/bc and may need
   separate work for modern formats.
5. Not yet verified end-to-end with a real Vault instance, any enterprise
   policy restrictions, self-signed CA, IPv6, or a complete original source
   checkout in this environment.
6. The original character-by-character REPL and parser have other legacy
   quirks. In particular, beware of commands containing complicated JSON
   escaping. Prefer read-only smoke tests first.

**Never use a privileged production token to test an unverified patch.**

## NEW: Phase 0 — real Vault CI/CD (design + runnable scaffold)

The package now contains `.github/workflows/ci.yml`,
`tests/integration/` and `docs/CI-CD.md`. This is the **required prerequisite**
for VCLI 0.7, not a substitute for actually running the test matrix.

After unarchiving your original GitHub repository, copy the package files
into the repository root and open a PR. The workflow checks Bash syntax,
ShellCheck, source invariants, current isolated tests, plus the **full** patched
script against ephemeral real Vault containers tagged `1.20.4`, `1.21.4`,
`2.0.4` and `2.1.1`. Each container provisions KV v1/v2, a nested KV v2
mount, AppRole, a read-only policy and a restricted token. Tests call actual
VCLI commands and independently inspect state using Vault's HTTP API; an
HTTPS proxy tests trusted vs untrusted CAs. A basic PTY test opens the REPL.

```bash
# Local prerequisites: Docker, Python 3, Bash, jq, curl, openssl, gettext-base
VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
```

No live-Vault or GitHub Actions run has occurred in this environment, so
**this new CI design and its end-to-end tests are unverified**. They are
intentionally strict and may report genuine failures in the current revival
patch. A failing check blocks releases; do not mark it as `continue-on-error`.

**Outstanding 0.7 blockers:** automated end-to-end coverage for destructive
recursive commands, backup/restore, `find`/`replace`, full keyboard completion
and history, concurrency/CAS, PKI/Kerberos, fault injection and distro matrix.
See `docs/CI-CD.md` for the exact coverage gaps and release policy.
