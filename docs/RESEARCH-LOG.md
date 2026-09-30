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
