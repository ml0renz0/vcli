# Contrato de vcli revival (candidato v0.7) — test-first

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
3. **TLS:** CA ausente, incorrecta y correcta, opt-out explícito, cadenas
   inválidas, hostname/expiración y casos de mTLS dentro de la suite avanzada.
4. **TTY:** 38 contratos PTY cubren navegación, historial, pegados,
   interrupciones y TERM mínimo. Unicode/ancho del cursor y resize pendientes.
5. **Destructivo:** backup/restore, cptree/rmtree, límites de rutas y
   resultados parciales sobre el estado independiente de Vault.
6. **Resiliencia:** errores HTTP/TLS, pérdida de respuesta y escrituras
   ambiguas, fallos parciales, concurrencia y CAS KV v2.

## Reglas de avance

- Diseñar primero el comportamiento esperado y añadir el test reproducible.
- Ejecutar rojo sobre versión antigua cuando sea posible (*red*).
- Implementar en **`vcli`**, no en un patcher (*green*).
- Refactorizar manteniendo la suite verde y midiendo rendimiento antes/después
  cuando se modifique una ruta crítica; véase [fase 04-Q](CODE-QUALITY.md).
- Mantener helpers y comentarios mínimos que expliquen invariantes,
  especialmente seguridad, control de errores y efectos secundarios.
- Conservar intacto `vcli.orig.sh` y no publicar release mientras queden
  gaps bloqueantes descritos en `docs/CI-CD.md`.

Los tests Python en `tests/cli` son **test harness**, no transformación
del código de producción: VCLI sigue siendo Bash.


## Incidencias históricas detectadas y corregidas durante la PR #25

La batería `tests/integration/test_operations.py` se añadió antes de
corregir los comandos Bash. Las primeras ejecuciones revelaron:

- `replace`: no modifica todos los secretos coincidentes.
- `cptree`: no recrea la estructura de destino dentro del mismo KV mount.
- `rmtree`: no elimina todos los secretos del subárbol.
- `backup`: el TAR no contiene todos los secretos previstos.

Esas incidencias motivaron correcciones y tests reales que forman parte
del gate actual, incluyendo el roundtrip backup/restore y la preservación
de un secreto centinela fuera del subárbol de `rmtree`. No deben
presentarse como fallos todavía abiertos sin una reproducción actual.

No usar `skip`, `xfail`, `continue-on-error` ni relajar aserciones
para ocultar regresiones. Verificar estado final mediante la API de Vault.

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

`tests/integration/test_pki_kerberos.py` contiene **33 contratos E2E**
de exportación e inspección, incluidos certificados OpenSSL, keytabs MIT,
parejas incorrectas, rutas inseguras, TOCTOU, rollback y señales.
Los comandos de listados y caducidad también se ejercitan. El playground
precarga datos sintéticos equivalentes para pruebas manuales.

**Límites:** las exportaciones múltiples no son transacciones completas;
`SIGKILL`, fallos de disco y ciertas carreras de nombres requieren
prudencia. La compatibilidad nativa BSD/macOS no está certificada.
La auditoría de [fase 04-Q](CODE-QUALITY.md) revisará duplicidades de
validación, dependencias GNU, manejo de temporales y su coste en procesos.
