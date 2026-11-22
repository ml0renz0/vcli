# Informes medidos de vcli

Los SVG iniciales proceden de los jobs de GitHub Actions #45027729938 y #45027729945, ambos SUCCESS.
Cada barra representa tiempo de pared del job, no tiempo de ejecución de un comando.
Las grabaciones `.cast` y las métricas SaaS están disponibles en los Artifacts de esas ejecuciones.

## Publicación automática

El workflow `.github/workflows/publish-reports.yml` se activa desde la rama
principal después del merge, mediante `workflow_run` de dos suites existentes.
Solo consume ejecuciones correctas sobre `master`, usa código del checkout
confiable de `master`, copia los cast verificados y genera SVG a partir de los
JSON originales. **No repite pruebas ni altera sus tiempos.**

Los commits automáticos incluyen `[skip ci]` para evitar un bucle de ejecuciones.
Su activación depende de que este workflow llegue a la rama por defecto y de
que GitHub Actions tenga autorización para escribir en `master`. Si la
protección de rama impide esa escritura, el job fallará sin publicar datos
falsos; deberá elegirse entonces una rama de informes con permisos adecuados.

Para ver el reproductor, configurar Settings → Pages → Deploy from branch →
`master` → `/docs`. La página está en
`docs/asciinema-player.html`; el README solo puede enlazarla, ya que GitHub
filtra los scripts/iframes en Markdown.
