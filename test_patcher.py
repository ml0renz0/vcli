#!/usr/bin/env python3
"""Fixture test for textual patch anchors; actual original comes from GitHub."""
import ast
from pathlib import Path
import subprocess
import tempfile
from revive_vcli import revive

root = Path(__file__).parent
source = root.joinpath('revive_vcli.py').read_text()
module = ast.parse(source)
consts = {}
for node in ast.walk(module):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in ('old_colors', 'probe'):
            if isinstance(node.value, ast.Constant):
                consts[node.targets[0].id] = node.value.value

fixture = '''#!/usr/bin/env bash
declare VERSION="0.6.2"
declare DEPENDENCIES="curl jq openssl klist bc"
declare DEBUGFILE="/tmp/vcli.debug"
declare TMP="/tmp"
'''
fixture += consts['old_colors'] + '\n'
fixture += '''
function new_version(){
    :
}
function _run(){
    :
}
# simplified curl function
function _curl(){
    :
}
function _lspol(){
    :
}
function _showpol(){
    :
}
function save_history(){
    : > "$VCLISTORY"
}
function add_cmd(){
        is_cmd "$cmd" || return 1
}
function _mount(){
        echo "{ \\"type\\": \\"generic\\", \\"description\\": \\"$url secret backend\\" }" > "$data"
}
function _put(){
    echo "$2" | envsubst > "$data"
}
function main(){
'''
fixture += '''    # Use VAULT_ADDR as the primary default as it is the most common setting.
    # If VAULT_ADDR is set it will overwrite VAULT_HOST and VAULT_PORT
    if [[ $VAULT_ADDR ]] && [[ $VAULT_ADDR =~ ^http[s]*://[a-zA-Z0-9.-]+:[0-9]+ ]]; then
        protocol=${VAULT_ADDR%%:*}
        local host_port=${VAULT_ADDR#*//}
        VAULT_HOST=${host_port%:*}
        VAULT_PORT=${host_port#*:}
    fi

    # Use environment variables VAULT_HOST VAULT_TOKEN and VAULT_PORT if defined
    vault_port=${VAULT_PORT:-"$vault_port"}
    vault_port=${vault_port:-"8200"} # Define Vault default port if not specified
    vault_host=${VAULT_HOST:-$vault_host}
    vault_token=${VAULT_TOKEN:-$vault_token}
'''
fixture += consts['probe'] + '\n}\n'
updated = revive(fixture)
assert 'eval "$*"' not in updated
assert 'generic' not in updated
assert 'VAULT_KV_VERSION' in updated
assert 'VAULT_SKIP_VERIFY' in updated
assert '/sys/policies/acl' in updated
with tempfile.TemporaryDirectory() as temp:
    dest = Path(temp) / 'vcli'
    dest.write_text(updated)
    subprocess.run(['bash', '-n', str(dest)], check=True)
print('Patch transformation test: OK (synthetic 0.6.2 fixture)')
