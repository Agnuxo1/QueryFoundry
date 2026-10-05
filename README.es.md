# QueryFoundry

Proyecto dedicado a [Python Database Performance Optimization](https://www.kaggle.com/competitions/python-database-performance-optimization).
Cliente Windows, PostgreSQL Linux y SSH. Generador y aplicación oficial
conservados byte a byte en `app/`; extensiones en `dbperf/`.

**Kaggle: Submitted, 5 October 2026.** [View the official writeup](https://www.kaggle.com/competitions/python-database-performance-optimization/writeups/queryfoundry-verifiable-jsonb-expansion-and-durab). Exact submitted source: `32c283f32fe35e9b5bd0e538bd6631f305863fb3`. [Verified receipt](reports/kaggle-submission.json). Source ZIP, PDF report and SHA-256 manifest are attached and their public downloads verified. No official score or rank has been assigned.


## Release evidence - 5 October 2026

[Consolidated report](docs/WRITEUP.md) and [reproduction guide](docs/REPRODUCIBILITY.md). Final 100k, three-round GUI/SSH/Linux medians: original **16.046 s**, payload_once with recovery **15.447 s**, experimental lazy_keys **12.792 s**; all nine runs passed six-table equivalence. These are local exploratory observations. Figures and animated charts below describe earlier historical series.

Payload_once remains the primary profile. Independent planner tests exposed two unresolved multi-error SQLSTATE differences in lazy_keys; it remains opt-in. Actual SSH recovery passed at three transaction boundaries. The formal Kaggle receipt records submitted state and exact code separately; no official score or rank is claimed.


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

Huella reforzada: benchmark GUI n3 a100k, original 15,875 s /candidato 15,094 s,
reducción local 4,920% con rangos solapados. Código c0c7e27;
reports/research-fingerprint-100k/durable-gui/. Sin significancia demostrada.

## I-005: resultados GUI100k verificados

Commit3c62b85, tres rondas rotatorias original/payload_once/lazy_keys.
Medianas15,588055791 /15,56720543 /12,34199933s. Candidatos con recuperación
reforzada; original sin recibos. Lazy reduce20,824% frente al original y20,718%
frente a payload. Las seis tablas coinciden en las9ejecuciones. Todos los
resultados conservados: primera original25,973s, dominada por table1, causa
no establecida. Rangos original15,085–25,973, payload15,154–15,792, lazy12,142–12,422s.
N3exploratorio; no significancia ni puntuación oficial. La pequeña mejora de
payload de series anteriores no se reproduce aquí. Table2 mediana6,741/5,734/3,423s.
Linux:800fuzz lazy con219errores y150payload con19errores, idénticos al original.
El control sparse produce la discrepancia esperada. Defaultpayload conservado;
lazyoptativo; falta concluir piloto1M con esta implementación.

## I-005: piloto1M aceptado y decisión

Datos oficiales preservados, runtime3c62b85 equivalente a9e83eb5 (diferencia sólo
documentación/informes en los paths medidos); los tres registros usan9e83eb5.
Una ejecución por variante: original164,108612678 /payload152,427291575 /
lazy126,771885872s. Seis tablas equivalentes;5.321.553filas insertadas por variante.
Reducción lazy22,751% original y16,831% payload. Table2:73,052 /60,071 /35,405s.
Working set muestreado1,779 /1,763 /1,826GiB bajo límite2GiB. Archivos de trabajo
PostgreSQL observados57,275MiB original y0 candidatos; escritos136,518MiB /0 /0.
No incluye tablasTEMP, WAL y todos los intermediarios; pico global desconocido.
RAM libre del host cayó transitoriamente a1,04GiB; no es medición exhaustiva.
A esta escala se repitieron los8contraejemplos conocidos: salidas/SQLSTATE
coincidentes. N1exploratorio, sin significancia ni puntuación oficial.
JEV connected/provenancejev/modeljev-1.13.0 recomienda lazy optativo (confianza1),
réplica independiente de recuperación como prioridad siguiente (confianza0,31).
Decisión: KEEP como candidato de investigación optativo; defaultpayload conservado.
No afirmamos equivalencia de errores en todos los planes/versiones.
RES006liberada; ambos laboratorios detenidos y datasets preservados.
