# QueryFoundry — borrador de entrega

Estado: resultados locales reproducibles; pendiente de publicación y envío.
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

La comparación GUI independiente con recuperación, tres repeticiones por
variante, produjo medianas de 13,868 s original y 12,911 s candidato (6,9% menos).
Picos de working set: 633 y 643 MiB respectivamente. No interpretar la diferencia
entre esta serie y la anterior como coste negativo de recuperación: existe
variación de tiempos y ambas tienen sus propios baselines.

## Reproducción y límites

Seguir README.md y usar el commit indicado. Imagen base PostgreSQL fijada por
digest, versiones Python registradas, origen oficial fijado y clientes con
registro de descarga y hash. El laboratorio tiene claves propias y puertos
loopback; no se incluyen credenciales en la entrega.

Falta ampliar escala, medir disco temporal y verificar restauración de dumps,
reinicio del servidor y concurrencia. La transacción única puede requerir
repetir mucho trabajo si falla antes del commit. Las mejoras pequeñas requieren
más repeticiones y hardware independiente para confirmar estabilidad.
