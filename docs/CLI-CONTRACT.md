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
| Administración | `lspol/showpol/lsrole/showrole` | políticas y AppRoles obtenidos de Vault real; nombres de consulta como segmento único, sin separadores, consultas ni caracteres de control |
| Privacidad | comandos con secretos | el CLI no debe registrar payloads de comandos sensibles por defecto ni pasar credenciales AppRole por argv; temporales de sesión se limpian al salir normalmente o mediante señales capturables |

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


## KV v2 — revisión semántica de la adaptación del CLI (2026-11-22)

**Regla de interfaz:** `vcli` recibe rutas lógicas (`kv2/equipo/secreto`), no debe
pedir al usuario que conozca `/data/`, `/metadata/` o `/delete/`.
La selección de motor KV v2 se hace actualmente con
`VAULT_KV_VERSION=2` y `VAULT_KV_MOUNTS`, incluido prefijo más largo
para montajes anidados. Esta configuración **no descubre** versiones de
montaje automáticamente ni valida el tipo de cada mount en tiempo de uso.

| Comando clásico | Traducción KV v2 | Semántica observable |
| --- | --- | --- |
| `cat PATH` | GET `mount/data/path` | Solo el objeto de la versión activa; datos nulos, destruidos o inválidos implican fallo, nunca éxito con metadatos en lugar del secreto |
| `ls PATH` | LIST `mount/metadata/path` | Lista nombres, **incluidos los soft-deleted** mientras Vault conserve metadatos; no prueba que puedan leerse |
| `tree PATH` | LIST `mount/metadata/path` recursivo | Estructura de nombres, no estados ni valores; mount vacío y ruta inexistente necesitan distinguirse por la información del mount |
| `put PATH JSON` | POST `mount/data/path` con `data` y CAS | Crea una versión; CAS a partir de `current_version` de metadata, 0 al crear; una carrera debe fallar sin reintentar la escritura |
| `addsecret` / `delsecret` | GET + POST con CAS | Transforman el objeto de la versión activa; versiones borradas no son objetos editables |
| `rm PATH` | POST `mount/delete/path`, versión explícita | Borrado reversible de la versión activa; si ya estaba borrada devuelve diagnóstico y error, sin reintentar |
| `rm --purge [--yes] PATH` | DELETE `mount/metadata/path` | **Irreversible**: borra todos los datos versionados y metadata de ese único path; requiere confirmación o `--yes` |
| `rmtree -f PATH` | Descubre hojas + `rm` por hoja | Soft-delete de versiones activas; hojas ya borradas se omiten sin purga; un error 403/5xx/transporte sigue siendo error |
| `rmtree [-f] --purge PATH` | LIST recursivo + GET de metadatos de todas las hojas + DELETE `mount/metadata/leaf` por hoja | Elimina permanentemente **todas** las versiones y metadatos de cada secreto KV2, incluidas hojas previamente soft-deleted. Sin `-f` pide escribir `PURGE`; con `-f` omite confirmación. Prohibido para KV1, mounts completos y rutas reservadas. Preflight sin atomicidad: errores posteriores pueden producir resultado `PARTIAL` |
| `cp`, `cptree`, `mv` | GET + POST CAS=0, y en `mv` soft-delete exacto | No atomicidad entre secretos ni copias del historial; se informa de resultados parciales; los destinos con metadata previa no se pisan |
| `backup`, `find`, `replace` | LIST de metadata y GET de datos | No deben convertir tombstones en secretos válidos. Si aparecen versiones borradas, se falla de forma explícita antes que ocultar pérdidas de datos |

**Importante:** un LIST exitoso no significa que el contenido exista o esté
activo. La API KV v2 incluye `exclude_deleted`, pero **no** filtramos
silenciosamente los nombres borrados: esa decisión exigiría una opción
explícita y tests de historial/recuperación. Véase
[API-first](API-FIRST-ACCEPTANCE.md).

### Brechas que permanecen abiertas

1. **Rutas lógicas y API tipada (candidato pendiente de CI):** los segmentos
   `data/`, `metadata/` y `delete/` se consideran nombres literales de
   carpeta en todos los comandos públicos. Las llamadas internas a Vault
   usan `-_api_path_-` para endpoints ya tipados, sin reescritura KV2.
   Esto sustituye la interpretación implícita de endpoints crudos mediante
   `cat`. La aceptación con Vault TLS y la regresión de rutas reservadas
   siguen siendo obligatorias antes de declarar el cambio validado.
2. **Operaciones históricas de KV v2:** el CLI todavía no expone comandos
   específicos y consistentes para leer una versión, ver metadatos,
   recuperar versiones borradas (`undelete`) o destruir una versión
   (`destroy`). `rm --purge` **no** sustituye estas operaciones.
3. **Autodescubrimiento:** `VAULT_KV_MOUNTS` es una declaración del cliente,
   no una consulta autoritativa del servidor. Hay que contrastar
   `sys/mounts`, opción `version`, montajes anidados y acceso ACL antes de
   decidir un cambio de configuración por defecto.
4. **Semántica de árboles:** `ls` muestra nombres con metadata; comandos
   recursivos que necesitan datos activos pueden fallar cuando encuentran
   una entrada soft-deleted. Falta una política de filtrado explícita,
   sin convertir denegaciones o errores de LIST en «vacío».
5. **CAS y concurrencia:** las comprobaciones previas mitigan las carreras
   pero no ofrecen transacciones multisecreto ni protección frente a todas
   las modificaciones que pueden llegar después del preflight.

Estos puntos **siguen abiertos** en la aceptación F0–F5. Ni añadir
comentarios ni pasar tests aislados los convierte en funcionalidades
certificadas. Contrastar siempre contra
[la API oficial KV v2](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2).

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

## Archivos locales (endurecimiento S-01 a S-04)

El cliente separa los temporales internos en un directorio privado 0700
por sesión y utiliza umask 077. Los backups y los certificados/keytabs
exportados son artefactos intencionalmente persistentes; no se borran
al cerrar el CLI. El backup se construye por completo en privado y se
publica de forma exclusiva, sin sobrescribir destinos ni seguir symlinks.
El modo de depuración solo admite un fichero regular propio 0600 con
un único enlace duro. El historial se reemplaza de forma atómica;
la lectura comprueba la identidad del fichero abierto.

**Límites de contrato:** no se garantiza eliminación tras SIGKILL o fallo
físico. El historial no sincroniza escrituras de sesiones concurrentes;
la última escritura válida prevalece. Estas garantías de rutas y permisos
se validan actualmente en Linux/GNU, no en macOS/BSD nativo.

## Cobertura por comando y catálogo empresarial

`tests/cli/test_command_coverage.py` asigna los 43 comandos públicos a sus
suites E2E existentes; es un contrato de inventario, no cobertura de ramas.
`tests/playground/test_enterprise.py` añade validación funcional a escala
de 45 hojas repartidas en KV1, KV2 y un montaje KV2 anidado. La comparación
se realiza mediante REST independiente y miembros JSON de los archivos TAR,
no por marcadores de éxito del CLI. Se ejecuta antes de cualquier grabación.
