# Fase 04-Q — Calidad, refactorización y eficiencia

**Estado: en ejecución incremental; A/B y auditoría S-01–S-04 documentadas,
cierre pendiente.** Hito obligatorio antes de la auditoría y RC v0.7.
Los cambios funcionales ya implementados se enumeran con sus pruebas y
limitaciones; no se considera completada la revisión de calidad.

## Balance de la auditoría de seguridad (PR #25)

| ID | Prioridad | Riesgo corregido | Protección / evidencia |
|---|---|---|---|
| S-01 | Alta | Payload KV v2 temporal abandonado tras error | Limpieza explícita de `_put` y regresiones de 403/transportes; CI verde |
| S-02 | Alta | Temporales sensibles mezclados con artefactos persistentes | Directorio por sesión 0700, señales y aislamiento de procesos; CI verde |
| S-03 | Alta | Credenciales AppRole visibles en argv de `jq` | Descriptores anónimos, preflight mTLS y regresiones de señales; CI verde |
| S-04a | Alta | Sobrescritura o symlink en backup | Archivo privado y publicación exclusiva mediante `ln -T`; CI verde |
| S-04b | Alta | Debug sobre rutas inseguras y carrera de directorio en historial | Descriptor validado para logs, `mv -T` e historial leído por FD; CI verde |

**Evidencia del estado consolidado:** [CI completo #44044728301](https://github.com/ml0renz0/vcli/actions/runs/44044728301)
(estado SUCCESS: static, playground TLS, cuatro versiones de Vault,
Alpine, Debian y required gate). Las pruebas aisladas complementan, pero
no reemplazan, los contratos E2E con Vault real. El backend Raft del
benchmark sigue siendo opcional y no equivale a una topología productiva.

### Riesgos y trabajos abiertos antes del RC

- **Concurrencia:** el historial entre dos sesiones conserva semántica de
  último escritor; no hay reconciliación de cambios concurrentes.
- **Portabilidad:** `ln -T`, `mv -T`, `stat -L -c`, `/dev/fd` y algunos
  comportamientos de `mktemp` dependen de GNU/Linux. Sin compatibilidad
  nativa macOS/BSD acreditada.
- **Señales y persistencia:** `SIGKILL`, pérdida de alimentación y
  fallos del host pueden dejar temporales. Backups y exportaciones
  solicitados por el usuario permanecen deliberadamente en disco.
- **Alcance del auditor:** revisar caminos raros de error, permisos de
  directorios padre, REPL con Unicode/redimensionamiento y el playground
  exploratorio. Mantener el tratamiento de escrituras HTTP con resultado
  ambiguo y las limitaciones de atomicidad de Vault.
- **Rendimiento:** más muestras y recuento de procesos/llamadas HTTP antes
  de inferir efectos generales o establecer límites cuantitativos.

**Decisión:** S-01–S-04 se consideran implementadas y verificadas en la
matriz obligatoria, pero **04-Q no está cerrada**. No iniciar el RC v0.7
sin resolver o aceptar explícitamente los riesgos bloqueantes y pasar la
fase 05 con el candidato congelado.

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
`storage_backend=inmem`, `raft-1` o `raft-3`. Los tres perfiles utilizan el mismo benchmark; con un commit de referencia
se comparan dos CLI (A/B) y con `none` se mide exclusivamente el candidato. Para probar localmente:

```bash
VCLI_BENCH_STORAGE=raft-1 bash scripts/bench-compare.sh 4f96cf2091be1d6c70a5e3df3bf214d6dfb4c67e 10,100 4
VCLI_BENCH_STORAGE=raft-3 bash scripts/bench-compare.sh 4f96cf2091be1d6c70a5e3df3bf214d6dfb4c67e 10,100 4
```

**Validación funcional real completada:** [inmem #43138509881](https://github.com/ml0renz0/vcli/actions/runs/43138509881),
[raft-1 #43137292992](https://github.com/ml0renz0/vcli/actions/runs/43137292992) y
[raft-3 #43138179058](https://github.com/ml0renz0/vcli/actions/runs/43138179058).
En Raft-3 el runner verificó un líder y **tres votantes**, y el bootstrap
Shamir/TLS terminó correctamente. Los resultados de cada operación se
contrastaron con el estado de Vault, no solo con el exit code de vcli.

## Flags de benchmark en pull requests

Las etiquetas de repositorio `bench:inmem`, `bench:raft-1` y `bench:raft-3`
seleccionan respectivamente un Vault de desarrollo, Raft en disco con un nodo
y Raft HA con tres votantes. `.github/workflows/benchmark-labels.yml`
crea las etiquetas ausentes cuando se abre o actualiza una PR del propio
repositorio, **sin aplicarlas**. Añadir manualmente una etiqueta a una PR
lanza únicamente el benchmark seleccionado; cambiar archivos, abrir la PR
o añadir otras etiquetas **no** dispara ese benchmark. La ejecución por etiqueta
se mantiene separada del CI obligatorio y del lanzamiento manual existente.

Las tres etiquetas de perfil miden **solo el candidato actual** (sin baseline
histórica). Además, `bench:ab-inmem` ejecuta una comparativa por parejas
contra el commit verde fijado en `.github/workflows/benchmark-labels.yml`
(actualmente `0b69ae8`); actualizar ese commit para medir un cambio posterior.
Todas usan cargas `10,100` y cuatro muestras por operación. Cada etiqueta
desencadena una prueba cuando se añade,
no en cada commit posterior. Para repetir la misma prueba, retirar la etiqueta
y volver a aplicarla. La creación de etiquetas necesita que Actions tenga
permiso `issues: write` en el job de aprovisionamiento, no en el job de
benchmark. Por motivos de seguridad, el flujo por etiqueta solo ejecuta PR
cuyo repositorio de origen coincide con el de destino (no forks).

## Resultados reales: perfiles inmem / Raft, 2026-11-12

Los tres workflows etiquetados usan **Vault 2.1.1**, TLS verificado,
el mismo `vcli` candidato y 4 muestras por combinación de tamaño y
operación; los resultados se validan funcionalmente. Las cifras están
redondeadas a una décima de milisegundo y son **medianas**, no percentiles
p95 ni mejoras de versiones.

| Secretos | Operación | inmem (ms) | raft-1 (ms) | raft-3 (ms) |
| ---: | --- | ---: | ---: | ---: |
| 10 | tree | 248,6 | 339,2 | 380,8 |
| 10 | find | 442,3 | 603,1 | 674,4 |
| 10 | cptree | 750,1 | 1.063,6 | 1.187,5 |
| 10 | backup | 470,3 | 630,9 | 729,3 |
| 100 | tree | 254,2 | 316,3 | 385,1 |
| 100 | find | 2.247,7 | 2.817,1 | 3.474,6 |
| 100 | cptree | 5.438,1 | 7.097,7 | 8.614,9 |
| 100 | backup | 2.424,0 | 3.131,4 | 3.753,6 |

**Interpretación:** estos perfiles se ejecutaron en runners efímeros
distintos de GitHub Actions. Las diferencias observadas **no aíslan**
el efecto del backend o del número de nodos porque también pueden reflejar
variabilidad de CPU, disco, carga y red local de cada runner. Raft-3 usa
tres contenedores en **un único host**, no tres zonas de disponibilidad.
Estas medidas tampoco representan la latencia de un despliegue de producción.

**Actualización:** las comparativas A/B verificadas de `tree`, `backup` y
`cptree` figuran a continuación, con dos revisiones validadas en el mismo
runner y Vault. Siguen pendientes muestreos mayores e instrumentación
comparativa de procesos/HTTP para una conclusión estadística más robusta.
El commit histórico `4f96cf2` fue descartado como baseline porque devolvía
recorridos incompletos; no se atribuyen mejoras respecto a él.

## Medición A/B de la optimización de `tree` — 2026-11-13

Referencia: commit `c009d45` (matriz TLS completa verde). Candidato: `6d944b4`
con `_tree` sin la llamada externa a `basename` por directorio y normalización
robusta de barras finales. [GitHub Actions #43414343529](https://github.com/ml0renz0/vcli/actions/runs/43414343529)
terminó correctamente contra un **único Vault inmem TLS**, con ambos
binarios ejecutados en orden alternado sobre los mismos datos y runner,
cuatro muestras por operación y tamaño. Ambos superaron la validación
funcional; no son tiempos de despliegue productivo.

| Secretos | Operación | Base p50 (ms) | Candidato p50 (ms) | Cambio |
| ---: | --- | ---: | ---: | ---: |
| 10 | `tree` | 280,8 | 265,7 | −5,4 % |
| 10 | `find` | 480,3 | 484,2 | +0,8 % |
| 10 | `cptree` | 818,6 | 816,0 | −0,3 % |
| 10 | `backup` | 522,5 | 519,3 | −0,6 % |
| 100 | `tree` | 289,1 | 275,4 | −4,7 % |
| 100 | `find` | 2.563,5 | 2.496,2 | −2,6 % |
| 100 | `cptree` | 6.550,2 | 6.496,1 | −0,8 % |
| 100 | `backup` | 2.658,4 | 2.793,1 | +5,1 % |

**Interpretación:** observamos una reducción pequeña y consistente de la
mediana de `tree` para estos dos casos; el cambio elimina exactamente
una invocación de `basename` por directorio visitado. Con solo cuatro
muestras no puede afirmarse una ganancia estadísticamente estable, ni
atribuir a esta optimización las diferencias de otras operaciones. El
p95 con cuatro muestras coincide con el máximo; para una conclusión
más sólida harían falta más repeticiones y perfiles Raft A/B. No
se introducen gates de rendimiento por porcentajes arbitrarios.

## Optimización de backup: menos procesos por secreto

En `_backup`, `dirname` se sustituye por expansión nativa Bash y
`mkdir -p` solo se llama si falta el directorio destino. Esto evita
un proceso por hoja y llamadas redundantes a `mkdir` para hermanos
del mismo directorio. El contrato aislado comprueba seis ficheros de dos
carpetas, una con espacios, verifica el tarball y el número de llamadas
a `mkdir` sin utilizar Vault.

La comparación A/B posterior confirmó una reducción de mediana en `backup`
para los casos de 10 y 100 secretos; véase la tabla inmediatamente siguiente.
Las cuatro muestras disponibles no demuestran significación estadística.


### Medición A/B de `backup` — 2026-11-15

Referencia: `cdf1c8c` (último CI verde anterior al cambio).
Candidato: optimización de `_backup` sin `dirname` por secreto y sin
`mkdir -p` para padres que ya existan. [GitHub Actions
#43718675927](https://github.com/ml0renz0/vcli/actions/runs/43718675927)
ha terminado **verde**, comparando ambas versiones en orden alternado
contra el mismo Vault 2.1.1 `inmem` TLS, sobre el mismo runner, con
cuatro muestras y validación funcional de cada operación.

| Secretos | Operación | Base p50 (ms) | Candidato p50 (ms) | Cambio |
| ---: | --- | ---: | ---: | ---: |
| 10 | `tree` | 350,5 | 350,5 | ~0,0 % |
| 10 | `find` | 661,9 | 659,7 | −0,3 % |
| 10 | `cptree` | 1.143,3 | 1.150,5 | +0,6 % |
| 10 | `backup` | 720,6 | 702,8 | **−2,5 %** |
| 100 | `tree` | 365,4 | 365,1 | −0,1 % |
| 100 | `find` | 3.401,5 | 3.382,5 | −0,6 % |
| 100 | `cptree` | 8.157,6 | 8.189,7 | +0,4 % |
| 100 | `backup` | 3.629,5 | 3.373,9 | **−7,0 %** |

La mejora coincide con el efecto esperado de eliminar un `dirname`
externo por secreto y reducir preparaciones de directorios a los padres
distintos. Los controles no muestran una variación comparable. Con solo
cuatro muestras no se declara significación estadística ni se extrapola
el resultado a Vault Raft o a sistemas productivos. La reducción de
procesos por hoja sí queda protegida por el contrato aislado.

## Optimización cptree: clasificación reutilizada

El preflight conserva las consultas de metadata KV v2 (incluidos secretos
soft-deleted) y la validación de todos los destinos antes de escribir.
Ahora almacena una clasificación KV1/KV2 por hoja, construye metadata con
el montaje de prefijo más específico y evita repetir la consulta del tipo
de montaje al escribir. La longitud del array evita una pasada de recuento.
Los tests aislados protegen montajes solapados, espacios en rutas, CAS=0,
orden del preflight y la prohibición de escritura ante HTTP 403.
La matriz TLS real conserva las pruebas de colisiones y fallos parciales.
El A/B contra `0b69ae8` [terminó verde](https://github.com/ml0renz0/vcli/actions/runs/43776893780):
`cptree` pasó de 1.014,4 a 1.011,5 ms con 10 secretos (−0,3 %) y de
7.436,8 a 7.334,6 ms con 100 (−1,4 %), sin mejora estadística fuerte
demostrada con cuatro muestras.

## Auditoría S-01: limpieza de temporales sensibles en `_put`

**Prioridad alta.** Un error temprano de metadata KV v2 o una salida
prematura de `_curl` podía abandonar un fichero `.put.*` con contenido
de un secreto. Ahora `_put` usa variables locales y elimina el payload
en las ramas de error y después de cualquier respuesta de `_curl`, incluso
si el transporte falló sin consumir el fichero. La expansión fallida con
`envsubst` también aborta la escritura. Tests aislados verifican 403,
metadata inválida, fallo de ruta de metadata, error de transporte y éxito.
La protección frente a señales y fallos asíncronos requiere auditoría
separada del ciclo global de `TMP`.

## A/B de `cptree` validado

[CI #43776834258](https://github.com/ml0renz0/vcli/actions/runs/43776834258)
y [A/B #43776893780](https://github.com/ml0renz0/vcli/actions/runs/43776893780)
verdes. Medianas sobre un mismo Vault TLS y runner, cuatro muestras:
10 secretos, 1.014,4 → 1.011,5 ms (−0,3 %); 100 secretos,
7.436,8 → 7.334,6 ms (−1,4 %). Mejoras pequeñas y no concluyentes
estadísticamente.

## Auditoría S-02: aislamiento de temporales y señales

El cliente conserva `TMPDIR` para artefactos **solicitados por el usuario**
(backups `.tgz`, certificados, claves y keytabs), pero ahora crea al
iniciar una sesión válida un directorio aleatorio privado con modo
`0700` para las respuestas/payloads HTTP, el material transitorio de
AppRole/KV, el backup antes de comprimirlo y las exportaciones en staging.
El directorio se elimina en `EXIT`, y `SIGTERM`/`SIGINT` provocan
salida con estado 143/130 en modo batch. El editor REPL mantiene su
comportamiento propio de Ctrl+C sin abandonar la sesión.

La limpieza comprueba el identificador `BASHPID` del propietario para
evitar que un subshell suprima los datos transitorios del proceso
padre. Se añaden contratos aislados para salida normal, TERM, INT,
propiedad en subshell, permisos `0700`, un `_put` interrumpido y
supervivencia de artefactos externos a la sesión.

**Límites:** ninguna implementación puede garantizar limpieza ante
`SIGKILL`, fallos del kernel, cortes de alimentación u otros cierres
forzosos; mientras el REPL siga activo después de una interrupción,
los temporales residuales privados se eliminan al salir. Los archivos
de backup/exportación **no se eliminan** automáticamente.

## S-03: AppRole, argumentos y temprana limpieza HTTP

`_vcli_login_approle` ya no pasa `secret_id` por `jq --arg` (visible en argv).
En su lugar `jq --rawfile` recibe credenciales desde descriptores anónimos;
se preservan comillas y saltos de línea. Se rechaza mTLS incompleto
antes de crear archivos de credenciales, y `_curl` elimina la respuesta
si falla el `mktemp` del payload KV2. La prueba aislada inspecciona
los argumentos reales de jq, JSON, rechazo mTLS antes de EXIT e
interrupción TERM durante autenticación, además de conservar artefactos.

## S-04 (parte 1): publicación exclusiva de backups

**Hallazgo:** `_backup` generaba un nombre con resolución de un segundo y
ejecutaba `tar -czf` directamente sobre `TMPDIR`: dos backups del mismo
árbol podían sobrescribirse y un atacante con capacidad de crear un symlink
en el destino podía conseguir que se truncara su objetivo.

**Corrección:** se crea el fichero `.tgz` entero dentro del directorio
privado de sesión, con permisos 0600; el nombre final lleva una parte
aleatoria de `mktemp`. Se publica mediante `ln -T` exclusivamente
(en el mismo sistema de archivos). Si el destino existe o es un symlink,
la publicación falla sin reemplazarlo ni modificarlo. Ningún archivo
incompleto se publica antes de terminar `tar`. Solo se elimina el staging,
no los ficheros ajenos ni los backups publicados.

Las regresiones simulan una creación concurrente de symlink y de archivo
justo antes de publicar, validan ausencia de truncado, dos backups en un
mismo segundo, permisos 0600 y error de compresión sin salida parcial.
El mecanismo requiere `ln -T` GNU, ya requerido por las exportaciones
atómicas. Pendiente de S-04: endurecer el log de depuración y revisar
carreras del historial; las comprobaciones de rutas por nombre no bastan
para prometer ausencia de TOCTOU en todos los sistemas de archivos.

## S-04 (parte 2): logs e historial ante carreras de rutas

El log de depuración se crea de forma exclusiva con un fichero temporal
0600 y `ln -T`, o se abre para añadir texto si ya existe. El descriptor
real se verifica ANTES de la primera escritura: archivo regular, propio,
permisos 0600, un único enlace y coincidencia de inode con la ruta.
Se rechazan symlinks, hardlinks y logs con permisos inseguros. Las
escrituras posteriores reutilizan el descriptor validado, por lo que
un cambio de pathname no redirige las escrituras a un fichero ajeno.
Al desactivar el modo debug se cierra el descriptor.

En historial, `mv -T -f` evita que una carrera convierta el destino
en un directorio capaz de recibir el archivo de historial temporal.
La lectura ahora fija un descriptor y verifica la ruta antes de cargar.
Pruebas aisladas: nombre con espacios, append privado, links y
permisos inseguros, reemplazo de ruta tras apertura, historia con
symlink y sustitución del destino por directorio.

**Límites:** el historial entre sesiones simultáneas conserva una
política de último escritor; no se ofrece mezcla transaccional de
comandos concurrentes. Estas comprobaciones no protegen contra
actores con control completo de directorios padres. `ln -T`,
`mv -T`, `/dev/fd` y `stat -L -c` son dependencias GNU/Linux
que requieren adaptación en la futura fase de portabilidad macOS/BSD.
