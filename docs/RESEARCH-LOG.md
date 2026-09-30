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

Próximo experimento: un millón de filas del generador oficial en un volumen
independiente, dos CPU, dos GiB, recorrido GUI completo original/candidato.
Primera pareja como piloto de capacidad; n=1 no demuestra una ganancia estable.
Registrar memoria, archivos temporales muestreados y temp_bytes escritos; no
confundir tamaños muestreados con máximos garantizados ni bytes escritos con pico.
