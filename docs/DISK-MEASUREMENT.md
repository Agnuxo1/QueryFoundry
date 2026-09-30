# Medición del disco intermedio

## Estado

El pico global sigue sin medirse. Los resultados publicados de 100.000 y un
millón de filas sólo observaron archivos de trabajo de `pgsql_tmp`. El contador
`pg_stat_database.temp_bytes` mide bytes escritos acumulados, no espacio máximo
simultáneo, y no cubre todos los intermediarios.

El nuevo monitor está implementado y probado con fixtures locales. Su calibración
con PostgreSQL Linux real está pendiente de liberar la reserva de Claude T-003.
No se han recalculado ni cambiado las cifras históricas.

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
excluyen del máximo aceptado. Los tamaños observados siguen siendo cotas
inferiores, incluso cuando todos los recorridos terminan correctamente.

## Qué falta para evaluar recursos completos

El monitor no incluye recibos persistentes, cachés o staging del cliente, WAL ni
el aprovisionamiento del volumen. Los destinos finales deben separarse conforme
a la regla oficial. WAL generado acumulado tampoco equivale a pico adicional.
`global_intermediate_disk_peak_bytes` seguirá siendo `null` mientras esta
cobertura sea incompleta.

Antes de usarlo en una nueva serie: calibrar con una tabla TEMP mantenida en una
transacción, TOAST, un tablespace y un spill controlado; comprobar tamaños
lógicos/asignados y limpieza. Medir también el coste del propio monitor.
Después, ejecutar una serie nueva equilibrada, con fuente oficial intacta,
seis tablas equivalentes y reserva exclusiva. No mezclar tiempos antiguos con
los de una instrumentación distinta.

Implementación: `scripts/resource_sampling.py`; integración de laboratorio:
`scripts/benchmark_gui.py`; pruebas: `tests/test_resource_sampling.py`.
