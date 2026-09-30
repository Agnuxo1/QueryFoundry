# Estado factual — 2026-09-30

Proyecto local preparado con licencia, CI, versiones fijadas, laboratorio,
launcher GUI, informes originales, hashes y borrador de Writeup.
Docker/WSL reparados; datos existentes conservados en E:.

Verificado: 32 archivos oficiales intactos; seis tests upstream y seis nuevos;
generador oficial 100.000 filas; tres repeticiones GUI/SSH/Linux por variante;
equivalencia de seis tablas; recuperación de expansión y finalización mediante
recibos transaccionales; fallo precommit, respuestas perdidas y reintentos.
Investigación: concurrencia, reinicio y dump/restore de ambas bases con reanudación;
rechazo tras borrado y modificación de contenido con igual conteo. Huellas tipadas
no criptográficas vinculadas a versión/locale; no soporte de migración automática.
Piloto1M GUI/SSH:155,491/145,662s, una pareja exploratoria, seis tablas equivalentes,
revisionbfb2b44 con guard de conteos; memoria1,863/1,926GiB.
Huella reforzada medida a100k en c0c7e27: original15,875s /candidato15,094s,
tres repeticiones alternadas, reducción local4,920%, rangos solapados.
Original sin recuperación /candidato con recuperación; seis tablas equivalentes
en cada ejecución. Working set muestreado797,223/790,141MiB. No significancia demostrada.

Baseline/candidato sin recibos: 14,245/13,239 s. Segunda serie: original sin
recibos 13,868 s frente a candidato con recibos 12,911 s. n=3, exploratorio,
rangos solapados. Los cambios posteriores deben medirse de nuevo.
Métrica MEASURED PROCESSING TOTAL local; puntuación oficial nula.

I-005, comparación posterior de tres variantes en 3c62b85: a100k y n3,
original15,588s /payload15,567s /lazy_keys12,342s. Salidas equivalentes en las
nueve ejecuciones. Lazy reduce20,824% local; payload queda casi igual al
original en esta serie. Primera original25,973s conservada. Resultado
exploratorio. Linux800fuzz lazy y150payload coincidentes, con control negativo
detectado. Lazy es optativo; piloto1M terminado:164,109 /152,427 /126,772s, n1, las seis tablas equivalentes. Working set1,779 /1,763 /1,826GiB; ocho contraejemplos coincidentes. Ambos laboratorios detenidos.
Memoria de contenedor aproximadamente 633–647 MiB. Pico de disco temporal desconocido.

Antes de entregar:

1. Cierre verificado:12oct2026 04:59Madrid; reglas aceptadas, Writeup ausente. Preparar entrega y revalidar antes de enviarla.
2. Medir escalas mayores de forma gradual; reservar recursos antes de cargas largas.
3. Ampliar la matriz de dumps/restauración/reinicios/concurrencia a otros entornos.
4. Medir disco temporal y recursos con mayor resolución.
5. Mantener GitHub actualizado y preparar el envío del Writeup en Kaggle.

Repositorio público verificado: https://github.com/Agnuxo1/QueryFoundry;
revisión técnica y resultados publicados d8a02922e34dcd8abdde64ce6187e2a061eb735d,
confirmados con git ls-remote tras aprobación directa del usuario. Las revisiones
documentales posteriores se consultan en HEAD. El usuario prioriza mantener este
repositorio actualizado; no hace falta repetir autorización para sincronizar el proyecto.
No hay entrega Kaggle aceptada. No se reclama
victoria, ranking ni capacidad a 300 millones. La recuperación precommit sigue
repitiendo la transacción completa; los bloques durables son trabajo futuro.

R003 clone64: raw_hash alterado rechazado; JSON alterado con raw_hash conservado
aceptado. El manifiesto valida hashes almacenados, no recalcula el contenido JSON.
Fuentes y destinos oficiales no modificados por este experimento funcional.

Última ronda: T002 y R003 completados; T006 de Claude revisada, T007 completada
como KEEP optativo de lazy. Pendientes réplica independiente de recuperación y
planes, coste de huella JSON fuente, disco intermedio completo y checkpoints.
