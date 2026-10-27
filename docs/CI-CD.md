# Fase 0 — CI/CD de compatibilidad para VCLI original

**Objetivo:** ningún candidato Bash entra en la rama estable porque "pasa `bash -n`" o porque
un stub de `curl` devuelve 200. Todo cambio debe superar pruebas de contrato con
**HashiCorp Vault real** y demostrar que no rompe las operaciones heredadas.

Este diseño pertenece a **VCLI original (0.7)**, no al futuro Forge/VCLI 2.0.

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
     │ CLI offline + Vault real TLS 2.1.1, API oracle   │
     │ KV/ACL/backup y p95 real de cat (proceso nuevo)  │
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
| Linux mínimo | Alpine 3.22 (musl) y Debian bookworm-slim (glibc): CLI offline y suite KV v1/v2, ACL, árbol, backup/restore y comandos compuestos contra Vault HTTPS efímero 2.1.1 | PR obligatorio |
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

La matriz completa en Ubuntu ya cubre comandos de árbol, edición/CRUD,
backup/restore, exportaciones PKI/Kerberos y 38 regresiones PTY. La nueva matriz
de Linux mínimo selecciona **solo** `test_vault` y `test_operations`: ejercicios
de KV v1/v2, ACL y comandos compuestos contra un Vault independiente con TLS real,
más el benchmark de lectura. **No** equivale a ejecutar el conjunto completo
de fault injection, PKI/Kerberos ni REPL en Alpine y Debian; tampoco certifica
todas las combinaciones de comandos/caracteres especiales de la CLI.

## Requisitos adicionales antes de 0.7

1. **Regresión completa de comandos**: escenario aislado de cada comando, casos vacíos,
   caracteres especiales, rutas relativas, montajes mixtos y errores 403/404/5xx.
2. **Mutaciones destructivas**: `rmtree`, `mv`, backup/restore en un mount único,
   con asserts de estado y sin tocar rutas externas al prefijo de prueba. Exigir
   respaldo de versionado al aplicar sobre KV v2 (no confundir soft-delete con purge).
3. **Concurrencia y consistencia**: dos escritores simultáneos; detectar pérdida de
   actualización en `addsecret`/`delsecret`; implementar CAS v2 antes de exigir éxito.
4. **TTY real**: con `pexpect` en PTY, `Tab`, flechas, `Ctrl+R`, Home/End,
   `Ctrl+D` y comandos pegados con rapidez. Separar de batch para evitar flakiness.
5. **PKI y Kerberos**: fixtures generadas durante el test (`openssl`, `ktutil` o
   minicliente Kerberos de pruebas), exportación, permisos 0600 y comparación de
   claves públicas. Nunca certificados o keytabs reales.
6. **Fault injection**: Vault sellado, reconexión, timeouts, DNS/TLS, servidor
   caído, respuestas JSON malformadas, respuestas HTTP 429 y 500; el CLI debe
   fallar cerrado, sin borrar datos ni registrar tokens.
7. **Compatibilidad OS**: Ubuntu con matriz integral de cuatro Vault; en
   Alpine 3.22 (musl) y Debian bookworm-slim (glibc), CLI offline + subconjunto
   E2E real sobre Vault HTTPS 2.1.1. PKI/Kerberos, fault injection completo y
   PTY quedan por certificar en esas distribuciones. BSD/macOS no verificado.
   Revisar periódicamente las dependencias e imágenes de base.
8. **Pre-release**: correr matriz entera en cada RC, generar changelog y paquete
   reproducible, subir artefacto verificable (SHA-256), firmado/procedencia, y
   publicar release manualmente **solo** si todos los gates están verdes.

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
Los casos de hostname erróneo, expiración de certificado y mTLS quedan pendientes.

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
- La primera versión `0.7.0-rc1` debería generarse una vez implementados
  los bloques de regresión pendientes.

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

Las suites `tests.integration.test_vault` y
`tests.integration.test_operations` validan comandos contra la API independiente
de Vault, incluyendo KV v1/v2, navegación, ACL, operaciones compuestas,
backup y restore. Se comprueba que el certificado no sea de confianza sin CA.
El benchmark `python3 -m tests.integration.bench_vault` ejecuta **2 calentamientos
+ 10 procesos Bash nuevos** con `cat` de un secreto de prueba en KV v1 y verifica
JSON/valor del resultado de cada lectura. Registra mediana/p95; el máximo
p95 de **8000 ms** detecta bloqueos severos, no sirve como SLA de producción
ni comparación concluyente entre distribuciones/hardware.

El `Required CI gate` exige `static + integration + minimal`. La cobertura
de Alpine/Debian es **un subconjunto E2E**, no sustituye a la matriz integral
de cuatro Vault HTTPS en Ubuntu. El REPL tiene tres regresiones PTY
con `TERM=dumb`, `TERM` ausente y terminfo desconocido, todavía ejecutadas
solo en Ubuntu. PKI/Kerberos, PTY completo, señales y las operaciones de
seguridad avanzadas siguen sin certificarse E2E en Alpine o BSD/macOS.

### Ejecución local

```bash
python3 -m unittest -v tests.cli.test_cli_contract tests.cli.test_portability
python3 tests/cli/bench_startup.py
bash -n vcli
# Requiere Docker y runtime Bash:
VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
# Solo en host Linux con Docker (modo compatibilidad desde Alpine):
VCLI_TEST_MINIMAL_IMAGE=alpine:3.22 VCLI_TEST_MINIMAL_DISTRO=alpine \
  VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
```

Los primeros runs de la PR detectaron errores de exit code, AppRole y REPL.
Tras corregir el candidato Bash y la fixture Docker, la matriz real pasó
contra Vault 1.20.4, 1.21.4, 2.0.4 y 2.1.1. Es un baseline funcional;
la suite NO cubre aún todos los comandos heredados. El merge no equivale
a declarar estable VCLI 0.7.
