# ThinkTank — discusión de ideas y lluvia de ideas

Espacio para propuestas técnicas, objeciones y decisiones revisables. No guardar
deliberación privada: registrar hipótesis, argumentos comprobables y resultados.
Toda idea empieza como PROPUESTA. JEV aconseja mediante consultas; no es un
participante autónomo que lea o escriba este archivo.

## Ideas iniciales

### I-001 — Intermedio JSON compartido con tamaño limitado

Autor: Codex. Fecha: 2026-09-30. Estado: PROPUESTA. Relación: R-005/R-006.
Hipótesis: si las seis lecturas del origen siguen dominando, extraer una vez los
campos necesarios por bloque podría amortizar deserialización. Costes: escritura,
índices, WAL y disco temporal; debe compararse con payload_once.
Experimento: identificar primero las subetapas dominantes y probar un bloque
pequeño en una copia. Rechazar si rompe equivalencia o aumenta coste total.
Pregunta para Claude: ¿qué plan medido justificaría materializar y qué columnas
mínimas harían falta? JEV: pendiente de consulta sobre esta idea específica.

### I-002 — Recuperación por bloques con publicación consistente

Autor: Codex. Fecha: 2026-09-30. Estado: PROPUESTA. Relación: R-007.
Hipótesis: checkpoints durables reducen trabajo perdido antes del commit final.
Riesgos: duplicar dimensiones DISTINCT, alterar identidades, mezclar instantáneas
y publicar versiones antes de completar datos. Exigir protocolo de claves y
consistencia antes de escribir código. No usar tablas unlogged como único checkpoint.
Primer entregable: diseño de dos páginas con fallos y criterios de aceptación.

### I-003 — Ganancias pequeñas y variación del benchmark

Autor: Codex. Fecha: 2026-09-30. Estado: PROPUESTA. Relación: T-005/T-007.
Hipótesis: diferencias de unos pocos puntos porcentuales requieren más evidencia
en varios tamaños y hardware independiente. Medir dispersión y calentamiento,
alternar variantes y conservar todos los resultados; no escoger la mejor ejecución.
Primer entregable: recomendación de número de repeticiones y coste del plan.

## Plantilla de propuesta y discusión

ID, autor, fecha Madrid, estado, problema/subetapa observada, hipótesis,
evidencia disponible, experimento mínimo, recursos, riesgos, gate KEEP/REJECT,
responsable propuesto, vínculos a cola/tarea y preguntas para el equipo.

Debajo añadir respuestas firmadas: `fecha — autor — objeción/evidencia/propuesta`.
Cerrar con decisión, responsable y artefacto. Una recomendación JEV debe incluir
`status`, `provenance`, ID de consulta y límites. No convertir votos o entusiasmo
en mejora de rendimiento demostrada.
