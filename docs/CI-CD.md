# Fase 0 — CI/CD de compatibilidad para VCLI original

**Objetivo:** ningún parche entra en la rama estable porque "pasa `bash -n`" o porque
un stub de `curl` devuelve 200. Todo cambio debe superar pruebas de contrato con
**HashiCorp Vault real** y demostrar que no rompe las operaciones heredadas.

Este diseño pertenece a **VCLI original (0.7)**, no al futuro Forge/VCLI 2.0.

## Flujo de validación

```text
push / PR / manual / cron semanal
               │
     ┌─────────▼──────────┐
     │ Static              │
     │ bash -n             │
     │ ShellCheck (errors) │
     │ regressions/mock    │
     │ invariantes de seg. │
     └─────────┬──────────┘
               │
     ┌─────────▼─────────────────────────────────────────┐
     │ Vault real: 1.20.4 / 1.21.4 / 2.0.4 / 2.1.1      │
     │ 4 jobs paralelos, Docker efímero, versión fija   │
     │ KV v1 + KV v2 + montaje anidado y ACL/AppRole    │
     │ suites de contrato contra API Vault              │
     │ TLS válido/CA desconocida/inseguro explícito      │
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
Los tokens de test nunca proceden de GitHub Secrets ni de un Vault externo.

## Pruebas que ya se preparan en el paquete

| Grupo | Invariante o comportamiento | Nivel |
|---|---|---|
| Syntax/quality | `bash -n`, ShellCheck severidad error | PR obligatorio |
| Source guard | No `eval` en transporte, no `curl -k` fijo, sin secretos de prueba al arrancar | PR obligatorio |
| Transporte | `curl` real frente a mock HTTP, rutas /data vs /metadata y query string | PR obligatorio |
| E2E KV v1 | `put`, `cat`, `ls`, `rm`, verificación por API | PR obligatorio |
| E2E KV v2 | escritura/lectura/listado/borrado lógico, comprobación metadatos | PR obligatorio |
| Montajes | KV v2 anidado `team/config`, rutas mixtas | PR obligatorio |
| CRUD compuesto | `addsecret`, `delsecret`, `cp`, `mv` (al menos KV v1) | PR obligatorio |
| ACL/Auth | `lspol`, `showpol`, `lsrole`, `showrole` con AppRole real | PR obligatorio |
| Seguridad | token sin permisos de escritura, token inválido, salida sin filtración | PR obligatorio |
| TLS | CA desconocida debe fallar; CA correcta funciona; `skip-verify` solo explícito | PR obligatorio |
| Privacidad | el payload secreto no debe guardarse en `.vclistory` | PR obligatorio |
| REPL PTY smoke | abrir consola, ejecutar `help`, salir con `Ctrl+D` | PR obligatorio |

**No se consideran todavía cubiertas** las siguientes áreas del VCLI original:
`tree`, `find`, `replace`, `cptree`, `rmtree`, `backup`, `restore`, exportaciones
de certificados y keytabs, autocompletado, historial interactivo y parsing complejo.
Son pruebas de aceptación que **hay que implementar antes de declarar 0.7 estable**;
no se debe fingir cobertura por el mero hecho de lanzar un Vault real.

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
7. **Compatibilidad OS**: Ubuntu actual + Debian estable + Alpine/Bash moderno.
   Definir expresamente cuáles son soportadas y cuáles solo *best effort*.
8. **Pre-release**: correr matriz entera en cada RC, generar changelog y paquete
   reproducible, subir artefacto verificable (SHA-256), firmado/procedencia, y
   publicar release manualmente **solo** si todos los gates están verdes.

## Branch protection y política de releases

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

## Uso tras desarchivar el repo

Copiar estas rutas al repositorio `ml0renz0/vcli`:

```text
.github/workflows/ci.yml
docs/CI-CD.md
tests/__init__.py
tests/integration/__init__.py
tests/integration/run.sh
tests/integration/test_source_guards.py
tests/integration/test_vault.py
tests/integration/test_tls.py
tests/integration/test_repl.py
revive_vcli.py
# y las pruebas aisladas que ya acompañan el paquete
```

Luego abrir una PR en rama `revival/ci-vault`, configurar la protección de
rama y ejecutar el workflow. Los tests de integración requieren **Docker**:

```bash
python3 revive_vcli.py ./vcli   # solo si aún es v0.6.2 original
bash -n ./vcli
VCLI_TEST_VAULT_TAG=2.1.1 bash tests/integration/run.sh
```

**Estado de validación:** se han preparado scripts y pipeline. Todavía **no**
se ha ejecutado la matriz con un servidor Vault real en este entorno.
En particular, el pipeline puede descubrir fallos auténticos en VCLI que aún
no hemos corregido; hasta entonces el job rojo es un resultado válido.
