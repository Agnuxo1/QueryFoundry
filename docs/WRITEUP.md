# QueryFoundry — borrador de entrega

Estado: repositorio público; resultados locales reproducibles; sin envío Kaggle aceptado.
El commit exacto se obtiene con `git rev-parse HEAD` cuando se cierre la entrega.

## Problema y solución

La expansión oficial consulta varias veces el mismo subobjeto JSON por fila.
Reutilizamos `raw->'values'` con un LATERAL por receta y una barrera OFFSET 0.
No eliminamos preflight, manifiestos, validación, contadores, versiones ni
refrescos GUI. Se mantiene el orden table3, table4, table1, table5, table6, table2.
La aplicación y generador originales se preservan en app/ con hashes SHA-256.

Una variante de extracción anticipada de todos los campos fue descartada:
su mediana SQL diagnóstica, 15,384 s, fue peor que payload_once, 13,376 s.
Estas mediciones SQL sólo guiaron la selección; no son la métrica de entrega.

## Evaluación

Usamos el generador oficial sin modificaciones, 100.000 filas, PostgreSQL Linux
en Docker, cliente Windows y conexión SSH real. La GUI original ejecutó el
trabajador completo con sus widgets e informe; la ventana estaba oculta durante
la automatización. Se alternaron tres ejecuciones de cada variante.

Original: mediana 14,245 s. Payload reutilizado: 13,239 s. La reducción local
fue 7,1% en MEASURED PROCESSING TOTAL. Los seis informes y sus subetapas están en
reports/. Comprobamos que el agregado coincide con el total del informe original.
La equivalencia de las seis tablas se comprobó con EXCEPT ALL en ambos sentidos,
normalizando claves sustitutas y preservando sus relaciones. Las claves de
raw_data permanecen iguales.

La memoria observada fue aproximadamente 636–647 MiB de working set del
contenedor, incluyendo PostgreSQL y SSH, muestreado con memory.current menos
inactive_file. Límite de 2 GiB y dos CPU. No se midió el pico de disco temporal;
no se presenta esa ausencia como cero. No hay validación a 300 millones de filas
ni puntuación del organizador. Los resultados no permiten prometer posición.

## Recuperación

La opción --recovery confirma un recibo con los efectos en la base de trabajo
y otro con las seis versiones en la base de control. Puede reanudar después del
primer commit sin duplicar datos. No usa una transacción distribuida ni
checkpoints por bloques. La firma liga petición y manifiesto de origen.
Un archivo local conserva sólo el identificador para reanudar, no reemplaza el
recibo PostgreSQL. Se verificaron fallos antes del commit, pérdida de respuesta
tras ambos commits, nueva conexión entre commits y rechazo de UUID reutilizado.
Véanse reports/durable-recovery.json, docs/RECOVERY.md y el benchmark separado
reports/durable-gui/. Todos sus costes se incluyen en ejecuciones nuevas.

La comparación GUI independiente, tres repeticiones por variante, produjo
medianas de 13,868 s original SIN recibos y 12,911 s candidato CON recibos
(diferencia local del 6,9%; rangos solapados, sin prueba de significancia).
Picos de working set: 633 y 643 MiB respectivamente. No interpretar la diferencia
entre esta serie y la anterior como coste negativo de recuperación: existe
variación de tiempos y ambas tienen sus propios baselines.

## Reproducción y límites

Seguir README.md y usar el commit indicado. Imagen base PostgreSQL fijada por
digest, versiones Python registradas, origen oficial fijado y clientes con
registro de descarga y hash. El laboratorio tiene claves propias y puertos
loopback; no se incluyen credenciales en la entrega.

Falta ampliar escala y medir el pico completo de disco intermedio. La transacción única puede requerir
repetir mucho trabajo si falla antes del commit. Las mejoras pequeñas requieren
más repeticiones y hardware independiente para confirmar estabilidad.

## Investigación posterior — 2026-09-30

La auditoría independiente de Claude detectó una instantánea obsoleta al esperar
un lock dentro de REPEATABLE READ, reintentos con UUID distinto y recuperación
que no detectaba alteración de destinos. Codex reprodujo los escenarios y
corrigió la adquisición de lock antes de la instantánea, conservación de UUID
y verificación de destinos. Concurrencia de dos clientes, reinicio real,
dump/restore de ambas bases y reanudación en clones pasaron; también se rechazaron
borrado, actualización con igual conteo y renombrado de columna.

El recibo opcional guarda conteos, dos sumas de hashes de registros, descripción
de columnas y metadatos de versión, codificación y locale. Estas huellas no son
criptográficas ni permiten migración automática entre versiones PostgreSQL.
Los recibos antiguos requieren validación independiente.

En revisión c0c7e272ce56f126e3d42f3eda36b3aa90c90551, tres repeticiones alternadas
de la GUI completa a100k dieron mediana15,875292175s para original SIN recuperación
y15,094171879s para payload_once CON recuperación reforzada. Reducción local4,920%,
rangos15,375–16,698s y14,925–15,837s: solapados, n3, exploratorio. No se aísla el
coste de recuperación comparando con series históricas. Las seis tablas son
equivalentes en cada ejecución. Working set máximo muestreado797,223/790,141MiB.
Evidencia completa: reports/research-fingerprint-100k/durable-gui/.

Piloto1M con generador oficial y revisión anterior bfb2b44: original155,490661388s,
candidato145,661872109s; n1, seis tablas equivalentes, guard anterior de conteos.
Working set1,863/1,926GiB bajo límite2GiB. No extrapolar este piloto a la nueva
huella ni a300M. Temporales PostgreSQL observados43,056MiB original/0 candidato,
escrituras74,687MiB/0; no incluye todos los intermedios, WAL ni tablas TEMP.

Se rechazó sparse_positions antes de medir velocidad:2/8 casos sintéticos
alteraban errores de cast del original. Se conserva sólo como experimento negativo.
JEV, status connected/provenance jev, recomendó conservar el candidato de
investigación y recuperación opcional, sin declarar significancia o victoria.

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
