# VCLI revival: cambios directos sobre Bash

La PR conserva el histórico intacto en `vcli.orig.sh` y desarrolla
`vcli.sh` como candidato Bash revisable. Para inspeccionar exactamente las
diferencias, ejecutar `git diff --no-index -- vcli.orig.sh vcli.sh`.
Este comando devuelve código 1 cuando encuentra diferencias.

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

Una vez estabilizados los contratos y las pruebas, empaquetar `vcli.sh`
como ejecutable distribuido `vcli` sin alterar `vcli.orig.sh`.
Actualizar las instrucciones de instalación y release al cerrar la fase.
