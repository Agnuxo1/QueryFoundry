# QueryFoundry

Proyecto dedicado a [Python Database Performance Optimization](https://www.kaggle.com/competitions/python-database-performance-optimization).
Cliente Windows, PostgreSQL Linux y SSH. Generador y aplicación oficial
conservados byte a byte en `app/`; extensiones en `dbperf/`.

## Evidencia inicial

100.000 filas del generador oficial, PostgreSQL 17 Linux, dos CPU y límite de
2 GiB. Tres repeticiones alternadas del recorrido completo de la GUI Windows,
incluyendo validación, versiones y actualización de interfaz:

| Variante | Mediana MEASURED PROCESSING TOTAL |
|---|---:|
| Original | 14,245 s |
| JSON reutilizado por fila | 13,239 s |

Reducción local del 7,1%. Las seis tablas son equivalentes con `EXCEPT ALL`
bidireccional, normalizando identidades sustitutas y conservando relaciones.
Estos números corresponden a la optimización sin recibos adicionales. Informes
en `reports/gui-benchmark.json`; recuperación en `reports/durable-gui/`.
No hay puntuación oficial ni capacidad demostrada a 300 millones de filas.

La comparación independiente de tres repeticiones dio 13,868 s para el original
sin recibos y 12,911 s para QueryFoundry con recibos: diferencia local del 6,9%.
Pico de working set observado: 633 MiB frente a 643 MiB. Son mediciones locales
de series distintas; no permiten atribuir una diferencia entre ambas series al
coste de recuperación. El pico de disco temporal todavía no está medido.
Con n=3 y rangos solapados, las diferencias son exploratorias: no demuestran
significancia estadística. Los parches de investigación posteriores requieren
una nueva comparación y no heredan estas cifras como resultado propio.

## Implementación

`payload_once` extrae `raw->'values'` una vez por fila mediante `LATERAL` con
barrera `OFFSET 0`. Mantiene casts, filtros, orden, validaciones y transacciones;
no materializa toda la entrada. Sólo transforma las seis recetas oficiales
conocidas. Las recetas personalizadas conservan su SQL original.

`--recovery` añade recibos durables en las bases de trabajo y control. Recupera
commits confirmados y completa versiones pendientes sin duplicar filas.
No tiene checkpoints por bloques. Véase [RECOVERY.md](docs/RECOVERY.md).

## Instalación y GUI

Python 3.13 fue la versión usada en Windows. Desde esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe scripts/verify_upstream.py
.\.venv\Scripts\python.exe launch.py
```

`--baseline` usa recetas originales; `--recovery` activa recibos.
Configurar conexiones en la GUI original. No guardar contraseñas en Git.
`--resume-job UUID` recupera una operación según RECOVERY.md.

## Laboratorio

Docker Desktop debe usar contenedores Linux. En este equipo su disco está en E:;
proyecto, entornos y descargas están en D:. Sólo publica SSH en
`127.0.0.1:55222`, con volumen propio y comprobación estricta de host.

```powershell
.\.venv\Scripts\python.exe scripts/download_postgres.py
.\.venv\Scripts\python.exe scripts/docker_lab.py prepare
.\.venv\Scripts\python.exe scripts/docker_lab.py build
.\.venv\Scripts\python.exe scripts/docker_lab.py start
.\.venv\Scripts\python.exe scripts/docker_lab.py generate --rows 100000
.\.venv\Scripts\python.exe scripts/benchmark_gui.py --repeats 3
.\.venv\Scripts\python.exe scripts/test_durable_pipeline.py
.\.venv\Scripts\python.exe scripts/benchmark_gui.py --durable --repeats 3
.\.venv\Scripts\python.exe scripts/docker_lab.py stop
```

El generador crea la base de pruebas; usarlo sólo en el laboratorio. Los
benchmarks vacían las seis salidas únicamente del contenedor etiquetado
`queryfoundry`. Claves y contraseñas se generan en `.runtime/`, ignorado por Git.
La descarga de herramientas cliente registra origen y SHA-256.

## Verificación y entrega

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd app
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

[RULES.md](docs/RULES.md): contrato verificado.
[REUSE.md](docs/REUSE.md): métodos de proyectos locales y GitHub.
[WRITEUP.md](docs/WRITEUP.md): borrador de entrega.
Antes de enviar: verificar fecha exacta y elegibilidad, ampliar escala y recursos,
probar dumps y fallos restantes, publicar repositorio y fijar commit en Kaggle.
El repositorio local todavía no constituye una entrega aceptada.

Apache-2.0. Origen y hashes en `docs/upstream-manifest.json` y NOTICE.

## Investigación posterior

Piloto1M: original155,491s/candidato145,662s, seis tablas equivalentes, una pareja
exploratoria; memoria muestreada1,863/1,926GiB. Código medido bfb2b44, con guard de
conteos. Una huella posterior añade comprobación de contenido en recuperación,
vinculada a versión/locale y no criptográfica; requiere su propia medición.
Concurrencia, reinicio, dump/restore completo y alteraciones de salida están
probados en el laboratorio. Un prototipo de posiciones dispersas se descartó
por aceptar pesos malformados que el original rechaza. Resultados y límites en
[RESEARCH-LOG.md](docs/RESEARCH-LOG.md).

## Trabajo compartido con Claude y JEV

[Tablón](coordinacion/TABLON.md), [agenda por horas](coordinacion/AGENDA.md),
[tareas](coordinacion/TAREAS.md), [cola de investigación](coordinacion/COLA-DE-INVESTIGACION.md)
y [ThinkTank](coordinacion/THINKTANK.md). Incorporar a Claude con
[PROMPT_CLAUDE.md](coordinacion/PROMPT_CLAUDE.md). Las asignaciones iniciales
son propuestas; registrar aceptación y recursos antes de ejecutar.
