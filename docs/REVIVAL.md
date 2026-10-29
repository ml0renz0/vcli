# Revival de vcli — Bash mantenible y verificable

**Estado:** candidato v0.7 en PR, todavía no publicado como estable.
Se conserva el ejecutable histórico `vcli.orig.sh` sin modificar y se
desarrolla directamente `vcli` como único ejecutable de producción.

```bash
git diff --no-index -- vcli.orig.sh vcli
```

El código de producción es **Bash autocontenido**. Python se usa para
pruebas, benchmarks y herramientas de desarrollo, no como motor de `vcli`.

## Objetivos y garantías

- Operaciones Vault KV v1/v2 con montajes anidados y rutas verificables.
- HTTPS verificado por defecto, CA propia opcional y autenticación token/AppRole.
- Sin mutaciones al arrancar ni filtración deliberada de credenciales.
- Comandos históricos útiles y REPL con garantías de seguridad documentadas.
- Errores y resultados parciales explícitos donde Vault no es transaccional.
- Calidad y eficiencia demostrables con pruebas y mediciones antes del RC.

## Estado de las fases

| Hito | Alcance | Situación |
|---|---|---|
| 00 | Baseline, CI/CD y Vault TLS real | Implementado y cubierto por CI |
| 01 | Revival Bash, KV y operaciones base | Implementado y cubierto por CI |
| 02 | PKI, X.509, claves y Kerberos | Implementado y cubierto por CI |
| 03 | REPL, historial y terminal | Batería principal implementada; quedan casos UX específicos |
| 04 | Portabilidad, robustez, benchmark | Matriz Linux y playground operativos; quedan casos límites |
| Playground | Vault TLS real, fixtures KV/PKI/Kerberos/ACL/AppRole | Implementado y con aceptación automática; exploración manual pendiente |
| **04-Q** | **Revisión de calidad, refactorización y eficiencia** | **Planificado, obligatorio antes del RC** |
| 05 | Auditoría final y release candidate v0.7 | Pendiente |
| Evolución posterior | YAML, plan/apply/drift, políticas, plantillas y rotación | Roadmap, no implementado en v0.7 |

La descripción de las fases indica cobertura de CI dentro del alcance
documentado, **no** una declaración de compatibilidad universal.

## Pruebas y laboratorio

La matriz del workflow valida el `vcli` real con Vault nativo TLS
en cuatro versiones desde Ubuntu y todas las suites E2E sobre Vault 2.1.1
desde Alpine/Debian. También se comprueban terminales PTY y rutas de fallo.
Consulta [CI/CD](CI-CD.md) y [contrato CLI](CLI-CONTRACT.md).

El [playground](../playground/README.md) permite lanzar un Vault descartable
con datos sintéticos y ejecutar `vcli` directamente, sin mocks:

```bash
scripts/playground.sh up
scripts/playground.sh smoke
scripts/playground.sh shell
# Al terminar:
scripts/playground.sh down
```

Se precargan KV v1/v2, dos versiones de valores ficticios, montajes anidados,
JSON, certificados y claves X.509, keytabs MIT, ejemplos malformados, ACL
de lectura y AppRoles. El `smoke` comprueba API/CLI, permisos, autenticación
y limpieza; no sustituye una revisión exploratoria completa.

## Siguiente hito — Fase 04-Q: calidad y eficiencia

**No se considera completada.** Antes de la auditoría final se realizará
una revisión incremental del código Bash para:

1. Inventariar duplicidades, funciones con demasiadas responsabilidades,
   estado global, errores y temporales, priorizando seguridad y corrección.
2. Identificar helpers reutilizables en rutas KV v2, recorrido de árboles,
   transporte HTTP, validación PKI/Kerberos y REPL, sin introducir
   dependencias obligatorias ni alterar el contrato externo.
3. Medir procesos externos, llamadas HTTP, coste de parsing, CPU/memoria
   y latencia mediana/p95 sobre el mismo catálogo de Vault antes/después.
4. Aplicar cambios pequeños, con regresiones primero, matriz E2E obligatoria
   y benchmark comparativo para aceptar o revertir cada mejora.
5. Añadir **comentarios breves centrados en invariantes y decisiones no
   obvias**, evitando documentación redundante, y revisar los cinco Markdown.

La especificación completa, candidatos concretos y criterios de salida
están en [`docs/CODE-QUALITY.md`](CODE-QUALITY.md).

## Release y evolución posterior

**Orden:** finalizar límites de fase 04 → **04-Q** → auditoría fase 05 →
release candidate v0.7 con gate totalmente verde y artefacto verificable.
El playground y la matriz real siguen siendo obligatorios.

Las futuras funciones declarativas (blueprints YAML, validación, plan,
aplicación, detección de drift, gestión de accesos, plantillas SaaS y rotación)
son **evolución de vcli dentro de este mismo proyecto**. No existe aquí
un segundo producto implementado denominado «VCLI Forge».
