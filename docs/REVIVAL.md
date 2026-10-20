# VCLI revival: cambios directos sobre Bash

La PR mantiene el fichero original `vcli` y añade
`vcli-revival.sh`: una copia modificada y revisable del shell original.

No existe un generador de parches Python. CI valida **el fichero Bash que
realmente se entregará**, con los contratos de `docs/CLI-CONTRACT.md`.

## Objetivos

- Vault actual con KV v1 y KV v2 explícitamente configurado.
- TLS verificado por defecto. VAULT_CACERT permite usar una CA privada.
- El cliente no hace escrituras al arrancar.
- Tokens restringidos y errores HTTP sin filtración de secretos.
- Mantener comandos y REPL históricos, documentando compatibilidades.

## Contratos y pruebas

`python3 -m unittest -v tests.cli.test_cli_contract` cubre la interfaz
independiente de Vault. La matriz de integración levanta Vault con TLS
nativo y verifica el estado a través de su API.

`docs/CI-CD.md` describe los gates y pruebas pendientes. No utilizar un
servidor productivo ni credenciales reales en la suite.

## Plan de integración

Una vez que las pruebas y el candidato estén estabilizados, sustituir
`vcli` por el código probado, eliminar `vcli-revival.sh` y actualizar
las instrucciones de instalación y release.
