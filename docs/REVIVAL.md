# VCLI revival: cambios directos sobre Bash

La PR conserva el histórico intacto en `vcli.orig.sh` y desarrolla
`vcli` como candidato Bash revisable. Para inspeccionar exactamente las
diferencias, ejecutar `git diff --no-index -- vcli.orig.sh vcli`.
Este comando devuelve código 1 cuando encuentra diferencias.

No existe un generador de parches Python. CI valida **el fichero Bash que
realmente se entregará**, con los contratos de `docs/CLI-CONTRACT.md`.

## Objetivos

- Vault actual con KV v1 y KV v2 explícitamente configurado.
- TLS verificado por defecto. VAULT_CACERT permite usar una CA privada.
- El cliente no hace escrituras al arrancar.
- Tokens restringidos y errores HTTP sin filtración de secretos.
- Mantener comandos y REPL históricos, documentando compatibilidades.

## Contratos y pruebas

`python3 -m unittest -v tests.cli.test_cli_contract` cubre la interfaz
independiente de Vault. La matriz de integración levanta Vault con TLS
nativo y verifica el estado a través de su API.

`docs/CI-CD.md` describe los gates y pruebas pendientes. No utilizar un
servidor productivo ni credenciales reales en la suite.

## Próximo hito — Playground local contra Vault real (fase 04, antes del RC)

**Estado: planificado; aún no implementado.** El CI ya comprueba el CLI con
Vault real y TLS, pero inicia y destruye sus servidores automáticamente.
El playground añadirá un entorno **manual, reproducible y aislado**, donde
probar el `vcli` Bash de esta misma rama con datos sintéticos antes de publicar
un release candidate. No será un simulador de HTTP ni un reemplazo del CI.

### Diseño previsto

- **Docker Compose + lanzador Bash** (`scripts/playground.sh`): ciclo de vida
  explícito `up`, `status`, `shell`, `smoke`, `reset` y `down`. El modo
  `shell` ejecutará el verdadero `./vcli` en una terminal interactiva; se
  documentará también cómo invocarlo en modo batch.
- **Vault HashiCorp real con versión fijada**, inicialmente 2.1.1; modo
  desarrollo **efímero**, TLS nativo con CA generada para cada entorno,
  escucha publicada exclusivamente en `127.0.0.1` y nombres/redes de
  contenedor dedicados. Sin exposición pública ni conexiones a Vault de
  producción. Posibilidad futura de seleccionar otras versiones de la matriz.
- **Bootstrap idempotente de datos ficticios**: KV v1, KV v2 y mount KV v2
  anidado; secretos de ejemplo, política de solo lectura, token limitado y
  AppRole para probar tanto operaciones autorizadas como errores 403/404.
  Reutilizar, donde sea posible, la preparación del fixture de
  `tests/integration/run.sh` evitando una segunda definición divergente.
- **Credenciales y certificados solo locales y temporales**: directorio de
  estado privado con permisos restrictivos (0700, archivos sensibles 0600),
  ignorado por Git; nada de secretos reales, credenciales permanentes, CA de
  usuario ni tokens en logs o archivos versionados. `down` y `reset`
  limpiarán los artefactos generados, también si falla el arranque.
- **Guía práctica de ejercicios**: `ls`, `cat`, `put`, `cp`, `mv`,
  navegación/REPL, KV v2, ACL/AppRole y exportación de certificados/keytabs
  *sintéticos*, además de TLS válido/CA inválida y recuperación ante
  reinicio. Incluir comandos de inspección del estado desde la API de Vault.
- **Separación del CI**: funcionamiento local voluntario; un smoke test
  automatizado validará `up → bootstrap → vcli real → verificación por API
  → down` y la limpieza, pero las sesiones exploratorias del usuario no
  se ejecutarán en los runners de CI.

### Criterios de aceptación

1. En un host Linux con Docker/Compose y Bash, una persona puede crear el
   entorno con **un comando** y obtener instrucciones de conexión, sin
   preparar Vault manualmente ni reutilizar tokens externos.
2. El `vcli` original modernizado se conecta con validación TLS habilitada;
   las operaciones de ejemplo se corroboran mediante la API, no solo por el
   texto de salida del CLI.
3. Se prueba un token de lectura: puede leer, pero **no** modificar secretos.
   Una CA desconocida y un token inválido deben fallar sin filtrar secretos.
4. `reset` es repetible y `down` elimina los contenedores, los datos
   sintéticos y los archivos temporales con permisos seguros.
5. README con requisitos, comandos y troubleshooting; smoke test integrado
   en CI antes del RC. El playground no modifica `vcli.orig.sh` ni requiere
   Python como implementación de `vcli`.

**Orden de trabajo:** completar la fase 04 de portabilidad y robustez,
implementar el playground como hito previo al release candidate y utilizarlo
en la aceptación manual del CLI antes de cerrar la PR. Posteriormente podrá
ampliarse para experimentar con blueprints YAML, AppRoles y rotación, pero
esas funciones quedan fuera del primer entregable.

## Plan de integración

Una vez estabilizados los contratos y las pruebas, completar el
playground local con Vault TLS real y su smoke test de aceptación manual.
Solo después, empaquetar `vcli` como ejecutable distribuido sin alterar
`vcli.orig.sh`. Actualizar instalación y release al cerrar la fase.
