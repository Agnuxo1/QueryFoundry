# Cola de investigación

Las prioridades se revisan con subetapas medidas y recomendaciones verificadas
de JEV. Abrir una sola investigación experimental por propietario; descartar
pronto cuando falle consistencia, coste o evidencia. No iniciar todas las filas.

| ID | Prioridad | Pregunta | Responsable propuesto | Experimento mínimo | Gate de salida | Estado |
|---|---|---|---|---|---|---|
| R-001 | P0 | ¿El recibo mantiene consistencia con dos peticiones del mismo UUID? | Claude diseña / Codex ejecuta | Dos conexiones, misma petición; variantes antes/después de commit | Una expansión y un lote de seis versiones; petición distinta rechazada | EN_COLA |
| R-002 | P0 | ¿Los recibos sobreviven al reinicio y a dump/restore? | Claude diseña / Codex ejecuta | Reinicio del lab; dump de ambas bases y restauración aislada | Datos, recibos y versiones recuperables sin duplicación | EN_COLA |
| R-003 | P0 | ¿Se detectan cambios de origen entre captura y expansión? | Claude | Mutación controlada de manifiesto con otra conexión | Rechazo seguro sin efectos parciales; límites del raw_hash documentados | EN_COLA |
| R-004 | P1 | ¿Cómo crecen tiempo, memoria, WAL y disco a 1 millón? | Codex | Piloto gradual con generador oficial y base aislada | Métrica GUI, equivalencia y recursos; no extrapolar a 300 millones | EN_COLA |
| R-005 | P1 | ¿La lectura repetida del JSON sigue dominando tras payload_once? | Claude | Revisar subetapas y EXPLAIN en copia del lab | Plan y cuello de botella observado antes de implementar | EN_COLA |
| R-006 | P2 | ¿Un intermedio compartido limitado por bloques mejora las seis recetas? | Claude | Diseño + microexperimento independiente | Ganancia compensa lectura/escritura y mantiene recuperación/relaciones | EN_COLA |
| R-007 | P2 | ¿Checkpoints por bloques pueden preservar dimensiones DISTINCT y versiones? | Claude + revisión Codex/JEV | Especificar claves, instantánea, publicación y reintento; luego prototipo pequeño | Equivalencia y fallos demostrados antes de medir velocidad | EN_COLA |

## Registro de experimentos

`ID · fecha · propietario · hipótesis · commit · filas/seed · reserva · comandos
· informes · comparación · decisión KEEP/REJECT/INCONCLUSO · límites`.

Variar un factor por comparación y usar repeticiones alternadas cuando corresponda.
La métrica válida incluye validación, versiones y GUI. SQL aislado es diagnóstico.
No reutilizar un nombre de informe para ocultar una ejecución peor.
