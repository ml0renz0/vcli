# API-first: política de pruebas de aceptación para vcli

**Aplicación obligatoria en la PR #25 y en las features futuras.** Los tests no determinan el comportamiento esperado observando el código bajo prueba. El contrato proviene de la documentación semántica oficial de HashiCorp Vault, de la versión concreta de Vault/motor y de peticiones HTTP directas a un Vault TLS desechable.

## Secuencia de aceptación

1. Documentar el contrato de la operación: precondiciones, tipo y versión del backend, token y ACL, método y endpoint HTTP, formato de petición/respuesta, códigos permitidos y efectos esperados. En operaciones interactivas, documentar semántica de teclas y terminal.
2. Crear fixtures **independientemente del CLI** mediante la API de Vault y verificar su estado inicial por consultas API: mount, claves, versiones, permisos, certificados y configuración.
3. Ejecutar el mismo endpoint **directamente por API**. Registrar en aserciones HTTP status, JSON y campos de interés; nunca usar `vcli` para construir el oráculo. No registrar tokens, valores secretos ni keytabs sin sanear.
4. Invocar el comando real desde el parser Bash completo. Para TAB/historial/cursores, enviar las secuencias de teclado a una terminal PTY real.
5. Comparar la salida del CLI con el contrato semántico y la API, no con un string fijo arbitrario: status HTTP cuando se muestra, exit code, estructura, campos, orden si es parte del contrato, y diagnóstico útil.
6. Cuando hay mutación, volver a consultar Vault directamente para comprobar **estado final**: valores, metadata, versiones, borrados, restauración y ausencia de efectos colaterales.
7. Ejecutar escenarios positivos y negativos: vacío/poblado, inexistente, 403, backend equivocado, problemas de red/TLS, token sin permisos, versiones históricas, operaciones repetidas y carreras. La regresión debe detectar la versión incorrecta cuando sea reproducible.
8. Guardar el ID ACC, commit de corrección, prueba exacta, CI de ese SHA y, cuando aplique, artefacto de demo; no declarar un bug cerrado por mocks, grep o un test verde de otro commit.

## Matriz de oráculos

| Área | Oráculo independiente | Aserciones mínimas |
|---|---|---|
| `ls /`, `tree /` | `GET /v1/sys/mounts` autorizado | Conjunto **exacto** de mounts visibles; no hay endpoint `LIST /` equivalente |
| `ls`/`tree` en KV1 | `LIST /v1/:mount/:path` | Códigos de respuesta y `data.keys` para empty, populated y nonexistent |
| `ls`/`tree` en KV2 | `LIST /v1/:mount/metadata/:path` | Verificar `metadata/`, no `data/`, y el tipo real de mount |
| `ls`/`tree` en cubbyhole | `LIST /v1/cubbyhole/:path` | Estado y claves con el mismo token; origen API, no salida del comando |
| `mount`/`umount` | `sys/mounts` y sus opciones; `LIST` con ACL apropiada | Existencia/tipo/versión, ocupación, permisos, persistencia del cambio |
| `cat`, `put`, CAS | KV1 read/write; KV2 `data/`, `metadata/` y control CAS | Valor exacto, versión y estado posterior; errores reales |
| `rm`/purga | KV1 delete; KV2 soft delete `data/`, version delete/destroy y `metadata/` | Distinguir dato borrado de metadatos existentes, verificar versiones no afectadas, confirmar irreversibilidad |
| Certificados / Kerberos | API de secretos y validación real por OpenSSL / `klist` | Validez, SAN, fechas, keytabs buenos/malos, estados sin inventar |
| `verbose`, progreso, benchmarks | HTTP real + reloj monotónico y captura de salida | Diagnósticos reales, sin credenciales; alertas temporizadas sin degradar tiempos |
| TAB, `help`, `!$`, historial, cursor, colores | **PTY real**, no HTTP | Buffer y cursor, secuencias emitidas, historial, bytes ANSI, resultado ejecutado; si llama Vault, además API |

## Dos advertencias de semántica

- **Un HTTP 404 no demuestra «mount vacío».** Según la API Vault, un `LIST` sin resultados devuelve 404. El 404 también puede indicar que la ruta no existe o que no se permite descubrirla. Confirmar existencia del mount con `sys/mounts` usando un token adecuado y controlar permisos antes de interpretar un 404. No convertir artificialmente un 404 en 200 para que un test pase.
- **KV2 separa contenido y metadatos.** Un soft-delete puede dejar `data: null` y conservar información sobre versiones. Un `LIST` que muestre el nombre del secreto no contradice necesariamente un `GET` a la versión actual que ya fue borrada. Validar mediante `metadata/` y endpoints correspondientes.

## Fuentes oficiales

- [Vault HTTP API — status y semántica de LIST](https://developer.hashicorp.com/vault/api-docs)
- [KV v1 API](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v1)
- [KV v2 API](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2)
- [API /sys/mounts](https://developer.hashicorp.com/vault/api-docs/system/mounts)

## Definition of Done ampliada

Una prueba **aislada** de un helper Bash con `_curl` simulado es útil para ramas y edge cases, pero no puede cerrar por sí sola una incidencia que depende de Vault. Para cerrar, al menos una prueba de integración debe usar el CLI real sobre Vault TLS real y un oráculo API directo; si el problema afecta al REPL, también PTY. Anotar excepciones justificadas si el subsistema no tiene API (p. ej. editor), sin falsear cobertura.
