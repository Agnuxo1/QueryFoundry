# Medición del disco intermedio

## Estado

El pico global sigue sin medirse. Los resultados publicados de 100.000 y un
millón de filas sólo observaron archivos de trabajo de `pgsql_tmp`. El contador
`pg_stat_database.temp_bytes` mide bytes escritos acumulados, no espacio máximo
simultáneo, y no cubre todos los intermediarios.

El nuevo monitor está implementado, probado con fixtures locales y calibrado
con PostgreSQL Linux real en una base desechable. No se han recalculado ni
cambiado las cifras históricas.

La calibración comparó tres observaciones con `pg_total_relation_size` de la
sesión propietaria: 14.647.296 bytes de tablas TEMP, TOAST e índices, incluidos
5.914.624 bytes en un tablespace separado. Desde otra sesión las dos tablas
todavía no eran visibles en el catálogo. Un cursor retuvo 9.235.232 bytes de
spill, observados por el monitor y coincidentes con los bytes escritos que
PostgreSQL informó al cerrar la sesión. Después quedaron cero bytes efímeros;
se eliminaron la base, el tablespace y su directorio desechables.

Evidencia: `reports/disk-sampler-linux-calibration.json`. Es una calibración
sintética del instrumento, no una medición del concurso. Los segmentos mayores
de 1 GiB y los filesets paralelos todavía no tienen calibración real; las pruebas
locales cubren su clasificación. El coste del monitor se evalúa en una serie
separada de cuatro pares GUI a 100.000 filas, activado/desactivado, con orden
equilibrado y verificación de las seis tablas.

## Qué observará el nuevo monitor

| Categoría | Identificación | Unidad |
|---|---|---|
| Archivos del ejecutor, incluidas operaciones paralelas | Archivos bajo `pgsql_tmp`, incluidos directorios de filesets compartidos | Tamaño lógico y bloques asignados × 512 bytes |
| Relaciones SQL TEMP, incluidos índices y TOAST | `t<backend>_<relfilenode>`, forks y segmentos dentro de un directorio de base | Tamaño lógico y bloques asignados × 512 bytes |

Se recorren `PGDATA/base` y los tablespaces de `PGDATA/pg_tblspc`, siguiendo sus
enlaces. La identificación por nombre permite observar tablas TEMP de una
transacción que todavía no son visibles desde otra conexión SQL. El recorrido
lee metadatos de archivos, no su contenido.

El máximo combinado se calcula sumando ambas categorías **en cada recorrido**.
No se suman máximos de categorías obtenidos en instantes distintos. Se conserva
el comienzo y fin de cada recorrido, el mayor intervalo observado y los fallos.
La espera solicitada de 0,5 s comienza después de cada llamada; no implica una
frecuencia efectiva de dos muestras por segundo.

Un recorrido no es una instantánea atómica. Puede perder archivos breves o
coincidir con un borrado. Los recorridos incompletos quedan identificados y se
excluyen del máximo aceptado. Puede tanto omitir un pico breve como sumar
estados de archivos que no coexistieron. Por ello el máximo observado no es
una cota inferior matemática garantizada, aunque todos los recorridos terminen
correctamente. Se informa como máximo muestreado con esta incertidumbre.

## Qué falta para evaluar recursos completos

El monitor no incluye recibos persistentes, cachés o staging del cliente, WAL ni
el aprovisionamiento del volumen. Los destinos finales deben separarse conforme
a la regla oficial. WAL generado acumulado tampoco equivale a pico adicional.
`global_intermediate_disk_peak_bytes` seguirá siendo `null` mientras esta
cobertura sea incompleta.

Antes de presentar nuevas cifras: evaluar el coste del propio monitor y sus
límites de cobertura. Ejecutar una serie nueva equilibrada, con fuente oficial intacta,
seis tablas equivalentes y reserva exclusiva. No mezclar tiempos antiguos con
los de una instrumentación distinta.

## Control de instrumentación en GUI

Cuatro pares lazy_keys con recuperación, 100.000 filas oficiales y orden
equilibrado activado/desactivado. Las ocho ejecuciones produjeron las seis
tablas equivalentes al original. Medianas: 14,087 s con monitor y 14,972 s sin
monitor. Cambios de tiempo por par: +379,29%, −8,59%, +16,42%, −8,40%; mediana
de cambios +4,01%. Cuatro pares no permiten concluir un coste fijo o nulo.

El primer registro con monitor (91,441 s) incluye 77,804 s en el INSERT de
table1; se conserva sin excluirlo y su causa sigue sin establecerse. Los cuatro
registros con monitor observaron 164.044.800 bytes efímeros lógicos
(156,445 MiB); el máximo asignado estuvo entre 164.044.800 y 164.048.896 bytes,
sin recorridos fallidos. Es un máximo observado de las categorías
cubiertas, no el pico global. No se mezcla esta serie con tiempos históricos.

Evidencia: `reports/research-disk-100k/monitor-overhead.json` y los ocho informes
GUI JSON/TXT. Los hashes fijan el código medido; el sampler ejecutado coincide
con el commit d6eae56. La revisión posterior sólo aclara texto de metadatos sobre
incertidumbre, sin cambiar selección de archivos ni sumas.

Implementación: `scripts/resource_sampling.py`; integración de laboratorio:
`scripts/benchmark_gui.py`; pruebas: `tests/test_resource_sampling.py`.
