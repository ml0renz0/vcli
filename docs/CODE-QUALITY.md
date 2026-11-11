# Fase 04-Q — Calidad, refactorización y eficiencia

**Estado: en ejecución incremental; cierre y comparativa pendientes.** Hito obligatorio de la
recuperación de `vcli`, después de la cobertura funcional y del playground
con Vault real, y **antes de la auditoría y release candidate v0.7**.
No modifica por sí solo el comportamiento del CLI ni declara optimizaciones
ya implementadas.

## Alcance

Revisar `vcli` (Bash de producción), `scripts/playground*.sh`,
`tests/` y documentación. Mantener `vcli` autocontenido en Bash;
`vcli.orig.sh` permanece idéntico al original. Python puede seguir
usándose únicamente como herramienta de pruebas y medición.

### 1. Inventario y calidad estructural

- Enumerar funciones, dependencias entre módulos lógicos, estado global,
  parámetros implícitos, efectos secundarios, manejo de errores y rutas de
  limpieza; priorizar funciones extensas, bucles anidados y código duplicado.
- Revisar quoting/word splitting, `set -e` y subshells, salidas de `curl`,
  permisos/umask, carreras de ficheros y llamadas no idempotentes.
- Clasificar hallazgos en **seguridad / corrección / mantenibilidad /
  rendimiento / portabilidad**, con severidad y pruebas que los reproduzcan.
- Mantener trazabilidad mediante un inventario antes/después y commits
  pequeños agrupados por responsabilidad, no una reescritura masiva.

### 2. Identificar código reutilizable (candidatos observados)

| Área | Candidatos para estudiar | Riesgo que no debe perderse |
|---|---|---|
| Rutas KV v1/v2 | `vcli_is_kv2_path`, `vcli_kv2_metadata_path`, `vcli_kv_path`, lógica de montajes dentro de `_curl` y `_cptree` | Montajes anidados, elección de versión, CAS y API paths |
| Recorridos de árboles | `_vcli_absolute_path`, `_vcli_children`, `_vcli_leaves` y consumidores como `_replace`, `_cptree`, `_rmtree`, `_backup` | Orden de lectura/escritura, parcialidad, ACL, límites del subárbol |
| PKI y Kerberos | `_vcli_pki_json`, `_vcli_cert_pairs`, `_vcli_krb_pairs`, listados, exportación y caducidad | Validación criptográfica, permisos 0600, exclusión de symlinks |
| HTTP y temporales | `_curl`, manejo de payload/respuesta, creación y limpieza de ficheros | No filtrar tokens; evitar reintentos ciegos y preservar errores |
| REPL y terminal | Parseo de teclas, redibujado, historial, autocompletado | Ctrl+C, paste, Unicode/anchura de cursor y TERM mínimo |
| Playground/CI | Bootstrap, datos sintéticos, helpers Bash y scripts de aceptación | Independencia del laboratorio y limpieza de credenciales |

Estas son **hipótesis de refactorización** basadas en el código actual.
Solo se extraerán helpers si mejoran legibilidad, corrección o rendimiento
sin acoplamiento innecesario. No se introducirá una biblioteca externa
obligatoria para ejecutar `vcli`.

### 3. Eficiencia medible

- Establecer una **línea base reproducible**, comparando en el mismo runner,
  versión de Vault y catálogo de datos, antes y después de cada cambio.
- Medir mediana/p95, procesos externos, llamadas HTTP, bytes procesados y
  memoria/CPU cuando se pueda instrumentar sin comprometer secretos.
- Escenarios: `--version`, `cat` KV v1/v2, `ls`, recorridos de 10/100/1000
  secretos sintéticos, `find`, `cptree`, `backup/restore`, listados PKI
  y sesiones REPL de uso habitual. Los tamaños 100/1000 son propuestas
  de carga, no prestaciones actualmente demostradas.
- Investigar llamadas repetidas a `jq`, `curl`, `openssl`, `date` y
  pipelines por elemento; amortizar transformaciones o agrupar resultados
  solo cuando Vault y los contratos permitan hacerlo con seguridad.
- No fijar umbrales de regresión arbitrarios: conservar la línea base y
  acordar límites razonables tras varias muestras. Documentar condiciones,
  dispersión y cualquier cambio de metodología.

### 4. Refactorización incremental y protección

1. Capturar el comportamiento actual con pruebas de contrato o nuevas
   regresiones antes de modificar una ruta crítica.
2. Cambiar **una responsabilidad por commit**, sin reorganización cosmética
   de miles de líneas ni alterar contratos, códigos de salida o trazas
   sensibles incidentalmente.
3. Repetir `bash -n`, ShellCheck, tests CLI, suites E2E de Vault 1.20.4 /
   1.21.4 / 2.0.4 / 2.1.1, Alpine/Debian y aceptación del playground.
4. Contrastar salidas, estado real por API, archivos exportados y métricas
   **antes/después**; conservar o revertir según evidencia.
5. Documentar límites de atomicidad, portabilidad y concurrencia que
   persistan tras los cambios. No relajar gates, saltar tests ni depender
   de mocks para declarar corrección de las operaciones reales.

### 5. Comentarios breves y documentación mínima

- Escribir comentarios **cortos que expliquen el porqué**, invariantes,
  efectos secundarios, precondiciones y decisiones de seguridad; no repetir
  lo evidente del código.
- Documentar entradas/salidas y errores solo en helpers no triviales; usar
  nombres autoexplicativos antes de aumentar comentarios.
- Comentar especialmente las partes delicadas: KV v2/CAS, rollback parcial,
  material criptográfico, ficheros privados, traps y compatibilidad de TTY.
- Revisar `README.md`, `docs/CI-CD.md`, `docs/CLI-CONTRACT.md`,
  `docs/REVIVAL.md` y `playground/README.md`: eliminar descripciones
  obsoletas, diferenciar **histórico / implementado / pendiente** y comprobar
  enlaces, comandos y versiones. Usar una única identidad de proyecto,
  `vcli`; las futuras capacidades YAML/declarativas son **roadmap** y no
  forman parte del candidato actual.

## Criterios de aceptación

- Informe breve de hallazgos priorizados, duplicidades corregidas o
  justificadamente conservadas y mapa de responsabilidades de helpers.
- Todas las suites obligatorias pasan con el mismo candidato Bash y no hay
  degradación funcional ni de seguridad conocida.
- Comparativa de rendimiento reproducible antes/después; mejoras
  cuantificadas o justificación explícita si se prioriza mantenibilidad.
- Sin secretos en stdout, historial, temporales persistentes, trazas ni
  scripts de prueba; sin escritura/despliegue contra Vault externo.
- Código documentado solo donde añade información, con Markdown coherentes
  y ejemplos comprobados. `vcli.orig.sh` no se modifica.
- La **auditoría final y el RC v0.7 quedan bloqueados** hasta completar y
  revisar esta fase. El playground y la matriz E2E continúan como gates.

## Secuencia en el plan

Fases 00–03 (contratos y funcionalidad) → fase 04 (robustez, portabilidad,
playground) → **04-Q (calidad y eficiencia)** → fase 05 (auditoría final y
release candidate v0.7) → evolución funcional declarativa posterior.

## Avance medible: recorridos recursivos

- `_vcli_children` combina validación de `data.keys` y extracción en **una**
  invocación de `jq` en lugar de dos por listado. Los contratos aislados
  comprueban listados correctos, vacíos, error HTTP y respuesta inválida.
- El benchmark aísla cada cardinalidad en `tree-size-N`, alterna el orden
  referencia/candidato por muestra y verifica que `tree` enumera exactamente
  N secretos. `tests/cli/test_recursive_benchmark.py` protege la metodología
  sin levantar Vault ni consumir los minutos de integración.
- Benchmark **opt-in** `tests/integration/bench_recursive.py`: mide
  `tree`, `find`, `cptree` y `backup` contra el Vault HTTPS desechable,
  verificando el resultado de cada operación. Los datos de origen se guardan
  en un montaje temporal y se eliminan desmontándolo incluso si hay errores.
- Ejecución manual desde la raíz:
  ```bash
  VCLI_TEST_VAULT_TAG=2.1.1 VCLI_BENCH_RECURSIVE=1 \\
    VCLI_BENCH_SIZES=10,100 VCLI_BENCH_REPEATS=3 \\
    bash tests/integration/run.sh
  ```
  El runner primero ejecuta las suites completas de integración; el
  benchmark solo se lanza al solicitarlo. Para incluir 1000 secretos,
  establecer `VCLI_BENCH_SIZES=10,100,1000`, aumentar
  `VCLI_BENCH_TIMEOUT` si fuese necesario y reservar tiempo suficiente.
  Para comparar dos commits con **un solo Vault TLS** y omitir la matriz
  E2E durante la medición, ejecutar desde la raíz:
  ```bash
  bash scripts/bench-compare.sh 4f96cf2091be1d6c70a5e3df3bf214d6dfb4c67e 10,100 4
  ```
  Se puede utilizar GitHub Actions → **VCLI trunk validation** → **Run
  workflow**, seleccionar la rama de la PR y activar
  `recursive_benchmark`. Los inputs `baseline_ref`, `sizes` y
  `repeats` controlan el experimento. Ese despacho deja fuera
  `integration`, `minimal`, `playground` y `gate`; ejecuta la
  validación estática y un job independiente de benchmark con un único Vault
  desechable. El CI normal de PR y push mantiene los siete Vault. La acción
  manual requiere que GitHub permita despachar el workflow desde la rama
  seleccionada; no se ha ejecutado todavía una comparativa de rendimiento.
  Una comparativa por parejas admite `VCLI_BENCH_BASELINE=/ruta/vcli-anterior`
  junto con `VCLI_BENCH_CANDIDATE=/ruta/vcli-candidato`. Ambos deben ser
  ejecutables Bash que acepten `-n`. Las métricas p50/p95 no constituyen
  todavía una comparación publicada ni un objetivo de regresión.

## Almacenamiento integrado: perfiles Raft opcionales

El CI funcional sigue utilizando **siete Vault de desarrollo TLS efímeros**
sobre `inmem`. Los perfiles Raft están restringidos al benchmark manual;
**no son una simulación de latencia de producción**, ya que los nodos del
perfil HA comparten la misma máquina Docker, y las medidas de disco/red de
GitHub Actions no representan discos ni zonas de disponibilidad reales.

| Perfil | Instancias | Objetivo |
| --- | ---: | --- |
| `inmem` | 1 | Aislar el coste del cliente y los procesos |
| `raft-1` | 1 | Persistencia integrada en disco, sin replicación |
| `raft-3` | 3 | Consenso Raft con quórum 2/3 y replicación local |

`tests/integration/run-raft.sh` crea una red y certificados TLS privados
de vida corta, configura una ruta de datos independiente por nodo, inicializa
Vault con Shamir (una participación por ser un entorno efímero), une y
desprecinta los seguidores y **verifica los votantes y un solo líder** antes
de medir. Se elimina el clúster completo al salir. No conserva datos,
credenciales o llaves tras la prueba. Requiere Docker local y se niega
a usar un daemon remoto. El perfil no es una receta de configuración segura
de Vault para producción.

Desde GitHub Actions → **VCLI trunk validation** → **Run workflow**:
activar `recursive_benchmark`, seleccionar la rama de la PR y elegir
`storage_backend=inmem`, `raft-1` o `raft-3`. Los tres perfiles utilizan
el mismo benchmark y su misma comparación A/B. Para probar localmente:

```bash
VCLI_BENCH_STORAGE=raft-1 bash scripts/bench-compare.sh 4f96cf2091be1d6c70a5e3df3bf214d6dfb4c67e 10,100 4
VCLI_BENCH_STORAGE=raft-3 bash scripts/bench-compare.sh 4f96cf2091be1d6c70a5e3df3bf214d6dfb4c67e 10,100 4
```

**Pendiente de verificación:** ejecutar los perfiles Raft con Docker real
antes de dar por completada esta parte del plan. Los contratos estáticos
verifican el selector, la topología esperada y los rechazos de perfiles
inconsistentes, pero no sustituyen una prueba funcional real.

## Flags de benchmark en pull requests

Las etiquetas de repositorio `bench:inmem`, `bench:raft-1` y `bench:raft-3`
seleccionan respectivamente un Vault de desarrollo, Raft en disco con un nodo
y Raft HA con tres votantes. `.github/workflows/benchmark-labels.yml`
crea las etiquetas ausentes cuando se abre o actualiza una PR del propio
repositorio, **sin aplicarlas**. Añadir manualmente una etiqueta a una PR
lanza únicamente el benchmark seleccionado; cambiar archivos, abrir la PR
o añadir otras etiquetas **no** dispara ese benchmark. La ejecución por etiqueta
se mantiene separada del CI obligatorio y del lanzamiento manual existente.

Se utiliza una revisión base fijada y cargas `10,100` con cuatro muestras
por operación; cada etiqueta desencadena una prueba una vez cuando se añade,
no en cada commit posterior. Para repetir la misma prueba, retirar la etiqueta
y volver a aplicarla. La creación de etiquetas necesita que Actions tenga
permiso `issues: write` en el job de aprovisionamiento, no en el job de
benchmark. Por motivos de seguridad, el flujo por etiqueta solo ejecuta PR
cuyo repositorio de origen coincide con el de destino (no forks).
