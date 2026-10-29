# vcli — Bash CLI para HashiCorp Vault

**Revival en desarrollo (candidato v0.7, todavía no publicado como estable).**
CLI autocontenido en **Bash** para consultar, gestionar y explorar secretos de
HashiCorp Vault. El ejecutable modernizado es [`vcli`](vcli) y el
original histórico, conservado sin modificaciones, es
[`vcli.orig.sh`](vcli.orig.sh).

El objetivo es modernizar el CLI original, conservar las funciones útiles,
mejorar su seguridad y eficiencia y validar el comportamiento contra **Vault
real**; no sustituirlo por una implementación Python o Go.

## Funcionalidad del candidato

- Consola interactiva y modo batch (`./vcli -n`) con ayuda, historial y
  navegación/autocompletado; pruebas PTY con terminales básicos.
- KV v1 y KV v2 configurables, montajes anidados, lectura/escritura,
  listados, recorridos, operaciones compuestas y backup/restore.
- Autenticación mediante token o AppRole, consulta de políticas y roles,
  acceso de solo lectura y tratamiento explícito de permisos insuficientes.
- HTTPS verificado, CA personalizada, opciones de mTLS y manejo de fallos
  HTTP sin considerar seguras las escrituras ambiguas.
- Certificados X.509, claves PEM y keytabs MIT Kerberos guardados como
  secretos KV: inspección, listados y exportación protegida contra nombres
  inseguros, destinos existentes y archivos parcialmente escritos.

**Compatibilidad verificada en CI:** Vault 1.20.4, 1.21.4, 2.0.4 y 2.1.1
desde Ubuntu; todas las suites E2E también se ejecutan en Alpine 3.22 y
Debian bookworm-slim con Vault 2.1.1. Esto **no** certifica otros entornos ni
todos los casos de uso. La PR de desarrollo sigue pendiente de auditoría final.

## Probarlo con un Vault real, sin tocar producción

El [playground local](playground/README.md) inicia un Vault efímero con TLS
nativo y datos **sintéticos**: KV v1/v2, JSON anidado, versiones, certificados
y claves, keytabs, ejemplos inválidos, políticas y AppRoles.

Requiere Linux, Docker Compose v2, Bash, `curl`, `jq`, OpenSSL, MIT Kerberos
(`klist`) y herramientas GNU indicadas en la guía.

```bash
scripts/playground.sh up
scripts/playground.sh smoke
scripts/playground.sh shell

# O usar directamente el mismo ejecutable Bash:
source .playground/env.sh
./vcli
```

`up` repetido conserva los experimentos; `reset` los elimina y siembra
el catálogo de nuevo; `down` destruye el contenedor y las credenciales
locales. El playground se limita a loopback, no a un Vault externo.

Para batch:

```bash
printf 'cat kv1/demo/app\n' | scripts/playground.sh batch
```

## Desarrollo, calidad y próximos pasos

- [Contrato CLI](docs/CLI-CONTRACT.md): comportamiento observable y garantías.
- [CI/CD y matriz de Vault real](docs/CI-CD.md): tests, limitaciones y gates.
- [Revival y secuencia de entrega](docs/REVIVAL.md): estado de la recuperación.
- [Fase 04-Q — calidad y eficiencia](docs/CODE-QUALITY.md): revisión de
  duplicidades, helpers reutilizables, optimización medida, comentarios y
  documentación, **previa a la auditoría/release v0.7**.
- [Catálogo del playground](playground/README.md): rutas, fixtures y recetas.

**Evolución futura (no implementada en v0.7):** blueprints declarativos YAML,
`validate/plan/apply/drift`, gestión declarativa de accesos, plantillas de
escenarios SaaS/multiusuario/multiapp y rotación. Pertenecen al roadmap
posterior de **vcli**, no a otro proyecto ni a funcionalidades disponibles hoy.

## Antecedentes del proyecto

El proyecto original nació como una utilidad Bash para navegar y gestionar
Vault (en sus primeras etapas se probaba contra Vault 0.9.x). La antigua
documentación, su demostración y sus bugs históricos **no deben
interpretarse como el comportamiento del candidato actual**; el contrato
actual está en `docs/CLI-CONTRACT.md`.

[Demostración histórica (asciinema)](https://asciinema.org/a/132636)

Para comparar el original y el nuevo Bash:

```bash
git diff --no-index -- vcli.orig.sh vcli
```

El comando devuelve código 1 si encuentra diferencias.

### Contribuidores del proyecto original

- Marcos Lorenzo (creador) — [mlorenzo-stratio](https://github.com/mlorenzo-stratio)
- Ángel Prada — [angelprada](https://github.com/angelprada)
- Viktor Jacynycz — [vjacynycz-stratio](https://github.com/vjacynycz-stratio)
