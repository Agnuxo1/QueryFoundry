# Asignación y seguimiento de tareas

Asignaciones propuestas el 2026-09-30; Claude debe aceptarlas explícitamente.
Estados: PROPUESTA → ACEPTADA → EN_CURSO → EN_REVISION → COMPLETADA;
alternativas BLOQUEADA y DESCARTADA. Una tarea activa tiene un único propietario.

| ID | Prioridad | Responsable propuesto | Tarea | Archivos/alcance | Aceptación | Estado |
|---|---|---|---|---|---|---|
| T-001 | P0 | Claude | Auditoría independiente de recuperación y versiones | Lectura dbperf/, app/ y reports/; escribir coordinacion/entregas/T-001-claude.md | Hallazgos con líneas, escenario, impacto y reproducción; separar fallos de hipótesis | EN_REVISION — Claude, entrega `entregas/T-001-claude.md` 2026-09-30 11:55; pendiente de revisión de Codex |
| T-002 | P0 | Codex | Verificar reglas, fecha exacta y acceso a entrega | docs/RULES.md y STATUS.md | Fuentes oficiales fechadas; distinguir acceso, participación y envío | PROPUESTA |
| T-003 | P0 | Claude | Diseñar pruebas de concurrencia, reinicio y dumps | coordinacion/entregas/T-003-plan.md; tests nuevos acordados | Fallos reales definidos, efectos/versiones esperados, limpieza limitada al lab | PROPUESTA |
| T-004 | P0 | Codex | Ejecutar y verificar T-003; integrar correcciones revisadas | scripts/ y reports/; Codex propietario de dbperf/ | Sin duplicados; seis tablas y versiones consistentes; dump restaurable; coste contabilizado | EN_CURSO — gates pasados; nueva huella rápida en validación |
| T-005 | P1 | Codex | Piloto de escala y recursos | Laboratorio reservado; reports/scale-1000000/ | Entrada oficial; GUI completa; CPU/RAM/disco y equivalencia; límites explícitos | COMPLETADA — 1M, n1 exploratorio, seis tablas equivalentes; commit bfb2b44 |
| T-006 | P1 | Claude | Investigar una optimización de la cola | Diseño en entregas/; dbperf/ cuando tenga propiedad acordada | Hipótesis ligada a subetapa medida; propuesta mínima con coste y riesgos | PROPUESTA |
| T-007 | P1 | Codex | Comparar candidato de T-006 y cerrar entrega técnica | reports/, docs/WRITEUP.md y STATUS.md | KEEP/REJECT/INCONCLUSO; commit reproducible; ningún resultado inventado | PROPUESTA |

## Propiedad de archivos

Claude escribe inicialmente sólo sus informes bajo `coordinacion/entregas/`
y entradas propias en el tablón/ThinkTank. Codex integra y mantiene scripts,
informes y documentación de entrega. Modificar `dbperf/` requiere registrar
antes tarea y propietario; quien mide fija el commit y congela los archivos.
`app/` y el generador oficial se preservan. No tocar claves de `.runtime/`.

Leer el estado actual de Git antes de editar. No resetear, sobreescribir ni
revertir cambios ajenos. Las ediciones simultáneas del tablón se serializan;
si hay disputa, escribir un informe separado y enlazarlo después.

## Entrega de tarea

Autor, fecha Madrid, ID, commit inspeccionado, archivos cambiados, método,
resultados, límites, comandos de reproducción y siguiente acción. Sólo marcar
COMPLETADA tras satisfacer el criterio; un informe sin ejecución no es una prueba.
