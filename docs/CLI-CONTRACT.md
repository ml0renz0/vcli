# Contrato CLI de VCLI revival (v0.7) — test-first

Este documento define la interfaz que **un usuario de VCLI** puede observar.
Los tests no comprueban el nombre de una función interna ni si se usó
`eval`, sino el efecto real del ejecutable.

## Superficies

| Área | Entrada | Resultado/contrato |
|---|---|---|
| Version | `--version` sin Vault ni token | exit 0, versión en stdout, sin conexión externa |
| Ayuda | `-h` sin Vault ni token | exit 0, ayuda utilizable |
| Config | `VAULT_ADDR`, `VAULT_TOKEN` | prioridad de entorno documentada, errores claros |
| Seguridad TLS | `VAULT_CACERT` | CA propia válida acepta, ausente/no válida rechaza, sin `-k` implícito |
| Modo batch | `-n` + comandos por stdin | no requiere TTY, termina al EOF, éxito=0, fallo≠0 |
| TTY | prompt, `help`, `exit`, Ctrl+D | prompt usable, sin cuelgues, historial privado |
| KV v1 | `put/cat/ls/rm` | estado observado por REST API real de Vault |
| KV v2 | `put/cat/ls/rm` | formato `data`, `metadata`, borrado lógico, versionado |
| Montajes | KV v1, v2, v2 anidado | rutas sin ambigüedad ni datos fuera del montaje |
| Escrituras compuestas | `addsecret/delsecret/cp/mv` | estado final correcto, sin corrupción |
| Autorización | solo lectura, token inválido | 403/denegación, nunca mutar ni imprimir token |
| Administración | `lspol/showpol/lsrole/showrole` | políticas y AppRoles obtenidos de Vault real |
| Privacidad | comandos con secretos | no queda plaintext en historial, logs, argv o temporales persistentes |

## Matriz de tests

1. **Sin servidor:** versión, ayuda, flags inválidos, ausencia de credenciales,
   salida y no fuga de tokens.
2. **Vault HTTPS real:** KV1/KV2, montajes anidados, roles, policies, permisos,
   no mutación al inicio, salidas y códigos de error.
3. **TLS negativo:** CA ausente/incorrecta; positivo: CA correcta;
   opt-out explícito. Pendientes: SAN incorrecta, certificados expirados, mTLS.
4. **TTY:** abrir, prompt, ayuda, salir; ampliar a Tab/flechas/Control-R.
5. **Destructivo:** backup/restore, cptree/rmtree, límites de ruta;
   antes de declarar v0.7 estable.
6. **Resiliencia:** errores 403/404/5xx, timeouts, Vault sellado,
   fallos de conexión, concurrencia/CAS.

## Reglas de avance

- Diseñar primero el comportamiento esperado y añadir el test reproducible.
- Ejecutar rojo sobre versión antigua cuando sea posible (*red*).
- Implementar en **`vcli-revival.sh`**, no en un patcher (*green*).
- Refactorizar manteniendo la suite verde.
- No mover el código probado a `vcli` ni publicar release mientras queden
  gaps bloqueantes descritos en `docs/CI-CD.md`.

Los tests Python en `tests/cli` son **test harness**, no transformación
del código de producción: VCLI sigue siendo Bash.
