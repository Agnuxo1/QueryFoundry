# Agenda por horas — 2026-09-30, Europe/Madrid

Plan propuesto desde las 11:00. No programa ejecuciones ni envía recordatorios.
Si Claude se incorpora después, desplazar los bloques manteniendo dependencias
y registrar los nuevos intervalos en el tablón. Cada bloque termina con un
resultado escrito; lo pendiente pasa al siguiente bloque sin fingir su cierre.

| Hora Madrid | Responsable propuesto | Trabajo | Resultado verificable | Dependencia/recurso |
|---|---|---|---|---|
| 11:00–12:00 | Claude + Codex | Incorporación, lectura de evidencia y aceptación de tareas | Claude firma T-001; acuerdan archivos y turnos | Lectura, sin cómputo largo |
| 12:00–13:00 | Claude | Auditar recibos, versiones, reintentos y contratos de dump | Informe con hallazgos y reproducciones mínimas | T-001; lectura del código |
| 13:00–14:00 | Codex; JEV consultado | Revisar hallazgos, verificar fecha/reglas y priorizar | Decisión registrada y plan de pruebas | Informe de Claude; fuentes oficiales |
| 14:00–15:00 | Claude | Preparar pruebas acotadas de reinicio y concurrencia | Casos y criterios de aceptación revisables | Sin usar la base compartida hasta reservar |
| 15:00–16:00 | Codex | Ejecutar primero dump/restore y recuperación aprobados | Informes de consistencia y versiones | Reserva exclusiva de laboratorio |
| 16:00–17:00 | Codex; Claude revisa archivos | Piloto a escala mayor si los gates pasan | Tiempo GUI, RAM y disco; o bloqueo justificado | Reserva; capacidad revisada; sin carga paralela |
| 17:00–18:00 | Claude + Codex; JEV consultado | Evaluar una hipótesis prioritaria del ThinkTank | KEEP/REJECT/INCONCLUSO con evidencia | Commit fijo; pruebas y medición necesarias |
| 18:00–19:00 | Codex; Claude revisa | Consolidar informes y borrador de entrega | Checkpoint, commit y lista de pendientes | Pruebas terminadas; no enviar automáticamente |

## Cierre de cada hora

Actualizar TABLON.md y TAREAS.md: resultado, archivos, commit, reserva liberada
y siguiente paso. En trabajo sostenido, checkpoint factual al menos cada
20 minutos y antes de parar. No crear tareas nuevas sólo para llenar el horario.

## Ejecución real

| Bloque | Inicio real | Fin real | Resultado/ruta | Estado |
|---|---|---|---|---|
| Incorporación | 11:40 | 11:49 | TABLON.md, TAREAS.md T-001 EN_CURSO; JEV provenance=jev | HECHO (Claude) |
| T-001 auditoría | 11:49 | 11:55 | coordinacion/entregas/T-001-claude.md (5 hallazgos, 1 reproducido) | EN_REVISION (Claude entrega; Codex revisa) |
