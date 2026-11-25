# vcli — Plan de acción para incidencias de la primera aceptación manual

> **Fuente de verdad de este trabajo.** Estado inicial: 2026-11-22. Rama: `ci/baseline-vault-revival`, PR #25.
> El usuario ha reproducido los síntomas en el playground, inicialmente en `f435c7e`.
> Que una función ya exista o una suite anterior esté verde **no equivale a incidencia resuelta**.
> Este documento se actualiza por cada cambio y debe permanecer accesible al abrir otro chat.

**Política transversal obligatoria:** [API-first: oráculos semánticos para las pruebas](API-FIRST-ACCEPTANCE.md). Para los comandos Vault se exige API HTTP independiente y verificación del estado inicial/final; para el REPL se exige PTY real. Ni un mock ni un texto coincidente bastan para cerrar una incidencia.

## Principios no negociables

1. **Primero reproducir, luego corregir, luego demostrar.** Para cada defecto: evidencia de fallo, regresión que falla en la revisión previa, cambio mínimo, regresión que pasa y prueba real end-to-end cuando corresponda. Mantener el ID durante todo el ciclo.
2. **No declarar «solucionado» solo por inspección de código, mocks o CI estática.** Estados permitidos: `pendiente`, `reproducido`, `test rojo`, `implementado`, `CI verde`, `validado en playground/PTY`, `cerrado`. Anotar commit, workflow y prueba.
3. **Preservar el ejecutable Bash** y la seguridad de Vault; Python se admite para harness/tests/métricas, no como sustituto del CLI. No exponer tokens, payloads ni keytabs en logs, informes o casts.
4. **No fusionar la PR #25 ni cambiar el comportamiento de `master` sin una decisión explícita.** Hacer cambios pequeños sobre la misma PR; no abrir ramas efímeras que queden sin integrar. Mantener los datos de prueba aislados de cualquier Vault de producción.
5. **No esconder errores reales.** Separar un mount existente pero vacío (LIST 404) de una ruta inexistente, una denegación 403 y un fallo de transporte. Preservar semántica de Vault y devolver información comprensible.
6. **No duplicar suites caras ni ralentizar pruebas por grabarlas.** Mediciones y casts de la ejecución real; pausa y velocidad reducida solo en reproducción/transformación del cast.

## Alcance y backlog de incidencias (todos abiertos hasta nueva evidencia)

| ID | Prioridad | Petición/síntoma observado | Criterio de aceptación (comportamiento) | Prueba obligatoria |
|---|---|---|---|---|
| ACC-01 | P0 | `tree /` falla o no recorre mountpoints | El árbol de la raíz representa los mounts visibles sin pedir `LIST /` inexistente; conserva códigos de salida y no esconde denegaciones | E2E Vault TLS con varios mounts + salida PTY |
| ACC-02 | P0 | `tree cubbyhole` parecía tener éxito mientras `ls cubbyhole/` devolvía 404 | **API-first:** LIST vacío devuelve HTTP 404, también si el mount existe. `ls` no se modifica. `tree` debe conservar ese 404 sin dibujar un árbol ficticio. `tree /` obtiene los mounts con `sys/mounts` | Comparación directa con `LIST` real en KV1, KV2 metadata y cubbyhole, más controles positivos de LIST 200 |
| ACC-03 | P0 | TAB introduce doble espacio (`ls  cubbyhole/`) | Exactamente un separador entre comando/opciones/ruta; preservar rutas, barra final, prefijos y cursor | PTY de TAB sobre rutas, opciones y múltiples candidatos, no solo invocación aislada de `completion()` |
| ACC-04 | P1 | `help` no completa nombres de comandos | `help getallcert<TAB>` y `help tr<TAB>` completan igual que los comandos de primer nivel; ambigüedades se listan sin ejecutar nada | PTY + casos sin coincidencia/ambigüedad |
| ACC-05 | P0 | `!$` no reutiliza el último argumento del historial | Usa el último argumento del **comando anterior aceptado** (también si falló una petición) antes de guardar el nuevo comando; no consulta su propia entrada, no evalúa expresiones arbitrarias y maneja historial vacío | Secuencia PTY real `getallcertexpiration ...` → `getallkrblist !$` + historial persistido; caso con comillas/rutas |
| ACC-06 | P1 | Desaparecen colores en `getallcertexpiration` | En TTY con color: OK verde, WARNING amarillo, CRITICAL/EXPIRED rojo; sin TTY/TERM=dumb: sin ANSI; filtros `-oo/-ow/-oc` conservan significado | PTY con certificados reales en cuatro estados + comprobación de bytes ANSI y salida no TTY |
| ACC-07 | P1 | `verbose` no parece producir cambios | Al activar/desactivar se observa una diferencia útil en solicitudes posteriores: método, status, duración/diagnóstico **sin** tokens ni datos secretos; `help` no genera HTTP | E2E interactivo: `verbose` → `ls`/`cat` → `verbose`, assertions de stdout/stderr y redacción |
| ACC-08 | P1 | `mount` no permite distinguir ubicaciones vacías y ocupadas | Mostrar tipo de backend y ocupación **superficial** `con contenido`, `vacío`, `sin permisos` o `desconocido`; soportar rutas KV2 correctamente, cubbyhole y backends no KV sin leer valores | Vault TLS con mounts KV1/KV2 vacíos y poblados, cubbyhole, ACL 403 y errores de red |
| ACC-09 | P0 | `getallcertexpiration kv1/saas/ -oo` parece colgarse | Mensaje de actividad tras 5 s **solo si sigue ejecutándose**, sin ralentizar la ejecución; salida/estado final correcto y aviso cancelado al terminar/interrumpir. Investigar complejidad real del recorrido antes de atribuir ganancias | E2E árbol grande; temporización rápida/lenta; timeout, Ctrl+C, salida terminal y tiempos medidos |
| ACC-10 | P0 | `rm` sobre secretos KV2 devuelve 404 y el secreto sigue en `ls` | Diferenciar lectura de versión borrada (`data:null` + `deletion_time`) de existencia de metadatos: `rm` es **soft delete** con confirmación/resultado claro; `ls` puede listar metadata tras borrado; `rm --purge` irreversible con confirmación explícita y solo sobre ruta exacta. No borrar jamás otras versiones o secretos por accidente | Vault TLS: KV1, KV2 activo, ya soft-deleted, sin permiso, repetición, purge confirmado/cancelado, CAS y aislamiento multitenant |
| ACC-11 | P1 | Navegar y borrar palabras con teclado | Ctrl+Izquierda/Derecha, Alt+B/F, Ctrl+W, Alt+Backspace; Ctrl+Backspace si el emulador emite secuencia distinguible. Preservar líneas/cursor; implementar en Bash/TTY sin dependencia Python en runtime | PTY con secuencias CSI, SS3 y Alt, cursor en mitad de línea, terminal macOS/Linux, verificación del comando realmente ejecutado |
| ACC-12 | P0 transversal | Tests existentes no detectaron regresiones observables | Pasar de comprobar que existe código (`grep`, mocks) a aceptar flujos completos con Vault real y PTY. Los tests sintéticos siguen para casos imposibles/race, pero **nunca son la única prueba** para lo visible por usuario | Matriz de aceptación por ID, regresión negativa inicial y positiva final; fixtures y capturas/registro del resultado |
| ACC-13 | P1 transversal | Demostraciones/README deben reflejar lo que funciona | Regrabar desde tests reales sin ralentizar CI, mostrar tiempos reales actualizados automáticamente, visor Pages reproducible en móvil y README con previews/enlace válido. Evitar promocionar una demo verde si el comando mostrado falla | Comprobar `.cast` con marcadores y resultados, SVG procedentes de métricas verificadas, URL Pages + reproducción móvil |
| ACC-14 | P1 transversal | Calidad/eficiencia y código espurio | Tras resolver síntomas, eliminar duplicación y complejidad accidental, revisar HTTP/temporales y seguridad; no optimizar a ciegas ni degradar portabilidad | Diff de revisión, ShellCheck, suite completa, benchmarks A/B y justificación de cada optimización |

**Estado inicial de ACC-01 a ACC-14: `pendiente de verificación de aceptación`.**
Parte del código en la rama ya intenta resolver estas incidencias, pero los síntomas del usuario se obtuvieron sobre un commit previo y los tests actuales incluyen comprobaciones aisladas insuficientes. Se auditará cada caso, sin asumir que sigue roto ni darlo por arreglado sin reproducir en el nuevo HEAD.

## F0 — fotografía de evidencias verificadas (2026-11-24)

**Revisión auditada:** `461356acb7b5b4f33ebb4fd622793035dd8d518f` (PR #25).
Gate [CI principal #45286451690](https://github.com/ml0renz0/vcli/actions/runs/45286451690) **verde**; benchmark por etiquetas [#45286451679](https://github.com/ml0renz0/vcli/actions/runs/45286451679) verde.
La aceptación SaaS de 8.640 secretos **no corre en PR** desde el cambio release-only: su última ejecución verde anterior no demuestra una nueva aceptación sobre este SHA. Se comprobará en un tag de RC, sin crear ninguno ahora.

La columna **estado** expresa la mejor evidencia disponible **para el criterio completo**, no una afirmación de cierre. Se conservan los 14 IDs abiertos hasta completar las pruebas indicadas en el backlog y la aceptación manual.

| ID | Estado auditado | Evidencia funcional existente en la CI verde | Qué falta para cerrar |
|---|---|---|---|
| ACC-01 | CI verde | `test_operations.test_16_root_tree_lists_actual_mounts`: `sys/mounts` como oráculo independiente para `tree /` y `ls /` | Salida del árbol raíz comprobada también por PTY con distintos roles |
| ACC-02 | CI verde | `test_operations.test_17_list_api_authority_for_empty_and_populated_mounts`: LIST 404 KV1/KV2/cubbyhole y controles 200 | Aceptación interactiva de los mensajes y códigos ante montajes vacíos |
| ACC-03 | validado en playground/PTY | `test_repl.test_41_tab_path_completion_uses_one_separator`: TAB real, sin doble espacio, LIST ejecutado | Prefijos de opciones, múltiples candidatos y cursor intermedio |
| ACC-04 | validado en playground/PTY | `test_repl.test_42_help_completes_command_name_on_pty`: `help tr<TAB>` y ayuda ejecutada | Ambigüedad y ausencia de coincidencia; segundo prefijo `getallcert` |
| ACC-05 | validado en playground/PTY | `test_repl.test_43_history_bang_dollar_expands_preceding_vault_command`: `ls` → `tree !$` y contenido persistido | Secuencia original `getallcertexpiration` → `getallkrblist !$`, fallos previos y argumentos entrecomillados |
| ACC-06 | implementado | `test_pki_kerberos.test_35_expiration_reports_critical_and_omits_at_configured_threshold` comprueba filtro y estado CRITICAL en Vault real; `test_editor_quality.test_colors_and_verbose_are_enabled` es solo contrato aislado | PTY con bytes ANSI reales de OK/WARNING/CRITICAL/EXPIRED, `TERM=dumb` y todas las opciones de filtrado |
| ACC-07 | validado en playground/PTY | `test_repl.test_44_verbose_exposes_method_status_only_after_toggle`: LIST HTTPS real, método/status y canario secreto no impreso | Validar también cat, errores HTTP y redacción con token privilegiado |
| ACC-08 | CI verde | `test_operations.test_18_mount_inventory_reports_empty_populated_and_non_kv` y `test_19_mount_inventory_checks_kv2_metadata_without_client_configuration`: API Vault, KV2 no declarado, no fuga de datos | ACL restringida 403, cubbyhole desde token real y errores de transporte |
| ACC-09 | implementado | `test_mount_status.test_long_pki_scan_has_killable_delayed_notice` verifica solo presencia de `sleep/kill/wait`; tests PKI recursivos comparan valores | **Bloqueante:** PTY/tiempos reales rápido y superior a 5 s, Ctrl+C, sin aviso residual ni lentitud añadida, perfilado del árbol SaaS |
| ACC-10 | CI verde | `test_kv2_advanced.test_13` a `test_17`, `test_21` a `test_28` y `test_vault.test_04`: API real, soft-delete, purge, lectura histórica, undelete/destroy, aislamiento y ACL | Repasar explícitamente confirmación/cancelación PTY de purge, carreras y resultado parcial; **no** confundir `rm --purge` con `destroy --versions` |
| ACC-11 | validado en playground/PTY | `test_repl.test_39_ctrl_word_navigation_then_ctrl_w_deletes_only_last_word` y `test_40_alt_backspace_removes_word_without_exiting` | Alt+B/F, Ctrl+Backspace según terminal y edición en mitad de línea en macOS/Linux |
| ACC-12 | CI verde | Matriz Vault TLS, Alpine, Debian y macOS; contratos PTY en `test_repl`; oráculos independientes presentes en las pruebas anteriores | Matriz negativa histórica `antes rojo → después verde` por cada ACC y garantizar que ninguna incidencia queda cubierta únicamente por mocks |
| ACC-13 | implementado | `test_pages_contracts`, grabaciones del playground y validación de métricas `test_ci_reports`; reports/README enlazados | Comprobar **reproducción real en móvil** de Pages, contenido y artefactos correspondientes al mismo commit/tag; evidencia de tiempos publicados |
| ACC-14 | implementado | ShellCheck, pruebas de helpers `test_kv2_version_validation`, cobertura de seguridad y auditoría en `docs/CODE-QUALITY.md` | Benchmark A/B sobre rutas modificadas, inspección de redundancias y evidencia de no regresión en la aceptación manual |

**Hallazgos prioritarios:** ACC-09 y ACC-06 todavía carecen de pruebas temporales/PTY adecuadas; ACC-05 y ACC-11 tienen PTY pero no reproducen todas las variantes pedidas. No marcar `cerrado` ningún ACC por el mero verde global. Las afirmaciones antiguas «CI pendiente» de las notas históricas siguientes corresponden a commits anteriores, no al HEAD auditado.

**Aclaración de Vault:** no es posible montar un backend hijo debajo de otro mount existente; `test_30_auto_routes_similarly_named_kv1_and_kv2_mounts` usa montajes hermanos con prefijos similares. No reproducir el fixture inválido del test histórico.

## Orden de ejecución (no saltar fases sin registrar motivo)

### F0 — Congelar evidencia y comprobar la línea base
- [ ] Registrar SHA del HEAD de la PR #25, versión de Vault, flags KV1/KV2, plataforma, TERM y permisos del token de playground; no guardar credenciales.
- [ ] Reproducir uno a uno los 11 casos de interfaz y operación con comandos exactos, resultado esperado y observado, códigos de salida, stdout/stderr y tiempo. Diferenciar fallos del commit histórico `f435c7e` frente al HEAD actual.
- [ ] Levantar fixtures desechables: root, mount KV1 vacío/lleno, KV2 vacío/lleno/soft-deleted, cubbyhole, ACL denegado, secretos comunes, certificados OK/WARNING/CRITICAL/EXPIRED y Kerberos válido/inválido.
- [ ] Localizar por qué los tests previos no detectaron cada fallo: stubs poco realistas, status HTTP simulado, falta de PTY o asserts de texto sin ejecución.
- [ ] Publicar matriz con enlaces de reproducción y tests que fallen de forma fiable cuando corresponda.

### F1 — Navegación y experiencia interactiva (ACC-01, 02, 03, 04, 05, 11)
- [ ] Unificar interpretación de mount, ruta vacía, raíz y rutas inexistentes entre `ls`, `tree` y `cd`; `404` de LIST en un mount comprobado no es lo mismo que ausencia de la ruta.
- [ ] Corregir TAB sin doble espacio; `help` por prefijos; `!$` sin reordenar entradas de historial ni ejecutar contenido del historial como código.
- [ ] Corregir edición por palabra y escape sequences (CSI/SS3/Alt); PTY real al final de cada cambio. Verificar que teclas desconocidas no se insertan ni ejecutan comandos.
- [ ] Registrar fallos reproducibles antes del fix y un commit por grupo coherente.

### F2 — Observabilidad (ACC-06, 07, 08)
- [ ] Reponer ANSI únicamente para TTY y validar certificación en cuatro estados con material criptográfico real, no con strings simulados.
- [ ] Asegurar que `verbose` es perceptible en la sesión después de un HTTP y que nunca filtra credenciales ni secretos; distinguir `debug` de `verbose`.
- [ ] Corregir sondeo de mounts (KV2 `metadata/` cuando aplique, vacíos, permiso insuficiente). No confundir error con vacío.

### F3 — Ciclo de vida seguro de secretos (ACC-10)
- [ ] Documentar antes de cambiar semántica: `rm` soft delete KV2, `rm --purge` irreversible, `ls` lista claves persistentes mientras haya metadata y `cat` explica la versión borrada.
- [ ] Validar KV1/KV2 sobre Vault real y aislado; reintentos, carreras, fallos HTTP, ACL y limpieza; no probar purge sobre rutas no desechables.
- [ ] Añadir comandos de ayuda y mensajes explícitos de operación (no éxito silencioso ni 404 ambiguo).

### F4 — Escaneo recursivo y rendimiento (ACC-09)
- [ ] Medir latencia de LIST, GET y validaciones OpenSSL para descubrir el cuello de botella en árboles representativos (incluido SaaS 8.640 secretos).
- [ ] Aviso diferido a los 5 s si sigue activo, cancelable, no bloqueante y sin alterar duraciones reales; progreso medido, no estimado.
- [ ] Optimizar únicamente tras perfilado y con paridad exacta: certificados listados, filtros, errores, tokens, requests y memoria; evitar introducir concurrencia sin controlar límites/orden/ACL.
- [ ] Registrar antes/después (n, hardware, versión Vault, perfil, mediana/p95 y enlaces a run).

### F5 — Endurecer las pruebas y documentar (ACC-12, 13, 14)
- [ ] Para cada ACC cerrado, prueba negra en PTY o contra Vault TLS como mínimo; mocks solo complementarios. Probar error, resultado y **estado final de Vault**.
- [ ] Matriz obligatoria de Linux, macOS y versiones Vault soportadas. Conservar suites SaaS pesadas bajo su flag y gate correspondiente, sin repetir backup/restore en cada demo.
- [ ] Regenerar `.cast` y gráficos automáticamente con métricas originales. El README no puede incorporar JavaScript interactivo; debe mostrar preview enlazada al player móvil de Pages.
- [ ] Auditoría de seguridad, portabilidad, limpieza de código y documentación. Eliminar tests débiles o reforzarlos; no introducir deuda ni abstraer sin necesidad.

## Criterio de cierre por incidencia (Definition of Done)

**Una fila no se marca `cerrada` hasta que tenga:**
- [ ] Fallo inicial reproducido o evidencia contrastada de que solo se producía en el SHA antiguo.
- [ ] Test que verifica el **comportamiento**, no solo una cadena de código; cuando es UI, enviar teclas y leer resultado por PTY; cuando es Vault, comprobar estado y códigos HTTP reales.
- [ ] Cambio Bash mínimo y revisado, o justificación verificable de que el HEAD ya lo solucionó.
- [ ] Suite local + gates obligatorios en verde sobre el mismo SHA final (sin confundir ejecuciones anteriores ni canceladas).
- [ ] Comprobación de privacidad y seguridad para operaciones destructivas y logs.
- [ ] Evidencia trazable: commit, nombre del test, enlace a workflow/cast cuando corresponda, diferencia antes/después.
- [ ] Actualizados help/README o docs cuando cambia la semántica visible.

**Estado agregado de la PR #25:** no se declara `lista para merge` mientras quede alguna incidencia P0 sin cerrar o algún gate obligatorio rojo. No se modifica `master` para resolver esta auditoría.

## Evidencia F0 — auditoría inicial

- El fallo de demo mount se reprodujo en CI #45063467325.
- El fallo de tree se contrastó contra GET sys/mounts y LIST KV1/KV2/cubbyhole; el primer test falló por expectativas de formato y configuración KV2 incorrectas (CI #45065941076), corregidas en 8ff525d.
- Las demás incidencias siguen abiertas hasta superar sus pruebas de comportamiento.

## Registro de avance (actualizar en cada iteración)

| Fecha | Fase / ACC | HEAD | Test rojo | Commit fix | CI + E2E/PTY | Estado / bloqueo |
|---|---|---|---|---|---|---|
| 2026-11-22 | F0 / aceptación de mount + tree raíz | bf1db2fe | CI #45063467325, fallo de demo | Por validar en nuevo commit | test_operations.py + playground-demo.sh | Caso reproducido, corrección del test preparada; E2E pendiente de CI |

## Revisión KV v2 transversal — ACC-10 / F3 (2026-11-22)

- **Hallazgo de contrato:** no basta con traducir rutas KV1 a `/data/`.
  La respuesta GET KV2 puede contener `data.data=null` al consultar una
  versión ya borrada; los metadatos y nombres persisten. Ver
  [contrato de adaptación KV v2](CLI-CONTRACT.md#kv-v2--revisión-semántica-de-la-adaptación-del-cli-2026-11-22).
- **Candidato de corrección:** un GET normal con `data.data` no objeto
  falla sin emitir datos; `rm` distingue borrado previo y `rmtree`
  salta esas hojas sin purgarlas, preservando errores HTTP/ACL. No se
  declara validado hasta comprobar el estado real mediante Vault TLS.
- **Regresiones añadidas:** `tests/integration/test_kv2_advanced.py`
  valida lectura/backup de un secreto soft-deleted y `rmtree` mixto con
  metadatos conservados y un centinela fuera del árbol.
- **Pendiente de diseño:** rutas lógicas que empiezan por
  `data/`, `metadata/` o `delete/`; acceso a versiones históricas,
  undelete/destroy; descubrimiento de montajes KV2 anidados y política
  explícita para recorridos con secretos soft-deleted.
- **Estado:** `implementado; CI Vault TLS pendiente`. No cerrar ACC-10
  ni F3 todavía. No fusionar PR ni publicar RC.

## Pendientes previos que deben conservarse, sin reabrir trabajo resuelto

- Branding: mantener los dos WebP (`logo-crt.webp` y `logo-crt-256.webp`) y el GIF; no sustituir ni borrar el original de la PR.
- GitHub Pages está desplegado desde `master` y su workflow es independiente de la PR #25; ACC-13 verifica **reproducción real**, no solo el estado del deploy.
- Las ramas `ci-reports`, `docs/branding-vcli-staged` y las temporales de Pages ya se retiraron; no reconstruirlas.
- Mantener los benchmarks, gráficos de tiempos, grabaciones asciinema y pruebas multicapa; **no tomar un gráfico de duración de job por tiempo de un comando**.
- Los blueprints YAML, AppRoles declarativos y otros módulos futuros permanecen en el roadmap general y **no se mezclan con esta remediación de aceptación**.


## ACC-08 — corrección de ocupación KV2 en mounts descubiertos (2026-11-22)

- **Fallo identificado en el HEAD `02c53cc`:** `mount` construía una consulta `LIST /<mount>/` para un motor KV2 no declarado en `VAULT_KV_MOUNTS`. El servidor exige `LIST /<mount>/metadata/`; un 404 podía presentarse erróneamente como «vacío» incluso con secretos existentes.
- **Cambio candidato:** obtener la versión del motor de `GET sys/mounts` y consultar el endpoint LIST tipado sin transformación automática de rutas. KV1 y cubbyhole siguen usando el LIST normal; nunca se leen valores.
- **Nueva regresión API-first:** `tests/integration/test_operations.py::test_19_mount_inventory_checks_kv2_metadata_without_client_configuration` crea su propio motor KV2 en Vault TLS, confirma el 404 vacío por API, escribe un secreto por API y verifica que `mount` pasa a «contains entries» sin configuración especial del cliente. Contrato aislado complementario en `tests/cli/test_mount_status.py`.
- **Estado: implementado, CI Vault TLS del nuevo commit pendiente.** No se cierra ACC-08 hasta disponer de resultado del gate y evidencia de aceptación.

## KV2 — separación de rutas lógicas y endpoints (2026-11-22)

- **Defecto:** `data/`, `metadata/` y `delete/` como nombres de carpetas podían saltarse el adaptador KV2.
- **Test-first:** `test_kv2_reserved_logical_segments_always_get_typed_endpoints` y `test_18`/`test_19` en `test_kv2_advanced.py` prueban el CLI completo con API directa y montajes anidados.
- **Candidato:** los comandos públicos interpretan rutas lógicas; `_curl -_api_path_-` es privado para los endpoints de metadatos/borrado y las verificaciones de destino. Se mantiene CAS, borrado por versión y selección de montaje más específico.
- **Compatibilidad:** `cat kv2/data/...` se interpreta como carpeta literal `data/`, no como endpoint HTTP crudo.
- **Estado:** implementado; CI y aceptación Vault TLS pendientes.

## ACC-10 — `--api-path` explícito para lecturas (2026-11-22)

- Requisito aprobado: usar endpoints KV2 explícitos sin confundir las carpetas `data/`, `metadata/` y `delete/` con la API.
- Se propone `cat --api-path <mount>/data/<secret>` para GET crudo y `ls --api-path <mount>/metadata/<folder>` para LIST, exclusivamente para montajes KV2 configurados; quedan prohibidas mutaciones con este flag.
- `test_20_explicit_api_read_mode_matches_vault_and_rejects_writes` compara con API real y revisa ausencia de efectos colaterales. `test_45_explicit_api_path_read_and_list_in_real_pty` ejercita el parser real. Corrección del fixture equivocado en `test_10` y del marcador de `mount` de la demo SaaS.
- No se habilitan `put`, `rm`, `rmtree` ni endpoints administrativos bajo `--api-path`. Versiones históricas, undelete y destroy permanecen abiertos.
- Estado: implementado como candidato, CI completa y aceptación SaaS pendientes; ACC-10 sigue abierto.

## KV2 — lecturas históricas y metadatos (2026-11-23)

- **Incremento posterior a `--api-path`:** `cat --version N <ruta-lógica>` consulta GET KV2 data con el parámetro `version=N`, validando que N sea decimal positivo, ruta sin query arbitraria y montaje KV2 configurado. Presenta datos normalizados como `cat` y falla ante versiones soft-deleted/destroyed.
- **`cat --metadata <ruta-lógica>`:** GET al endpoint interno KV2 metadata/ con respuesta de historial (current_version, versions, deletion_time, destroyed) sin exponer datos del secreto. Requiere ACL de lectura de metadatos; errores 403/404 no se transforman en éxito.
- **Regresiones API-first:** `test_21_read_kv2_historical_versions_and_metadata` compara ambas variantes contra Vault TLS independiente (KV2 y montaje anidado, nombres de carpetas reservados), verifica entradas inválidas y ausencia de nuevas versiones. `test_22_deleted_and_destroyed_versions_do_not_leak_data` comprueba estados borrado/destruido. `test_46_version_history_read_uses_interactive_parser` valida PTY real.
- **Estado:** implementado como candidato, pendiente de CI completa sobre este SHA. `undelete` y `destroy` como operaciones del CLI continúan pendientes; no se fusiona la PR.

## KV2 — undelete con preflight de versiones (2026-11-23)

- Nuevo comando Bash `undelete --versions N[,N...] <ruta lógica KV2>` con montajes anidados y carpetas llamadas `data`, `metadata` o `delete`. No admite rutas API crudas, versiones duplicadas, cero o fuera del rango seguro de enteros JSON.
- Realiza GET tipado de metadata, comprueba **todas** las versiones antes de un POST al endpoint `/undelete/`, rechaza versiones activas, inexistentes o destruidas y vuelve a consultar metadata después de la operación. No imprime valores ni crea nuevas versiones.
- Contratos API-first `test_23_undelete_restores_only_selected_versions`, `test_24_undelete_invalid_batches_fail_before_modification` y `test_25_undelete_acl_blocks_mutation_even_with_metadata_read` prueban recuperación exacta, fallo sin cambios, aislamiento y permisos. Las demos estándar y SaaS incluyen el comando en el mismo Vault real, sin duplicar suites.
- Preflight y verificación posterior **no son transaccionales**: si se pierde la respuesta de POST se informa OUTCOME_UNKNOWN y no se reintenta automáticamente. Los cambios concurrentes deben investigarse desde Vault.
- Estado: implementado como candidato, pendiente de CI y aceptación SaaS. Siguiente incremento: `destroy` para versiones concretas, distinto de `rm --purge`.

## KV2 — destroy de versiones concretas (2026-11-23)

- Nuevo comando `destroy --versions N[,N...] [--yes] <ruta lógica KV2>`: POST al endpoint `mount/destroy/path`, con respaldo de metadata antes/después, ruta lógica, montajes anidados y aceptación de carpetas `data`/`metadata`/`delete`. Destruye datos de versiones individuales sin borrar el historial de metadatos.
- **Destructivo y no reversible:** preflight de existencia, no destruidas y sin repetidos. Si falta `--yes` exige terminal y escribir `DESTROY`; en batch sin terminal falla cerrado. Una operación con errores en la selección debe rechazar todo el lote.
- Pruebas E2E `test_26_destroy_selected_versions_preserves_other_history`, `test_27_destroy_refuses_invalid_batches_and_missing_confirmation` y `test_28_destroy_denied_when_only_metadata_read_is_permitted` comparan estados con la API Vault TLS. Demos estándar/SaaS incluyen operación irreversible en secretos efímeros sin cambiar datos ajenos.
- Alerta operativa: la precomprobación no es atómica con el POST; respuestas de escritura ambiguas no se reintentan y requieren comprobar metadata. Es diferente de `rm --purge`, que elimina todas las versiones y metadatos de un secreto.
- Corrección SaaS pendiente de CI: sustituido `len(metrics["commands_covered"]) == 43` por comparación exacta del conjunto de comandos registrados con el registro público real. Conserva cobertura total y exclusión de credenciales.
- Estado: implementado como candidato en PR #25; pendiente de gate Vault TLS y aceptación SaaS sobre el commit actualizado. No fusionar hasta todo verde.

## KV1/KV2 — autodescubrimiento optativo seguro (2026-11-23)

- Se implementa `VAULT_KV_VERSION=auto` para leer **una sola vez** `GET sys/mounts` al iniciar, después de establecer credenciales y validar TLS. No requiere módulos externos ni introduce tráfico en el modo manual.
- El inventario clasifica motores KV2 frente a KV1 y otros backends. Aplica el **montaje más específico**, evitando enrutar el KV1 hijo por un KV2 padre, y conserva carpetas normales llamadas `data`/`metadata`/`delete`.
- Errores 403, datos malformados, versiones KV desconocidas y la combinación `auto` + `VAULT_KV_MOUNTS` fallan explícitamente; no hay fallback a KV1 silencioso. Sin permisos de lectura de `sys/mounts`, usar la declaración manual existente.
- Regresiones API-first `test_29_auto_detects_kv1_kv2_and_nested_kv2_without_route_guessing`, `test_30_auto_routes_nested_kv1_below_kv2_without_rewriting`, `test_31_auto_denied_inventory_fails_closed_and_manual_still_works` y `test_32_auto_rejects_conflicting_or_invalid_configuration`. La fotografía del inventario no se actualiza automáticamente después de `mount`/`umount`: reiniciar la sesión hasta añadir una orden de refresco controlada.
- **Estado:** implementado como candidato; pendiente de CI Vault TLS completa. No cambiar el modo predeterminado ni fusionar la PR hasta disponer de pruebas de aceptación.

## CI/CD — aceptación SaaS trasladada al gate de release (2026-11-23)

- CI principal de PR y master permanece obligatorio: matriz Vault TLS, playground real, Alpine, Debian y macOS; `tests.cli.test_saas_manifest` verifica el catálogo sintético sin poblar 8.640 secretos.
- `.github/workflows/playground-saas.yml` deja de escuchar `push`/`pull_request`, y mantiene `workflow_dispatch` para investigación manual, además de `workflow_call`.
- En tags `v0.7.*`, `saas_release` se ejecuta **después** del `Required CI gate`, con exactamente la misma aceptación y grabación real. `package_candidate` depende de **ambos** jobs; no se produce artefacto si falla SaaS.
- `tests.cli.test_saas_release_gate` comprueba disparadores, dependencia obligatoria, conservación de controles de credenciales y retención de artefactos, y el smoke Vault real. El contrato se ejecuta en Linux y macOS.
- **Publicación separada:** los publicadores consumen la grabación y las métricas SaaS desde el run `ci.yml` que invocó `workflow_call` en el tag, o desde una ejecución SaaS manual. Verifican el SHA contra tags `v0.7.*` y mantienen el filtro de repositorio de origen; no publican artefactos de PR. Los artefactos temporales pueden expirar antes de la siguiente release y se falla sin inventar métricas ni capturas.
- Estado: candidato pendiente de nueva CI; PR #25 sigue abierta, no hay tag ni release publicados.

## ACC-09 — evidencia PTY temporal y siguiente perfilado (2026-11-26)

- **Revisión validada:** `cf2b6ff74854c3ef8ffb8adc5e5fd47dc7bc82e7`; [CI #45484512521](https://github.com/ml0renz0/vcli/actions/runs/45484512521) SUCCESS, gate obligatorio verde, cuatro versiones Vault TLS, Alpine, Debian, macOS y playground.
- `tests/integration/test_repl.py::test_48_acc09_certificate_scan_notice_with_real_vault_and_timing`: certificado X.509 real y fixtures escritos/verificados mediante API independiente; el scan rápido termina antes de 4,5 s sin aviso; se inyectan 6 s de espera antes del LIST real para comprobar que el aviso aparece una sola vez, dentro de la ventana de tolerancia de 4,3–7,5 s, antes del resultado final CRITICAL.
- `test_49_acc09_interrupt_cancels_delayed_notice`: sincroniza Ctrl+C con la entrada al LIST lento (12 s de espera inyectada), comprueba recuperación del prompt y ejecución de help, y observa otros 5,4 s sin avisos huérfanos. El commit validado estabiliza el redibujado de historial en macOS sin relajar las aserciones.
- **Estado:** validado en PTY para el aviso/cancelación. ACC-09 permanece abierto: los retardos inducidos comprueban comportamiento temporal, no throughput real; no hay prueba de ausencia de regresión de rendimiento a escala ni perfilado LIST/GET/OpenSSL publicado sobre este SHA. El job de 8.640 secretos y el benchmark A/B manual se omitieron por diseño en esta CI; el workflow de etiquetas [#45484512615](https://github.com/ml0renz0/vcli/actions/runs/45484512615) terminó verde, lo que no implica una medición nueva de PKI.
- **Siguiente iteración:** perfilado opt-in de `getallcertexpiration` sobre fixtures de tamaños crecientes, contando LIST/GET y validaciones OpenSSL y midiendo tiempo total sin registrar secretos. Comparar referencia/candidato sobre el mismo Vault antes de atribuir mejoras. Después ACC-06: cuatro estados X.509, filtros y bytes ANSI reales por PTY y modo no TTY/TERM=dumb.
- No se ha cerrado ACC-09 ni fusionado la PR; no se genera tag ni release.

