# Investigación experimental — ronda 1

Fecha: 2026-09-30. Codex ejecuta e integra; Claude aporta auditoría independiente
en coordinacion/entregas/T-001-claude.md; JEV recomienda gates de consistencia
y piloto aislado de capacidad, con provenance=jev verificado.

## Resultados de consistencia

`reports/research-consistency.json` registra experimentos sobre PostgreSQL Linux
con servicios Windows por SSH:

- Dos clientes del mismo UUID: una expansión, un recibo y seis versiones idénticas.
- Reinicio entre commits: reanudación sin cambiar la huella de datos.
- Dump y restauración de ambas bases: conteos y hashes de todas las tablas iguales;
  bajo los nombres originales se reanuda el mismo trabajo y las mismas versiones.
- La receta de restauración debe preservar propietario y permisos de la base;
  el primer fixture falló al crear las bases restauradas con propietario postgres.
- Antes del parche, borrar una fila de table2 en un clon daba éxito falso al
  reintentar. Después, se rechaza por cambio de conteos. El original no fue alterado.
- El guard de duplicados ahora usa bloqueo de sesión antes de REPEATABLE READ;
  se observó el cortocircuito antes de DML en el cliente duplicado. Claude había
  reproducido el fallo de la instantánea previa en un PostgreSQL separado.
- Una carrera local de registro de UUID pudo producir FileNotFoundError con
  temporales compartidos. Temporal único por escritor y test determinista lo corrigen.
- El mismo objeto de servicio reutiliza el UUID pendiente al reintentar la
  misma firma. Tras recrear servicio/proceso sigue siendo necesario --resume-job.

Se conservan los intentos fallidos y el resultado anterior al guard de conteos.
El guard nuevo detecta cambios de conteos, no contenido modificado con el mismo
número de filas. Rechaza también snapshots viejos tras nuevas inserciones y recibos
anteriores sin conteos. No presentarlo como validación criptográfica completa.

## Medición y siguiente gate

Las series históricas n=3 son exploratorias, con rangos solapados. La serie
durable compara original sin recibos con candidato con recibos. Los nuevos
parches no heredan las cifras históricas. Diagnóstico adicional separado por etiqueta.

Piloto completado: un millón de filas oficiales, volumen independiente, dos CPU
y dos GiB, recorrido GUI completo original/candidato con guard de conteos.
Original155,490661388s; candidato145,661872109s; seis tablas equivalentes y
5.321.553 filas insertadas en cada ejecución. n=1 no demuestra ganancia estable.
Código medido bfb2b44; reports/scale-1000000/. Generación138,755s.
Memoria muestreada1,863/1,926GiB; archivos de trabajo temporales43,06/0MiB
observados y74,69/0MiB escritos. Esto NO incluye todas las tablas SQL TEMP,
WAL o intermediarios: el pico global de disco intermedio sigue desconocido.
table2 domina70,281/58,390s; table1 ronda41s. Datos de ambos laboratorios preservados.

## Experimento descartado: posiciones dispersas

Un prototipo filtró posiciones no declaradas antes de extraer peso/tipo/gap.
Preservó el ejemplo oficial, pero2 de8 casos sintéticos cambiaron error SQL por
aceptación cuando el peso era malformado y la declaración vacía/incorrecta.
REJECT antes de medir velocidad. Código sólo en experiments/rejected_sparse_positions.py;
producción restaurada. Contraejemplos en reports/sparse-semantics.json.

## Huellas de contenido: costes y gate

Tres repeticiones SQL diagnósticas sobre las seis salidas100k: mediana0,040755s
para conteos;0,275279s para dos sumas de hashes PostgreSQL tipados;3,371308s para
MD5 de JSON ordenado. Una actualización en copia TEMP mantuvo64 filas pero cambió
ambas huellas. No son métricas GUI ni prueba criptográfica/adversarial.
La variante rápida se vincula a versión, encoding y locale; se prueba reinicio,
restauración con OIDs nuevos, reintento concurrente y cambio de contenido con
igual conteo antes de aceptar la extensión. La primera consulta de locale usó
un parámetro no existente; rollback dejó cero efectos/recibos, y se corrigió
consultando el catálogo pg_database. Los intentos fallidos permanecen guardados.

## Cierre de gates locales de la ronda 1 — 13:37 Madrid

Código medido c0c7e272ce56f126e3d42f3eda36b3aa90c90551. Tres repeticiones GUI
alternadas, 100.000 filas oficiales: original sin recuperación 15,875292175 s;
payload_once con recuperación reforzada 15,094171879 s. Reducción local 4,920%;
rangos 15,375–16,698 y 14,925–15,837 s solapados; resultado exploratorio.
Todas las salidas equivalentes en cada ejecución. Working set muestreado
797,223/790,141 MiB; pico completo de disco intermedio desconocido.
Los ocho gates del servicio pasaron, incluidos restauración con OIDs nuevos,
contenido modificado con igual conteo y renombrado de columna. Doce tests pasan
y los 32 archivos oficiales siguen intactos.
JEV consulta jev-cycle: exit_code0/status connected/provenance jev/model jev-1.13.0;
decisión keep_optional_recovery_and_research_candidate, confianza1. Esto recomienda
la disposición, no certifica rendimiento. Claude aportó auditoría compartida T-001.
RES003 liberada y laboratorio detenido; ambos datasets preservados. Próxima cola:
mutación de origen en clon, diseño seguro table2 y réplica independiente.
No hay envío Kaggle aceptado ni puntuación oficial.

## R-003: límites del manifiesto de origen

Prueba funcional con64filas oficiales copiadas a clones aislados y conexión SSH
real. Cambiar raw_hash después de la captura y antes de BEGIN fue rechazado
sin filas ni recibo. Cambiar numeric_column_1 del JSON manteniendo raw_hash
fue aceptado; el valor alterado llegó a table1. El manifiesto vincula hashes
almacenados, conteos y columnas, pero no recalcula el contenido JSON. No reclamar
integridad frente a escritura externa arbitraria. Estos datos mutados no son
benchmark. Origen original intacto; clones eliminados. Intento inicial falló
al restaurar public sobre el esquema vacío existente; se conserva el informe
y la corrección sólo elimina public del clon antes de restaurar. Evidencia:
reports/source-mutation.json; script reproduce con UUID de clon único.

## I-005: entrega Claude y verificación Linux

Claude entregó lazy_keys para table2: matriz de nombres de campo, extracción
de valores en los mismos filtros/casts y salida. Las otras cinco recetas
usarán payload_once. Default sin cambios; opción --recipe-mode lazy_keys.
Su tiempo Windows es diagnóstico. JEV recomienda Linux semántica antes de GUI.
Fuzz usa semillas nuevas31/41/51 y el prototipo rechazado como control positivo;
comparación GUI equilibrada incluye original y ambos candidatos con sus costes.
Resultados pendientes hasta completar cada gate.

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

## 2026-09-30T19:45:44.189794+02:00: cobertura del monitor de disco

Se prepara un muestreo de archivos SQL TEMP además de pgsql_tmp; incluye
tablespaces, bloques asignados y evidencia de fallos/intervalos. Validación
offline: 3 pruebas específicas, 9 raíz y 6 app aprobadas; 32 archivos originales
intactos. No se ejecuta carga durante RES-CL-03 de Claude. La calibración Linux
y el coste de instrumentación quedan pendientes. El pico global sigue sin
medirse y las cifras históricas no se modifican. Método y exclusiones en
DISK-MEASUREMENT.md; evidencia en reports/disk-sampler-offline-validation.json.
