# Fase 0 — CI/CD de compatibilidad para VCLI original

**Objetivo:** ningún candidato Bash entra en la rama estable porque "pasa `bash -n`" o porque
un stub de `curl` devuelve 200. Todo cambio debe superar pruebas de contrato con
**HashiCorp Vault real** y demostrar que no rompe las operaciones heredadas.

Esta documentación se refiere al **revival de vcli (candidato v0.7)**. Las
capacidades declarativas futuras pertenecen al roadmap posterior del mismo
proyecto y no están implementadas en el candidato actual.

## Flujo de validación

```text
PR / merge a master / manual / cron semanal
               │
     ┌─────────▼──────────┐
     │ Static              │
     │ bash -n             │
     │ ShellCheck (errors) │
     │ contratos CLI       │
     │ invariantes de seg. │
     └─────────┬──────────┘
               │
     ┌─────────▼─────────────────────────────────────────┐
     │ Vault real: 1.20.4 / 1.21.4 / 2.0.4 / 2.1.1      │
     │ 4 jobs paralelos, Docker efímero, versión fija   │
     │ KV v1 + KV v2 + montaje anidado y ACL/AppRole    │
     │ suites de contrato contra API Vault              │
     │ TLS nativo: CA válida/incorrecta/desconocida     │
     └─────────┬─────────────────────────────────────────┘
               │
     ┌─────────▼─────────────────────────────────────────┐
     │ Minimal Linux: Alpine 3.22 / Debian bookworm     │
     │ CLI offline + Vault TLS 2.1.1 + PKI/REPL PTY     │
     │ KV/ACL/backup y p95 real de cat (proceso nuevo)  │
     └─────────┬─────────────────────────────────────────┘
               │
     ┌─────────▼─────────────────────────────────────────┐
     │ Playground: Vault TLS + catálogo KV/PKI/Kerberos  │
     │ up → smoke → reset → smoke → down (Bash real)     │
     └─────────┬─────────────────────────────────────────┘
               │
     ┌─────────▼──────────┐
     │ gate REQUIRED      │
     │ *todo* debe pasar  │
     └─────────┬──────────┘
               │
        Merge protegido
```

El modo dev de Vault **no es producción**: usa memoria y token raíz efímero.
La CI levanta el propio Vault con `-dev-tls`: el listener de Vault sirve
HTTPS con su CA efímera, sin proxy TLS; `VAULT_CACERT` confía en esa CA.
Se prueban CA ausente, CA incorrecta y opt-in explícito `VAULT_SKIP_VERIFY`.
Nunca se versiona material criptográfico.
Los tokens de test nunca proceden de GitHub Secrets ni de un Vault externo.

## Pruebas ejecutadas actualmente por la CI

| Grupo | Invariante o comportamiento | Nivel |
|---|---|---|
| Syntax/quality | `bash -n`, ShellCheck severidad error | PR obligatorio |
| Source guard | No `eval` en transporte, no `curl -k` fijo, sin secretos de prueba al arrancar | PR obligatorio |
| Contratos CLI | Ayuda, versión, flags inválidos, errores de configuración y salida sin credenciales | PR obligatorio |
| Linux mínimo | Alpine 3.22 (musl) y Debian bookworm-slim (glibc): **las 12 suites E2E completas (170 tests por distribución)**, con KV v1/v2, TLS/mTLS avanzado, CAS, concurrencia, fault injection, ACL, PKI/Kerberos y 38 PTY, contra Vault HTTPS 2.1.1 | PR obligatorio |
| Playground local | Docker Compose con Vault TLS real, catálogo sintético completo KV/X.509/keytab, AppRole/ACL, `up` repetible, `smoke`, `reset`, `down` y eliminación de estado; CLI Bash + oráculo API | PR obligatorio |
| Rendimiento CLI | Benchmark del arranque `--version`: 2 warmups + 12 muestras, p50/p95 informativos y presupuesto p95 conservador de 2500 ms por contenedor | PR obligatorio |
| Rendimiento con Vault | 2 warmups + 10 invocaciones nuevas `vcli -n` para `cat` KV v1 HTTPS; verificación del valor vía JSON y p50/p95, límite de bloqueo conservador p95 8000 ms | PR obligatorio |
| E2E KV v1 | `put`, `cat`, `ls`, `rm`, verificación por API | PR obligatorio |
| E2E KV v2 | escritura/lectura/listado/borrado lógico, comprobación metadatos | PR obligatorio |
| Montajes | KV v2 anidado `team/config`, rutas mixtas | PR obligatorio |
| CRUD compuesto | `addsecret`, `delsecret`, `cp`, `mv` (al menos KV v1) | PR obligatorio |
| ACL/Auth | `lspol`, `showpol`, `lsrole`, `showrole` con AppRole real | PR obligatorio |
| Seguridad | token sin permisos de escritura, token inválido, salida sin filtración | PR obligatorio |
| TLS real | Listener Vault HTTPS, CA ausente/incorrecta, CA correcta, `skip-verify` explícito | PR obligatorio |
| Privacidad | el payload secreto no debe guardarse en `.vclistory` | PR obligatorio |
| REPL PTY smoke | abrir consola, ejecutar `help`, salir con `Ctrl+D` | PR obligatorio |

La matriz de Ubuntu ejecuta las **12 suites E2E (170 tests)** en cada una de
cuatro versiones de Vault; Alpine 3.22 y Debian bookworm-slim ejecutan **las
mismas 12 suites y 170 tests** contra una instancia TLS real independiente
de Vault 2.1.1 por distribución. La cobertura incluye KV v1/v2, ACL/AppRole,
CAS, concurrencia, TLS avanzado/mTLS, fault injection, restauraciones,
PKI/Kerberos y 38 regresiones PTY en Linux. Una prueba estática adicional
(`tests.cli.test_ci_matrix`) exige paridad exacta de módulos entre el
runner Ubuntu y los clientes mínimos para impedir regresiones de cobertura.

**Límites**: no se garantiza aún equivalencia de otras versiones Vault en
Alpine/Debian, portabilidad nativa BSD/macOS, comportamiento visual Unicode
o al redimensionar terminales, ni operaciones no contempladas por los
contratos actuales. Los datos del benchmark corresponden solo al CI.

## Cobertura lograda y pendientes antes de 0.7

1. **Regresión de comandos**: ya hay contratos E2E extensos sobre KV, roles,
   TLS, PTY y operaciones destructivas. Antes de declarar estable, revisar
   comandos históricos y casos límite aún no representados: entradas vacías,
   Unicode, rutas especiales y diferentes errores de permisos.
2. **Mutaciones destructivas**: `rmtree`, `mv`, backup/restore y restricciones
   fuera del prefijo ya tienen pruebas específicas. Mantener las aserciones
   independientes de estado y verificar nuevas rutas de fallo o concurrencia.
3. **Concurrencia y consistencia**: CAS y escritores simultáneos ya están
   cubiertos; conservar los límites documentados de atomicidad y añadir
   casos de carga prolongada antes del RC.
4. **TTY real**: la suite PTY ya cubre navegación, historial, pegado y
   terminales mínimos; faltan Unicode/ancho de cursor, redimensionamiento
   y escenarios exploratorios adicionales.
5. **PKI y Kerberos**: ya existen fixtures sintéticos, comparación de claves,
   permisos 0600, exportación, casos malformados y recorridos. Revisar
   dependencias y errores de disco en la auditoría. Nunca usar secretos reales.
6. **Fault injection**: las doce suites E2E incluyen distintos errores TLS,
   HTTP, pérdida de respuesta y fallos parciales. Añadir únicamente escenarios
   ausentes y mantener la regla de no reintentar escrituras ambiguas.
7. **Compatibilidad OS**: Ubuntu con cuatro versiones de Vault reales; en
   Alpine 3.22 (musl) y Debian bookworm-slim (glibc), CLI offline y **todas
   las 12 suites E2E (170 tests)** contra Vault HTTPS 2.1.1. Continúan
   pendientes BSD/macOS, Unicode/ancho de cursor, redimensionamientos,
   pruebas largas de carga y otras versiones de Vault en distribuciones
   mínimas. Revisar periódicamente dependencias e imágenes de base.
8. **Playground local con Vault real (previo al RC)**: Docker Compose y
   Bash con `up/status/shell/batch/smoke/reset/down`, catálogo sintético
   KV v1/v2, X.509, keytabs MIT, ACL/AppRole y datos inválidos.
   La aceptación automatizada se ejecuta como job obligatorio; falta
   explorar manualmente la UX. Véase `playground/README.md`.
9. **Fase 04-Q — calidad, reutilización y eficiencia** (**pendiente**):
   auditoría estructural y de seguridad de Bash; reducción justificada de
   duplicidades, procesos externos, parsing y llamadas HTTP; benchmark
   reproducible antes/después; comentarios breves y Markdown actualizado.
   Véase [`docs/CODE-QUALITY.md`](CODE-QUALITY.md). **Bloquea el RC**.
10. **Pre-release**: ejecutar matriz completa y playground, revisar changelog,
    reproducibilidad, SHA-256, procedencia y el estado de la auditoría; publicar
    manualmente **solo** con todos los gates y la fase 04-Q completados.

## Trunk-based development (TBD), protección y releases

- Una sola trunk: `master`, sin `develop` ni ramas de release de larga duración.
- Ramas cortas y PR con squash merge; checks obligatorios en PR.
- Sin duplicar CI en cada push a feature; CI en PR, y validación post-merge en `master`.
- Conventional Commits para semver/changelog y posterior autobump desde `master`
  una vez verde el gate completo; no publicar automáticamente en esta fase.
- Settings > Rulesets: proteger `master` y requerir **Required CI gate**.
  El archivo de workflow por sí solo no activa la protección.

### Verificación real de TLS

`run.sh` ejecuta `vault server -dev -dev-tls` con certificado y CA generados
por el propio Vault. El job copia solo la CA pública al runner y obliga a
`curl`, la CLI oficial y VCLI a verificarla. No hay terminador TLS externo ni
`curl -k` fuera del caso negativo `VAULT_SKIP_VERIFY` explícito.
La suite avanzada ya comprueba casos de cadena/hostname, expiración y
mTLS. Revisar nuevas variantes durante la auditoría, sin presentar esa
cobertura como una garantía para cualquier despliegue.

- `master`: branch protegida, bloquear merges directos, require checks **Required CI gate**.
- CI de PR (matriz fija) + nightly/cron semanal para localizar regresiones del
  ecosistema; revisar periódicamente los tags del matrix, nunca depender de
  `latest` flotante dentro de una PR.
- Dependabot/Renovate para acciones e imágenes fijadas (preferir SHA digest de
  acciones/containers cuando se estabilice la matriz).
- Sin `pull_request_target`, sin secretos de producción, permisos de Actions
  `contents: read`, sin `continue-on-error` en gates.
- Para tags `v0.7.*`, el job `package_candidate` solo genera un TAR con
  suma SHA-256 tras un CI íntegramente verde. Su `environment: release`
  puede requerir aprobación manual; no publica automáticamente en GitHub Releases.
- La primera versión `0.7.0-rc1` debe esperar al cierre de las
  regresiones pendientes, la revisión 04-Q y la auditoría final.

## CLI-first: contrato antes de implementación

La especificación ejecutable está en `docs/CLI-CONTRACT.md`. Las pruebas deben
verificar entradas, salidas, exit codes, cambios de estado mediante la API
independiente de Vault, seguridad de datos y comportamiento de la TTY, **sin
inspeccionar detalles de implementación de Bash para determinar el éxito**.

- El CLI que ejecuta CI es siempre `vcli` (sin parcheadores).
- `vcli.orig.sh` conserva intacto el original histórico; `vcli` es el candidato validado.
- Cada nueva regresión necesita test que falle **antes** de cambiar el Bash.
- Fixtures temporales generadas en cada ejecución, nunca credenciales reales.
- Si la prueba implica KV v2, autenticar y consultar la API para verificar
  datos y metadatos, no solo grep sobre stdout.
- El gate global permanece obligatorio y no se silencia con `continue-on-error`.

### Linux mínimo y medición de arranque

La matriz `minimal` del workflow ejecuta **el mismo `./vcli` del PR** dentro
de `alpine:3.22` y `debian:bookworm-slim`. Instala Bash, curl, jq,
coreutils, Python y certificados CA en un contenedor limpio. El checkout está
montado **solo lectura** y las pruebas utilizan HOME/TMPDIR efímeros.

Se ejecutan los seis contratos CLI previos, cinco contratos nuevos de
entorno/terminal y `tests/cli/bench_startup.py`: dos calentamientos y doce
ejecuciones reales de `--version` sin Vault ni credenciales. Se registran la
mediana y p95; el límite **p95 ≤ 2500 ms** solo detecta regresiones extremas
o procesos bloqueados, no debe usarse para comparar máquinas/distro de modo
estricto. El proceso individual tiene un timeout de cinco segundos.

Después del arranque sin Vault, el mismo job levanta un **segundo Vault
efímero 2.1.1 con TLS nativo**, limitado al loopback del runner Linux.
El contenedor cliente utiliza `--network host` únicamente para alcanzar
`https://127.0.0.1:8200`; no monta Docker socket, y el repositorio y la CA
pública del fixture se montan en modo solo lectura. Se pasan exclusivamente
tokens de prueba generados para ese Vault temporal.

Las doce suites de `tests.integration` utilizadas en Ubuntu se ejecutan ahora
también sin exclusiones en Alpine y Debian, con un Vault HTTPS 2.1.1 efímero.
Cubren KV v1/v2, navegación, ACL, operaciones compuestas, backup/restore,
certificados, keytabs, CAS, concurrencia, TLS avanzado/mTLS, expiración de
tokens y fallos de respuesta o cortes de red. Se comprueba el efecto por la
API de Vault o sobre ficheros exportados, además del código de salida del CLI.
Los contenedores instalan OpenSSL, MIT Kerberos, ncurses y terminfo y fallan
si faltan dependencias. El certificado de Vault debe rechazarse sin CA
de confianza.
El benchmark `python3 -m tests.integration.bench_vault` ejecuta **2 calentamientos
+ 10 procesos Bash nuevos** con `cat` de un secreto de prueba en KV v1 y verifica
JSON/valor del resultado de cada lectura. Registra mediana/p95; el máximo
p95 de **8000 ms** detecta bloqueos severos, no sirve como SLA de producción
ni comparación concluyente entre distribuciones/hardware.

El `Required CI gate` exige `static + integration + minimal + playground`. En
[CI #40793127176](https://github.com/ml0renz0/vcli/actions/runs/40793127176)
pasaron 170 E2E por instancia: cuatro Vault en Ubuntu y otros dos Vault 2.1.1
desde Alpine y Debian (**1020 ejecuciones E2E**). También se ejecutan
los contratos CLI offline y un benchmark de lecturas reales. El gate
estático incorpora una prueba de paridad de listas de suites para impedir
desalineación futura. La cobertura multicontenedor no acredita compatibilidad
nativa con BSD/macOS ni garantiza ausencia de regresiones fuera de las
operaciones concretas verificadas.

### Ejecución local

```bash
python3 -m unittest -v tests.cli.test_cli_contract tests.cli.test_portability tests.cli.test_ci_matrix
python3 tests/cli/bench_startup.py
bash -n vcli scripts/playground.sh scripts/playground-seed.sh tests/playground/smoke.sh
# Requiere Docker y runtime Bash:
VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
# Solo en host Linux con Docker (modo compatibilidad desde Alpine):
VCLI_TEST_MINIMAL_IMAGE=alpine:3.22 VCLI_TEST_MINIMAL_DISTRO=alpine \
  VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
# Playground: destruye todo el catálogo de prueba; revisar el estado local.
scripts/playground.sh up
scripts/playground.sh smoke
scripts/playground.sh down
```

La matriz real y el playground ya están integrados en el gate obligatorio.
Se mantienen limitaciones documentadas de portabilidad BSD/macOS, Unicode,
terminal y operaciones no cubiertas por los contratos. El merge **no**
equivale a declarar estable vcli 0.7: quedan la fase 04-Q y la auditoría final.
