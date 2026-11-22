# vcli — Plan de acción para incidencias de la primera aceptación manual

> **Fuente de verdad de este trabajo.** Estado inicial: 2026-11-22. Rama: `ci/baseline-vault-revival`, PR #25.
> El usuario ha reproducido los síntomas en el playground, inicialmente en `f435c7e`.
> Que una función ya exista o una suite anterior esté verde **no equivale a incidencia resuelta**.
> Este documento se actualiza por cada cambio y debe permanecer accesible al abrir otro chat.

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
| ACC-02 | P0 | `tree cubbyhole` se limita a imprimir `cubbyhole/`; `ls cubbyhole/` devuelve 404 | Un mount existente y vacío se reconoce como tal; `tree` indica `(vacío)` y `ls` ofrece semántica coherente (sin elementos, sin falso «no existe»). Una ruta realmente inexistente debe seguir fallando | E2E mounts vacíos (KV1, KV2 y cubbyhole), rutas ausentes, 403 |
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

## Evidencia F0 — auditoría inicial del HEAD de la PR

- **Baseline:** `bf1db2fe69c2d5b8e30b51e15e0f99e2d0792ee9` (22-11-2026), contra Vault TLS 2.1.1 y runners Linux/macOS, según CI.
- **Discrepancia de versión aclarada:** el usuario ejecutaba `f435c7e`, versión en la que `_tree /` hacía `LIST /` y por tanto **no** podía enumerar mounts. El cambio funcional llegó en [`5c64822b`](https://github.com/ml0renz0/vcli/commit/5c64822b) (posterior a ese SHA), que utiliza `_ls /` respaldado por `/sys/mounts`. En [CI #45063903358](https://github.com/ml0renz0/vcli/actions/runs/45063903358) de Vault TLS 2.1.1, `test_16_root_tree_lists_actual_mounts` y `test_17_tree_empty_mount_is_not_a_missing_path` terminaron en `ok`. **Ese resultado no valida el checkout local antiguo del usuario.**
- **Test reforzado (pendiente de nueva CI):** `test_16` compara el conjunto exacto y cardinalidad de la salida de `tree /` con `sys/mounts` consultado independientemente, además de `tree` sin argumentos y `ls /`; `test_17` exige salida de `tree cubbyhole` con `(empty)`.
- **Reproducido, fallo de prueba/demo:** [CI #45063467325](https://github.com/ml0renz0/vcli/actions/runs/45063467325), job `Real Vault TLS playground acceptance` rojo. El comando `mount` imprimía estados de ocupación válidos, pero `scripts/playground-demo.sh` exigía la cadena obsoleta `mounted paths:`; recibió `mounted paths (first-level content only):` y lanzó `RuntimeError: expected command output missing: mount`.
- **Impacto:** la demo/asciinema quedó incompleta aunque el CLI respondía. Los tests aislados de `tests/cli/test_mount_status.py` no verificaban el texto que esperaba la demo real. Añadida validación de inventario real en el controlador de la demo; E2E de raíz, mount vacío y ocupación en `tests/integration/test_operations.py`.
- **Código inspeccionado (sin cerrar incidencias):** `test_editor_quality.py` ejerce helpers en Bash aislado, no la secuencia de teclas completa; `test_mount_status.py` simula el transporte. `test_repl.py` sí dispone de PTY y debe ampliarse con casos exactos de TAB, `help` y `!# vcli — Plan de acción para incidencias de la primera aceptación manual

> **Fuente de verdad de este trabajo.** Estado inicial: 2026-11-22. Rama: `ci/baseline-vault-revival`, PR #25.
> El usuario ha reproducido los síntomas en el playground, inicialmente en `f435c7e`.
> Que una función ya exista o una suite anterior esté verde **no equivale a incidencia resuelta**.
> Este documento se actualiza por cada cambio y debe permanecer accesible al abrir otro chat.

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
| ACC-02 | P0 | `tree cubbyhole` se limita a imprimir `cubbyhole/`; `ls cubbyhole/` devuelve 404 | Un mount existente y vacío se reconoce como tal; `tree` indica `(vacío)` y `ls` ofrece semántica coherente (sin elementos, sin falso «no existe»). Una ruta realmente inexistente debe seguir fallando | E2E mounts vacíos (KV1, KV2 y cubbyhole), rutas ausentes, 403 |
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

. `test_vault.py` valida borrado KV2 recién escrito, pero no repetición sobre versiones ya soft-deleted.
- **Pendiente:** ejecutar la nueva CI y validar los 11 escenarios del usuario bajo HEAD con sus fixtures reales antes de marcar `cerrado`.

## Registro de avance (actualizar en cada iteración)

| Fecha | Fase / ACC | HEAD | Test rojo | Commit fix | CI + E2E/PTY | Estado / bloqueo |
|---|---|---|---|---|---|---|
| 2026-11-22 | F0 / aceptación de mount + tree raíz | bf1db2fe | CI #45063467325, fallo de demo | Por validar en nuevo commit | test_operations.py + playground-demo.sh | Caso reproducido, corrección del test preparada; E2E pendiente de CI |

## Pendientes previos que deben conservarse, sin reabrir trabajo resuelto

- Branding: mantener los dos WebP (`logo-crt.webp` y `logo-crt-256.webp`) y el GIF; no sustituir ni borrar el original de la PR.
- GitHub Pages está desplegado desde `master` y su workflow es independiente de la PR #25; ACC-13 verifica **reproducción real**, no solo el estado del deploy.
- Las ramas `ci-reports`, `docs/branding-vcli-staged` y las temporales de Pages ya se retiraron; no reconstruirlas.
- Mantener los benchmarks, gráficos de tiempos, grabaciones asciinema y pruebas multicapa; **no tomar un gráfico de duración de job por tiempo de un comando**.
- Los blueprints YAML, AppRoles declarativos y otros módulos futuros permanecen en el roadmap general y **no se mezclan con esta remediación de aceptación**.
