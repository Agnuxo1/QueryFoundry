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
revisionbfb2b44 con guard de conteos; memoria1,863/1,926GiB. Huella posterior por medir.

Baseline/candidato sin recibos: 14,245/13,239 s. Segunda serie: original sin
recibos 13,868 s frente a candidato con recibos 12,911 s. n=3, exploratorio,
rangos solapados. Los cambios posteriores deben medirse de nuevo.
Métrica MEASURED PROCESSING TOTAL local; puntuación oficial nula.
Memoria de contenedor aproximadamente 633–647 MiB. Pico de disco temporal desconocido.

Antes de entregar:

1. Verificar fecha exacta de cierre y condiciones de acceso/participación.
2. Medir escalas mayores de forma gradual; reservar recursos antes de cargas largas.
3. Ampliar la matriz de dumps/restauración/reinicios/concurrencia a otros entornos.
4. Medir disco temporal y recursos con mayor resolución.
5. Sincronizar revisión verificada en el repositorio público y enviar el Writeup en Kaggle.

Repositorio público verificado: https://github.com/Agnuxo1/QueryFoundry;
remote main observado88ce591, con revisiones posteriores locales por sincronizar.
No hay entrega Kaggle aceptada. No se reclama
victoria, ranking ni capacidad a 300 millones. La recuperación precommit sigue
repitiendo la transacción completa; los bloques durables son trabajo futuro.
