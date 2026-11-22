#!/usr/bin/env bash
# Drive a real interactive Bash terminal; the only displayed bytes come from its PTY.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -f .playground/env.sh && -f .playground/ready ]] || {
    echo 'Start playground: scripts/playground.sh up' >&2; exit 1
}
exec python3 - <<'PY'
import errno
import json
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

os.umask(0o077)

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
    deadline = time.monotonic() + 5400
    while time.monotonic() < deadline:
        chunk = output(0.1)
        if chunk == b"":
            raise RuntimeError("terminal exited before the expected prompt")
        if chunk:
            data.extend(chunk)
            # Only search the recent tail while reading large command output.
            # Decode the full response once at a real prompt.
            if re.search(pattern, ANSI.sub(b"", data[-8192:])):
                plain = ANSI.sub(b"", data)
                return plain, re.search(pattern, plain)
    raise RuntimeError("timed out waiting for the real terminal prompt")

def type_line(command):
    # Write real keystrokes, allowing the CLI's own editor to echo them.
    time.sleep(0.35)
    for key in command.encode():
        os.write(fd, bytes([key]))
        time.sleep(0.035)
    started = time.monotonic()
    os.write(fd, b"\n")
    return started

# Track every actual VCLI command typed and accepted at a prompt. Compare
# the manifest against the executable's public commands before publishing.
executed = set()
measurements = []
last_elapsed = 0.0
report_path = os.environ.get("VCLI_SAAS_METRICS")
suite_started = time.monotonic()

def write_metrics(status):
    if not report_path:
        return
    path = Path(report_path).resolve()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({
        "schema": 1, "profile": "saas",
        "commit": os.environ.get("GITHUB_SHA", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "vault_version": os.environ.get("VCLI_PLAYGROUND_VAULT_TAG", "2.1.1"),
        "catalogue_leaves": 8640,
        "status": status,
        "elapsed_seconds": round(time.monotonic() - suite_started, 6),
        "commands_covered": sorted(executed),
        "operations": measurements,
    }, indent=2) + "\n")
    tmp.replace(path)

def run(command, prompt=VCLI, expected=b"", code=None):
    global last_elapsed
    started = type_line(command)
    data, match = wait_prompt(prompt)
    last_elapsed = time.monotonic() - started
    if expected and expected not in data:
        raise RuntimeError("expected command output missing: " + command)
    if code is not None and match.group(1) != code:
        raise RuntimeError("unexpected HTTP status: " + command)
    return data

def v(command, expected=b"", code=None, prompt=VCLI, *, leaves=None,
      case=None, denied=False):
    name = command.split()[0]
    result = run(command, prompt, expected, code)
    executed.add(name)
    # Only allowlisted, non-secret metadata enters the machine-readable report.
    # A successful prompt is not a passed acceptance suite; the REST oracle
    # validates the values separately before marking the complete run passed.
    if report_path:
        measurements.append({"command": name, "case": case or name,
                             "leaves": leaves, "seconds": round(last_elapsed, 6),
                             "expected_denial": denied or code in (b"403", b"404")})
        write_metrics("running")
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
        sys.path.insert(0, str(Path.cwd() / "tests/playground"))
        from test_saas import SaaSContracts
        with SaaSContracts(v, shell, run) as acceptance:
            acceptance.exercise()
    else:
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
        # Assert the real mount inventory and distinguish populated / empty.
        # This is rendered from Vault's sys/mounts and first-level LIST calls.
        mounted = v("mount", b"mounted paths (first-level content only):", code=b"200")
        for expected_mount, expected_state in (
            (b"kv1/", b"contains entries"),
            (b"kv2/", b"contains entries"),
            (b"cubbyhole/", b"empty"),
        ):
            if not re.search(
                rb"(?m)^" + re.escape(expected_mount) +
                rb"\s+\S+\s+" + re.escape(expected_state) + rb"\s*$",
                mounted,
            ):
                raise RuntimeError(
                    "mount inventory missing or incorrect status: " +
                    expected_mount.decode()
                )
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
    write_metrics("passed")
    if os.environ.get("VCLI_DEMO_DONE"):
        Path(os.environ["VCLI_DEMO_DONE"]).touch(exist_ok=False)
except BaseException:
    write_metrics("failed")
    raise
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
