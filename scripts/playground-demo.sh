#!/usr/bin/env bash
# Drive a real interactive Bash terminal; the only displayed bytes come from its PTY.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -f .playground/env.sh && -f .playground/ready ]] || {
    echo 'Start playground: scripts/playground.sh up' >&2; exit 1
}
exec python3 - <<'PY'
import errno
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
from pathlib import Path

# This is a real Bash prompt, not a simulated VCLI prompt.
SHELL = rb"playground\$ $"
VCLI = rb"\[([0-9]{3})\] [^\r\n]* > $"
ANSI = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\([A-Za-z])")
size = struct.pack("HHHH", 30, 100, 0, 0)
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, size)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TERM") if k in os.environ}
    env.update(PS1="playground$ ", HISTFILE="/dev/null", TERM="xterm-256color")
    os.execvpe("bash", ["bash", "--noprofile", "--norc", "-i"], env)

def output(timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return None
    try:
        data = os.read(fd, 65536)
    except OSError as exc:
        if exc.errno != errno.EIO:
            raise
        return b""
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return data

def wait_prompt(pattern):
    data = bytearray()
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        chunk = output(0.1)
        if chunk == b"":
            raise RuntimeError("terminal exited before the expected prompt")
        if chunk:
            data.extend(chunk)
            plain = ANSI.sub(b"", data)
            match = re.search(pattern, plain)
            if match:
                return plain, match
    raise RuntimeError("timed out waiting for the real terminal prompt")

def type_line(command):
    # Write real keystrokes, allowing the CLI's own editor to echo them.
    time.sleep(0.35)
    for key in command.encode() + b"\n":
        os.write(fd, bytes([key]))
        time.sleep(0.035)

# Track every actual VCLI command typed and accepted at a prompt. Compare
# the manifest against the executable's public commands before publishing.
executed = set()

def run(command, prompt=VCLI, expected=b"", code=None):
    type_line(command)
    data, match = wait_prompt(prompt)
    if expected and expected not in data:
        raise RuntimeError("expected command output missing: " + command)
    if code is not None and match.group(1) != code:
        raise RuntimeError("unexpected HTTP status: " + command)
    return data

def v(command, expected=b"", code=None, prompt=VCLI):
    result = run(command, prompt, expected, code)
    executed.add(command.split()[0])
    return result

def shell(command, expected=b""):
    return run(command, SHELL, expected)

saved = termios.tcgetattr(1) if os.isatty(1) else None
reaped = False
try:
    if saved:
        tty.setraw(1)
    wait_prompt(SHELL)
    shell("scripts/playground.sh status", b"verified TLS")
    shell("./vcli --version")
    profile = Path(".playground/profile")
    saas = profile.is_file() and profile.read_text().strip() == "saas"
    if saas:
        shell("# SaaS inventory: 32 tenants across three environments and regions")
        run("scripts/playground.sh shell readonly")
        v("ls kv2/saas", b"tenant-032")
        v("tree kv2/saas/tenant-001/production/eu-west", b"notifications")
        v("cd /kv2/saas/tenant-001/production/eu-west/payments")
        v("cat database/postgres/primary", b"replicas")
        v("cat messaging/kafka/producer", b"SCRAM-SHA-512")
        v("cd /")
        v("cat team/config/saas/tenant-001/production/eu-west/payments/deployment/runtime/config",
          b"feature_flags")
        audit_saas = v("getallcertexpiration kv1/saas/tenant-001", b"[EXPIRED]")
        if not all(s in audit_saas for s in (b"[OK]", b"[WARNING]", b"[CRITICAL]", b"[EXPIRED]")):
            raise RuntimeError("Missing tenant certificate states")
        audit = v("getallcertexpiration kv1/flavours/pki/valid", b"[EXPIRED]")
        if not all(s in audit for s in (b"[OK]", b"[WARNING]", b"[CRITICAL]", b"[EXPIRED]")):
            raise RuntimeError("Missing certificate audit states")
        urgent = v("getallcertexpiration kv1/flavours/pki/valid -oo", b"[CRITICAL]")
        if b"[OK]" in urgent:
            raise RuntimeError("Expiration filter did not omit healthy certificates")
        v("getcertexpiration kv1/flavours/pki/valid/expired", b"[EXPIRED]")
        v("getcertlist kv1/flavours/pki/valid/multiple", b"alt.healthy.example.test")
        v("getsan kv1/flavours/pki/valid/healthy", b"healthy.example.test")
        v("certinfo kv1/flavours/pki/valid/critical", b"Certificate:")
        v("getcert kv1/flavours/pki/valid/multiple", b"Exported")
        v("getcert kv1/flavours/pki/invalid/key-mismatch", b"certificate/private key mismatch")
        v("getallkrblist kv1/saas/tenant-001", b"tenant-001.production.eu-west")
        krb = "kv1/saas/tenant-001/production/eu-west/identity/legacy/kerberos/http"
        v("getprinc " + krb, b"HTTP/tenant-001.production.eu-west.example.test@EXAMPLE.TEST")
        v("krbinfo " + krb, b"principal:")
        v("getkrblist " + krb, b"HTTP/tenant-001.production.eu-west")
        v("getkrb " + krb, b"Exported")
        for fault in ("wrong-principal", "prefix-principal", "bad-base64", "corrupt-keytab"):
            v("getkrb kv1/flavours/kerberos/invalid/" + fault, b"invalid keytab or principal")
        v("cat kv2/flavours/lifecycle/soft-deleted", code=b"404")
        v("cat kv2/flavours/lifecycle/versioned", b"revision")
        v('put kv2/saas/tenant-001/production/eu-west/payments/database/postgres/primary {"denied":true}', code=b"403")
        v("exit", prompt=SHELL)
        shell("# Inspect exported certificates and keytab with native tools")
        shell("ls -l .playground/work/first.{crt,key} .playground/work/second.{crt,key}", b"first.crt")
        shell("klist -kt .playground/work/HTTP_tenant-001.production.eu-west.example.test@EXAMPLE.TEST.keytab",
              b"HTTP/tenant-001.production.eu-west.example.test@EXAMPLE.TEST")
        shell("# Administrator: migrate a complete 270-secret tenant, rotate and recover")
        run("scripts/playground.sh shell admin")
        v("lsrole", b"saas-tenant-032")
        v("showrole saas-tenant-001", b"saas-tenant-001")
        v("showpol saas-tenant-001", b"capabilities")
        target = "kv1/saas-tour-" + os.urandom(3).hex()
        for source, label in (("kv1", "legacy"), ("kv2", "versioned"), ("team/config", "config")):
            v("cptree " + source + "/saas/tenant-001 " + target + "/" + label, b"Copied tree")
        v("tree " + target + "/legacy", b"credentials")
        old = '{"username":"legacy_gateway","password":"FAKE-SHARED-OLD"}'
        new = '{"username":"legacy_gateway","password":"FAKE-SHARED-ROTATED"}'
        found = v("find " + target + " " + old, b"/billing/legacy/credentials/service")
        matches = set(re.findall(rb"/kv1/saas-tour-[a-f0-9]+/[a-z0-9_./-]+/credentials/service", found))
        if len(matches) != 18:
            raise RuntimeError("Expected 18 shared credentials")
        changed = v("replace " + target + " " + old + " " + new, b"/catalog/legacy/credentials/service")
        if set(re.findall(rb"/kv1/saas-tour-[a-f0-9]+/[a-z0-9_./-]+/credentials/service", changed)) != matches:
            raise RuntimeError("Bulk replacement did not report exactly 18 targets")
        leaf = "/legacy/production/eu-west/billing/legacy/credentials/service"
        v("cat " + target + leaf, b"FAKE-SHARED-ROTATED")
        v("cat kv1/saas/tenant-001/production/eu-west/billing/legacy/credentials/service", b"FAKE-SHARED-OLD")
        saved_backup = v("backup " + target, b"Vault backup created:")
        result = re.search(rb"Vault backup created: ([^\r\n]+\.tgz)", saved_backup)
        if not result:
            raise RuntimeError("Missing SaaS backup path")
        saas_archive = Path(os.fsdecode(result.group(1)))
        if saas_archive.parent != (Path.cwd() / ".playground/work").resolve():
            raise RuntimeError("Backup outside owned playground")
        v("rmtree -f " + target, b"Removed secrets beneath")
        v("cat " + target + leaf, code=b"404")
        v("restore " + str(saas_archive.relative_to(Path.cwd())), b"Vault backup restored")
        v("cat " + target + leaf, b"FAKE-SHARED-ROTATED")
        v("rmtree -f " + target, b"Removed secrets beneath")
        v("exit", prompt=SHELL)
        shell("tar -tzf " + str(saas_archive.relative_to(Path.cwd())) + " | grep -v '/
    run("scripts/playground.sh shell readonly")
    v("help", b"Available commands:")
    v("tree /kv2/enterprise", b"production")
    v("tree /team/config/enterprise", b"observability")
    v("cd /kv2/enterprise/production/payments")
    v("ls", b"database/")
    v("cat database/primary", b"FAKE-production-payments")
    v("cd ..")
    v("ls", b"identity/")
    v("cd /")
    v("cat kv2/demo/payments/database", b"FAKE-VERSION-2")
    v("cat team/config/enterprise/production/platform/feature-flags", b"replicas")
    v("tree kv1/enterprise/legacy", b"analytics")
    v('find kv1/enterprise/legacy {"username":"shared_svc","password":"FAKE-LEGACY-OLD"}',
      b"/kv1/enterprise/legacy/identity/eu/primary")

    # Certificate/keytab commands use the actual stored keys, not Vault's
    # dynamic PKI or a Kerberos KDC. Only the disposable fixtures are shown.
    v("getallcertlist kv1/enterprise/pki", b"payments.production.example.test")
    v("getallcertexpiration kv1/enterprise/pki", b"days to renew certificate")
    v("getallcertexpiration kv1/enterprise/pki -oo", b"production")
    v("getcertexpiration kv1/enterprise/pki/production/payments/ingress", b"days to renew")
    v("getcertlist kv1/enterprise/pki/staging/payments/ingress", b"payments.staging.example.test")
    v("getsan kv1/enterprise/pki/production/payments/ingress", b"api.production.example.test")
    v("certinfo kv1/enterprise/pki/production/payments/ingress", b"Certificate:")
    v("getcert kv1/enterprise/pki/production/payments/ingress", b"Exported")
    v("getallkrblist kv1/enterprise/kerberos", b"/kv1/enterprise/kerberos/production/shared/http")
    v("getkrblist kv1/enterprise/kerberos/production/shared/http", b"HTTP/service.example.test@EXAMPLE.TEST")
    v("getprinc kv1/enterprise/kerberos/staging/shared/http", b"HTTP/service.example.test@EXAMPLE.TEST")
    v("krbinfo kv1/enterprise/kerberos/development/shared/http", b"principal:")
    v("getkrb kv1/enterprise/kerberos/production/shared/http", b"Exported")
    v("renew-token 30m", b"Vault token renewed")
    v('put kv1/demo/app {"x":"DENIED"}', code=b"403")
    v("cat kv1/demo/app", b"FAKE-VCLI-PASSWORD", code=b"200")
    v("exit", prompt=SHELL)
    shell("ls -l .playground/work/enterprise_production.{crt,key} .playground/work/*.keytab",
          b"enterprise_production.crt")
    shell("openssl x509 -in .playground/work/enterprise_production.crt -noout -subject",
          b"payments.production.example.test")
    shell("klist -kt .playground/work/HTTP_service.example.test@EXAMPLE.TEST.keytab",
          b"HTTP/service.example.test@EXAMPLE.TEST")

    shell("# Administrator: rotate a copied service credential across the estate")
    run("scripts/playground.sh shell admin")
    v("lsrole", b"vcli-playground-reader")
    v("showrole vcli-playground-reader", b"vcli-playground-read")
    v("lspol", b"vcli-playground-read")
    v("showpol vcli-playground-read", b"capabilities")
    v("mount", b"mounted paths:")
    scratch = "tour-" + os.urandom(3).hex()
    work = "kv1/" + scratch
    versioned = "kv2/" + scratch + "/credential"
    v("mount " + scratch, b"mounted")
    v("umount " + scratch, code=b"204")
    v("cptree kv1/enterprise/legacy " + work)
    v("tree " + work, b"analytics")
    # Keep the rotation commands real and use cd to make the typed paths short.
    v("cd /" + work)
    v('find /' + work + ' {"username":"shared_svc","password":"FAKE-LEGACY-OLD"}',
      b"/" + work.encode() + b"/identity/eu/primary")
    rotated = v('replace /' + work + ' {"username":"shared_svc","password":"FAKE-LEGACY-OLD"} {"username":"shared_svc","password":"FAKE-ROTATED"}',
                b"/" + work.encode() + b"/identity/eu/primary")
    for leaf in (b"payments/eu/primary", b"payments/us/primary", b"catalog/eu/primary"):
        if b"/" + work.encode() + b"/" + leaf not in rotated:
            raise RuntimeError("bulk replacement missed " + str(leaf))
    v("cat payments/eu/primary", b"FAKE-ROTATED")
    v("cat payments/eu/analytics", b"FAKE-KEEP-ANALYTICS")
    v("cp payments/eu/primary /" + versioned)
    v('addsecret /' + versioned + ' {"owner":"payments","temporary":true}')
    v("cat /" + versioned, b"temporary")
    v("delsecret /" + versioned + " temporary")
    v("cat /" + versioned, b"payments")
    v("mv /" + versioned + " migrated", b"Moved version")
    v("cat migrated", b"FAKE-ROTATED")
    v("cat /" + versioned, code=b"404")
    v('put scratch {"state":"temporary"}')
    v("cat scratch", b"temporary")
    v("rm scratch")
    v("cat scratch", code=b"404")
    v("cd /")
    backup = v("backup " + work, b"Vault backup created:")
    match = re.search(rb"Vault backup created: ([^\r\n]+\.tgz)", backup)
    if not match:
        raise RuntimeError("backup did not emit an archive path")
    archive = Path(os.fsdecode(match.group(1)))
    if archive.parent != (Path.cwd() / ".playground/work").resolve():
        raise RuntimeError("backup outside the owned playground")
    v("rmtree -f " + work, b"Removed secrets beneath")
    v("cat " + work + "/migrated", code=b"404")
    # Relative file path is what an operator would type from the repo root.
    v("restore " + str(archive.relative_to(Path.cwd())), b"Vault backup restored")
    v("cat " + work + "/migrated", b"FAKE-ROTATED")
    v("tree " + work, b"analytics")
    v("rmtree -f " + work, b"Removed secrets beneath")
    v("cat " + work + "/migrated", code=b"404")
    v("cat kv1/enterprise/legacy/payments/eu/primary", b"FAKE-LEGACY-OLD")
    v("history", b"cat kv1/enterprise/legacy")
    v("verbose", b"verbose mode ON")
    v("help history")
    v("verbose", b"verbose mode OFF")
    # Exercise the diagnostic switches without emitting any credentials.
    v("enable-debug", b"debug enabled")
    v("enable-hexdump-debug", b"hexdump debug enabled")
    v("help ls")
    v("disable-hexdump-debug", b"hexdump debug disabled")
    v("disable-debug", b"debug disabled")
    v("exit", prompt=SHELL)
    shell("tar -tzf " + str(archive.relative_to(Path.cwd())), b"migrated")
    declaration = re.search(r"declare -A vcli_commands=\((.*?)\n\s*\)",
                            Path("vcli").read_text(), re.S)
    public = set(re.findall(r"\[([a-z0-9-]+)\]=", declaration.group(1)))
    if executed != public:
        raise RuntimeError(f"demo coverage missing={public-executed}, unexpected={executed-public}")
    type_line("exit 0")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if output(0.1) == b"":
            break
    else:
        raise RuntimeError("shell did not exit")
    _, status = os.waitpid(pid, 0)
    reaped = True
    if status != 0:
        raise RuntimeError("interactive shell failed")
    # Recorder v2 does not propagate the command exit status.
    if os.environ.get("VCLI_DEMO_DONE"):
        Path(os.environ["VCLI_DEMO_DONE"]).touch(exist_ok=False)
finally:
    os.close(fd)
    if not reaped:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    if saved:
        termios.tcsetattr(1, termios.TCSANOW, saved)
PY
 | wc -l", b"270")
        shell("# Complete CLI tour: remaining administration, individual writes and diagnostics")
    shell("# Discovery and inventory: three environments, three application teams")
    run("scripts/playground.sh shell readonly")
    v("help", b"Available commands:")
    v("tree /kv2/enterprise", b"production")
    v("tree /team/config/enterprise", b"observability")
    v("cd /kv2/enterprise/production/payments")
    v("ls", b"database/")
    v("cat database/primary", b"FAKE-production-payments")
    v("cd ..")
    v("ls", b"identity/")
    v("cd /")
    v("cat kv2/demo/payments/database", b"FAKE-VERSION-2")
    v("cat team/config/enterprise/production/platform/feature-flags", b"replicas")
    v("tree kv1/enterprise/legacy", b"analytics")
    v('find kv1/enterprise/legacy {"username":"shared_svc","password":"FAKE-LEGACY-OLD"}',
      b"/kv1/enterprise/legacy/identity/eu/primary")

    # Certificate/keytab commands use the actual stored keys, not Vault's
    # dynamic PKI or a Kerberos KDC. Only the disposable fixtures are shown.
    v("getallcertlist kv1/enterprise/pki", b"payments.production.example.test")
    v("getallcertexpiration kv1/enterprise/pki", b"days to renew certificate")
    v("getallcertexpiration kv1/enterprise/pki -oo", b"production")
    v("getcertexpiration kv1/enterprise/pki/production/payments/ingress", b"days to renew")
    v("getcertlist kv1/enterprise/pki/staging/payments/ingress", b"payments.staging.example.test")
    v("getsan kv1/enterprise/pki/production/payments/ingress", b"api.production.example.test")
    v("certinfo kv1/enterprise/pki/production/payments/ingress", b"Certificate:")
    v("getcert kv1/enterprise/pki/production/payments/ingress", b"Exported")
    v("getallkrblist kv1/enterprise/kerberos", b"/kv1/enterprise/kerberos/production/shared/http")
    v("getkrblist kv1/enterprise/kerberos/production/shared/http", b"HTTP/service.example.test@EXAMPLE.TEST")
    v("getprinc kv1/enterprise/kerberos/staging/shared/http", b"HTTP/service.example.test@EXAMPLE.TEST")
    v("krbinfo kv1/enterprise/kerberos/development/shared/http", b"principal:")
    v("getkrb kv1/enterprise/kerberos/production/shared/http", b"Exported")
    v("renew-token 30m", b"Vault token renewed")
    v('put kv1/demo/app {"x":"DENIED"}', code=b"403")
    v("cat kv1/demo/app", b"FAKE-VCLI-PASSWORD", code=b"200")
    v("exit", prompt=SHELL)
    shell("ls -l .playground/work/enterprise_production.{crt,key} .playground/work/*.keytab",
          b"enterprise_production.crt")
    shell("openssl x509 -in .playground/work/enterprise_production.crt -noout -subject",
          b"payments.production.example.test")
    shell("klist -kt .playground/work/HTTP_service.example.test@EXAMPLE.TEST.keytab",
          b"HTTP/service.example.test@EXAMPLE.TEST")

    shell("# Administrator: rotate a copied service credential across the estate")
    run("scripts/playground.sh shell admin")
    v("lsrole", b"vcli-playground-reader")
    v("showrole vcli-playground-reader", b"vcli-playground-read")
    v("lspol", b"vcli-playground-read")
    v("showpol vcli-playground-read", b"capabilities")
    v("mount", b"mounted paths:")
    scratch = "tour-" + os.urandom(3).hex()
    work = "kv1/" + scratch
    versioned = "kv2/" + scratch + "/credential"
    v("mount " + scratch, b"mounted")
    v("umount " + scratch, code=b"204")
    v("cptree kv1/enterprise/legacy " + work)
    v("tree " + work, b"analytics")
    # Keep the rotation commands real and use cd to make the typed paths short.
    v("cd /" + work)
    v('find /' + work + ' {"username":"shared_svc","password":"FAKE-LEGACY-OLD"}',
      b"/" + work.encode() + b"/identity/eu/primary")
    rotated = v('replace /' + work + ' {"username":"shared_svc","password":"FAKE-LEGACY-OLD"} {"username":"shared_svc","password":"FAKE-ROTATED"}',
                b"/" + work.encode() + b"/identity/eu/primary")
    for leaf in (b"payments/eu/primary", b"payments/us/primary", b"catalog/eu/primary"):
        if b"/" + work.encode() + b"/" + leaf not in rotated:
            raise RuntimeError("bulk replacement missed " + str(leaf))
    v("cat payments/eu/primary", b"FAKE-ROTATED")
    v("cat payments/eu/analytics", b"FAKE-KEEP-ANALYTICS")
    v("cp payments/eu/primary /" + versioned)
    v('addsecret /' + versioned + ' {"owner":"payments","temporary":true}')
    v("cat /" + versioned, b"temporary")
    v("delsecret /" + versioned + " temporary")
    v("cat /" + versioned, b"payments")
    v("mv /" + versioned + " migrated", b"Moved version")
    v("cat migrated", b"FAKE-ROTATED")
    v("cat /" + versioned, code=b"404")
    v('put scratch {"state":"temporary"}')
    v("cat scratch", b"temporary")
    v("rm scratch")
    v("cat scratch", code=b"404")
    v("cd /")
    backup = v("backup " + work, b"Vault backup created:")
    match = re.search(rb"Vault backup created: ([^\r\n]+\.tgz)", backup)
    if not match:
        raise RuntimeError("backup did not emit an archive path")
    archive = Path(os.fsdecode(match.group(1)))
    if archive.parent != (Path.cwd() / ".playground/work").resolve():
        raise RuntimeError("backup outside the owned playground")
    v("rmtree -f " + work, b"Removed secrets beneath")
    v("cat " + work + "/migrated", code=b"404")
    # Relative file path is what an operator would type from the repo root.
    v("restore " + str(archive.relative_to(Path.cwd())), b"Vault backup restored")
    v("cat " + work + "/migrated", b"FAKE-ROTATED")
    v("tree " + work, b"analytics")
    v("rmtree -f " + work, b"Removed secrets beneath")
    v("cat " + work + "/migrated", code=b"404")
    v("cat kv1/enterprise/legacy/payments/eu/primary", b"FAKE-LEGACY-OLD")
    v("history", b"cat kv1/enterprise/legacy")
    v("verbose", b"verbose mode ON")
    v("help history")
    v("verbose", b"verbose mode OFF")
    # Exercise the diagnostic switches without emitting any credentials.
    v("enable-debug", b"debug enabled")
    v("enable-hexdump-debug", b"hexdump debug enabled")
    v("help ls")
    v("disable-hexdump-debug", b"hexdump debug disabled")
    v("disable-debug", b"debug disabled")
    v("exit", prompt=SHELL)
    shell("tar -tzf " + str(archive.relative_to(Path.cwd())), b"migrated")
    declaration = re.search(r"declare -A vcli_commands=\((.*?)\n\s*\)",
                            Path("vcli").read_text(), re.S)
    public = set(re.findall(r"\[([a-z0-9-]+)\]=", declaration.group(1)))
    if executed != public:
        raise RuntimeError(f"demo coverage missing={public-executed}, unexpected={executed-public}")
    type_line("exit 0")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if output(0.1) == b"":
            break
    else:
        raise RuntimeError("shell did not exit")
    _, status = os.waitpid(pid, 0)
    reaped = True
    if status != 0:
        raise RuntimeError("interactive shell failed")
    # Recorder v2 does not propagate the command exit status.
    if os.environ.get("VCLI_DEMO_DONE"):
        Path(os.environ["VCLI_DEMO_DONE"]).touch(exist_ok=False)
finally:
    os.close(fd)
    if not reaped:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    if saved:
        termios.tcsetattr(1, termios.TCSANOW, saved)
PY
