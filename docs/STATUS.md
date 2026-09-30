# Estado factual — 2026-09-30

Proyecto local preparado con licencia, CI, versiones fijadas, laboratorio,
launcher GUI, informes originales, hashes y borrador de Writeup.
Docker/WSL reparados; datos existentes conservados en E:.

Verificado: 32 archivos oficiales intactos; seis tests upstream y cuatro nuevos;
generador oficial 100.000 filas; tres repeticiones GUI/SSH/Linux por variante;
equivalencia de seis tablas; recuperación de expansión y finalización mediante
recibos transaccionales; fallo precommit, respuestas perdidas y reintentos.

Baseline/candidato sin recibos: 14,245/13,239 s. Con recuperación:
13,868/12,911 s. Métrica MEASURED PROCESSING TOTAL local; puntuación oficial nula.
Memoria de contenedor aproximadamente 633–647 MiB. Pico de disco temporal desconocido.

Antes de entregar:

1. Verificar fecha exacta de cierre y condiciones de acceso/participación.
2. Medir escalas mayores de forma gradual; reservar recursos antes de cargas largas.
3. Verificar dumps, restauración, reinicio de servidor y peticiones concurrentes.
4. Medir disco temporal y recursos con mayor resolución.
5. Publicar un repositorio público, fijar su commit y enviar el Writeup en Kaggle.

No hay repositorio GitHub publicado ni entrega Kaggle aceptada. No se reclama
victoria, ranking ni capacidad a 300 millones. La recuperación precommit sigue
repitiendo la transacción completa; los bloques durables son trabajo futuro.
