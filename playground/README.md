# Playground VCLI: Vault real con datos sintéticos

Este laboratorio inicia HashiCorp Vault 2.1.1 en modo dev, con TLS **nativo**
y CA generada automáticamente. Publica únicamente un puerto efímero de
127.0.0.1. El cliente siempre es el verdadero ejecutable Bash **./vcli**.

Inspirado en el ciclo de vida y las precauciones del playground de
[Secretwright](https://github.com/ml0renz0/secretwright), pero **independiente
de Python y Go**. El contenedor no está endurecido para producción.

## Arranque

Requiere Linux, Docker local con Compose v2, Bash, curl, jq, openssl,
klist (MIT Kerberos), base64 y GNU coreutils. El usuario debe tener permisos
sobre el socket Docker local (equivalente a una cuenta privilegiada).

    scripts/playground.sh up
    scripts/playground.sh status
    scripts/playground.sh smoke
    scripts/playground.sh shell

Para lanzar el CLI directamente:

    source .playground/env.sh
    ./vcli

Para abrirlo con privilegios limitados:

    scripts/playground.sh shell readonly

## Catálogo de secretos precargados

Todos los valores son **ficticios** y se escriben en el Vault real.

| Tipo | Ruta de Vault | Pruebas de VCLI |
| --- | --- | --- |
| KV v1, usuario/password | kv1/demo/app | cat, ls, put, addsecret, delsecret |
| KV v1, JSON anidado, booleanos y arrays | kv1/demo/nested/config | cat, tree, find, cp |
| KV v2, dos versiones de credencial ficticia | kv2/demo/payments/database | cat, put, ls, rm, backup |
| KV v2, clave API de mentira | kv2/demo/catalog/api | cat, cptree |
| KV v2, montaje independiente y anidado | team/config/demo/payments | cat, put |
| X.509 RSA: certificado, SAN y clave PEM | kv1/demo/pki/web | certinfo, getsan, getcert, getcertlist |
| X.509: certificado que vence en tres días | kv1/demo/pki/nested/service | getcertexpiration, getallcertlist |
| MIT Kerberos v2 AES256, principal HTTP | kv1/demo/kerberos/http | krbinfo, getprinc, getkrb, getkrblist |
| MIT Kerberos, estructura recursiva | kv1/demo/kerberos/nested/http | getallkrblist |
| X.509 inválido, clave no coincidente, par ausente | kv1/negative/pki/* | rechazo de getcert y certinfo |
| Kerberos Base64 inválido, principal inseguro | kv1/negative/kerberos/* | rechazo de getkrb |
| Política de solo lectura | vcli-playground-read | lspol, showpol y escritura denegada |
| AppRole lector | vcli-playground-reader | lsrole, showrole, auth AppRole |

### Ruta de ejemplo para una sesión interactiva

    ls kv1/demo/
    tree kv1/demo/
    cat kv1/demo/app
    cat kv2/demo/payments/database
    cat team/config/demo/payments
    certinfo kv1/demo/pki/web
    getsan kv1/demo/pki/web
    getallcertlist kv1/demo/pki
    getcertexpiration kv1/demo/pki/nested/service
    krbinfo kv1/demo/kerberos/http
    getprinc kv1/demo/kerberos/http
    getallkrblist kv1/demo/kerberos
    getcert kv1/demo/pki/web
    getkrb kv1/demo/kerberos/http

Los comandos getcert/getkrb exportan a .playground/work/ usando permisos
0600 y sin sobrescribir ficheros existentes. Los originales generados
están en .playground/fixtures/ (privados y excluidos de Git).

**Límite Kerberos:** el keytab contiene una entrada MIT v2 válida para
klist, pero su clave es pública, fija y sintética. **No hay KDC** y
no puede usarse para demostrar autenticación Kerberos con kinit.

Los formatos cubiertos son los que gestiona actualmente el VCLI Bash
revival. Blueprints YAML, reconciliación declarativa y rotación pertenecen
a fases futuras de VCLI Forge, no a esta entrega.

## ACL y AppRole

    scripts/playground.sh shell readonly
    printf 'cat kv1/demo/app\n' | scripts/playground.sh batch readonly
    printf 'put kv1/demo/app {"x":"denied"}\n' | scripts/playground.sh batch readonly

El último comando debe fallar. Se generan archivos privados readonly-token,
role-id y secret-id para experimentar también con AppRole.

## Conservación, reinicio y borrado

    scripts/playground.sh up       # repetible; conserva los cambios
    scripts/playground.sh smoke    # TLS, KV, ACL, PKI y keytab por API/CLI real
    scripts/playground.sh reset    # DESTRUCTIVO: destruye y recrea el catálogo
    scripts/playground.sh down     # contenedor, red, CA, tokens y datos

El archivo .playground/env.sh no guarda tokens en claro: al cargarlo en Bash
lee el token del archivo privado. Desactiva los VAULT_* de playground antes
de cambiar a cualquier otro entorno.

El directorio .playground/ (0700) y sus archivos (0600) se ignoran por Git.
El launcher comprueba el propietario del directorio, una etiqueta aleatoria
del contenedor y el proyecto Compose antes de actuar. No acepta daemons
Docker remotos. Nunca se conecta a un Vault externo.

Si Docker falla durante el borrado, los archivos de propiedad se conservan
para permitir reintentar down. No borres manualmente .playground/ mientras
haya un contenedor asociado.
