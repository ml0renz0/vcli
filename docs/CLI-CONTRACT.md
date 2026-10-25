# Contrato CLI de VCLI revival (v0.7) — test-first

Este documento define la interfaz que **un usuario de VCLI** puede observar.
Los tests no comprueban el nombre de una función interna ni si se usó
`eval`, sino el efecto real del ejecutable.

## Superficies

| Área | Entrada | Resultado/contrato |
|---|---|---|
| Version | `--version` sin Vault ni token | exit 0, versión en stdout, sin conexión externa |
| Ayuda | `-h` sin Vault ni token | exit 0, ayuda utilizable |
| Config | `VAULT_ADDR`, `VAULT_TOKEN` | prioridad de entorno documentada, errores claros |
| Seguridad TLS | `VAULT_CACERT` | CA propia válida acepta, ausente/no válida rechaza, sin `-k` implícito |
| Modo batch | `-n` + comandos por stdin | no requiere TTY, termina al EOF, éxito=0, fallo≠0 |
| TTY | prompt, `help`, `exit`, Ctrl+D | prompt usable, sin cuelgues, historial privado |
| KV v1 | `put/cat/ls/rm` | estado observado por REST API real de Vault |
| KV v2 | `put/cat/ls/rm` | formato `data`, `metadata`, borrado lógico, versionado |
| Montajes | KV v1, v2, v2 anidado | rutas sin ambigüedad ni datos fuera del montaje |
| Escrituras compuestas | `addsecret/delsecret/cp/mv` | estado final correcto, sin corrupción |
| Autorización | solo lectura, token inválido | 403/denegación, nunca mutar ni imprimir token |
| Administración | `lspol/showpol/lsrole/showrole` | políticas y AppRoles obtenidos de Vault real |
| Privacidad | comandos con secretos | no queda plaintext en historial, logs, argv o temporales persistentes |

## Matriz de tests

1. **Sin servidor:** versión, ayuda, flags inválidos, ausencia de credenciales,
   salida y no fuga de tokens.
2. **Vault HTTPS real:** KV1/KV2, montajes anidados, roles, policies, permisos,
   no mutación al inicio, salidas y códigos de error.
3. **TLS negativo:** CA ausente/incorrecta; positivo: CA correcta;
   opt-out explícito. Pendientes: SAN incorrecta, certificados expirados, mTLS.
4. **TTY:** abrir, prompt, ayuda, salir; ampliar a Tab/flechas/Control-R.
5. **Destructivo:** backup/restore, cptree/rmtree, límites de ruta;
   antes de declarar v0.7 estable.
6. **Resiliencia:** errores 403/404/5xx, timeouts, Vault sellado,
   fallos de conexión, concurrencia/CAS.

## Reglas de avance

- Diseñar primero el comportamiento esperado y añadir el test reproducible.
- Ejecutar rojo sobre versión antigua cuando sea posible (*red*).
- Implementar en **`vcli-revival.sh`**, no en un patcher (*green*).
- Refactorizar manteniendo la suite verde.
- No mover el código probado a `vcli` ni publicar release mientras queden
  gaps bloqueantes descritos en `docs/CI-CD.md`.

Los tests Python en `tests/cli` son **test harness**, no transformación
del código de producción: VCLI sigue siendo Bash.


## Regresiones nuevas detectadas — PR #25

La batería `tests/integration/test_operations.py` se añadió antes de corregir
los comandos Bash. El primer run real encontró:

- `replace`: no modifica todos los secretos coincidentes.
- `cptree`: no recrea la estructura de destino dentro del mismo KV mount.
- `rmtree`: no elimina todos los secretos del subárbol.
- `backup`: el TAR no contiene todos los secretos previstos.

Los tests se mantienen como gate obligatorio. No usar `skip`, `xfail`,
`continue-on-error` ni relajar las aserciones. Corregir exclusivamente
`vcli-revival.sh` y volver a comprobar las cuatro versiones reales de Vault.
El contrato de `rmtree` exige además conservar un secreto centinela fuera
del subárbol solicitado.

El test `restore` forma parte del roundtrip de backup y no llegará a
validar la restauración completa mientras falle antes la comprobación
de contenido del archivo. Esto no debe contarse como cobertura verde.

## KV v2 safe moves

El comando `mv` conserva la semántica de copiar + borrar (no es atómico).
En KV v2 captura la versión exacta del origen con una lectura de datos y
metadatos, crea el destino con `cas=0`, comprueba la versión original y
después usa `POST <mount>/delete/<path>` con `{"versions":[N]}` para
borrar **solo esa versión**, nunca la versión actual que haya creado otro
proceso mientras tanto.

Se valida una segunda vez `current_version` después del borrado para
avisar del resultado parcial si se detecta un escritor concurrente.
Una carrera puede seguir ocurrir después de esa comprobación, y una pérdida
de respuesta puede hacer ambiguo el resultado: no se promete aislamiento
transaccional ni rollback.

Los tests `test_12` a `test_15` de
`tests/integration/test_cas_concurrency.py` ejercitan borrado versionado,
modificación justo después de copiar, cambio concurrente durante el borrado
y montajes KV v2 anidados.

## PKI / Kerberos revival (E2E sobre Vault TLS real)

Los comandos de exportación `getcert` y `getkrb` ahora validan **todas**
las parejas antes de exportar. `getcert` comprueba PEM X.509, clave
privada coincidente y nombres seguros. `getkrb` descodifica base64,
valida el keytab con `klist`, comprueba el principal y evita que valores
de Vault se evalúen como código shell. `getsan`, `certinfo` y
`getprinc` reutilizan validaciones de datos, sin exportar secretos.
Los archivos exportados tienen permisos 0600 por `umask 077`.

`tests/integration/test_pki_kerberos.py` contiene 12 contratos que
ejercitan los comandos reales con certificados generados por OpenSSL,
un keytab v2 de MIT Kerberos, datos inválidos, parejas no coincidentes,
ausencia de datos y nombres potencialmente peligrosos. El CI instala
`klist` (paquete `krb5-user`) solo cuando falta.

**Limitaciones:** todavía se deben auditar los comandos históricos
de listados, expedición y caducidad (por ejemplo `getcertlist`,
`getallcertlist`, `getcertexpiration`, `krbinfo`) y sus dependencias,
así como los permisos/symlinks preexistentes en el directorio destino.
La exportación múltiple no es transaccional frente a fallos de disco.
