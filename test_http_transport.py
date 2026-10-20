#!/usr/bin/env python3
"""No-Vault HTTP integration smoke test of the replacement transport functions."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import os
import subprocess
import tempfile

ROOT = Path(__file__).parent

class MockVault(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        pass

    def answer(self):
        assert self.headers.get('X-Vault-Token') == 'secretToken'
        path = self.path
        data = self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b''
        self.requests.append((self.command, path, data))
        if self.command == 'GET' and path == '/v1/secret/data/demo':
            body = {'data': {'data': {'x': 'value'}, 'metadata': {'version': 4}}}
        elif self.command == 'POST' and path == '/v1/secret/data/demo':
            assert json.loads(data) == {'data': {'x': 'new'}}
            body = {'data': {'version': 5}}
        elif self.command == 'LIST' and path == '/v1/secret/metadata/':
            body = {'data': {'keys': ['demo']}}
        elif self.command == 'GET' and path == '/v1/secret/demo':
            body = {'data': {'x': 'v1'}}
        elif self.command == 'GET' and path == '/v1/auth/approle/role?list=true':
            body = {'data': {'keys': ['role1']}}
        elif self.command == 'GET' and path == '/v1/sys/health':
            body = {'initialized': True, 'sealed': False}
        else:
            body = {'errors': ['not found']}
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    do_GET = answer
    do_POST = answer
    do_LIST = answer


with ThreadingHTTPServer(('127.0.0.1', 0), MockVault) as server:
    th = Thread(target=server.serve_forever, daemon=True)
    th.start()
    port = server.server_port
    with tempfile.TemporaryDirectory() as tmp:
        code = '''
set -e
source "$TRANSPORT"
urlencode(){ jq -nr --arg v "$1" '$v|@uri'; }
debug(){ :; }
TMP="$TMP_DIR"
protocol=http
vault_host=127.0.0.1
vault_port="$PORT"
vault_token=secretToken
VAULT_KV_VERSION=2
VAULT_KV_MOUNTS=secret
_cat=$(_curl secret/demo '' GET)
echo "$_cat" | jq -e '.data.x == "value"'
_ls=$(_curl secret/ '' LIST)
echo "$_ls" | jq -e '.data.keys == ["demo"]'
echo '{"x":"new"}' > "$TMP/request.json"
_put=$(_curl secret/demo "$TMP/request.json" POST)
echo "$_put" | jq -e '.data.version == 5'
[[ ! -f "$TMP/request.json" ]]
VAULT_KV_VERSION=1
_cat=$(_curl secret/demo '' GET)
echo "$_cat" | jq -e '.data.x == "v1"'
_cat=$(_curl 'auth/approle/role?list=true' '' GET)
echo "$_cat" | jq -e '.data.keys == ["role1"]'
_cat=$(_curl sys/health '' GET)
echo "$_cat" | jq -e '.initialized == true'
'''
        env = os.environ | {'TRANSPORT': str(ROOT/'transport-isolated.sh'), 'TMP_DIR': tmp, 'PORT': str(port)}
        subprocess.run(['bash', '-c', code], env=env, check=True, text=True, capture_output=True)
    server.shutdown()
    assert [(m, p) for m,p,d in MockVault.requests] == [
        ('GET','/v1/secret/data/demo'),('LIST','/v1/secret/metadata/'),
        ('POST','/v1/secret/data/demo'),('GET','/v1/secret/demo'),
        ('GET','/v1/auth/approle/role?list=true'),('GET','/v1/sys/health')]
    print('HTTP transport tests: OK (6 requests, KV v1/v2, real curl)')
