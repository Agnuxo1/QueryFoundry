# T-003 — Réplica independiente de recuperación: resultados

Autor: Claude · 2026-09-30 (Europe/Madrid) · Base `64520b1`, recetas `lazy_keys` · RES-CL-03 (19:40–20:25, liberada)
Diseño: `T-003-plan.md`. Código: `t003/harness.py`, `t003/t003_replica.py`. Datos crudos: `t003/t003-results.json`, `t003/run2.log`.
Entorno: PostgreSQL 17.11 Windows propio y desechable, generador oficial 20.000 filas, el `DurableQueryFoundryService` **real**
con el transporte SSH sustituido por `psql.exe` local (mismo SQL). **Límites:** Windows ≠ Linux/SSH del lab; 20k ≠ 100k.
Por eso el punto 3 de Codex (SSH real sobre Linux) **sigue pendiente**: esta réplica no lo cubre.

Resultado: **12/12 escenarios con las invariantes cumplidas** (sin duplicados, sin recibos huérfanos, sin bloqueos ni sesiones
residuales, seis versiones una sola vez). No hay fallos de corrección. Sí hay **3 defectos menores y 5 límites** (abajo).

## Escenarios (antes → después)
| Escenario | Resultado |
|---|---|
| s01 4 clientes, mismo UUID | 1 cliente ejecuta DML, 3 recuperan; filas = esperado; 1 recibo por base; 6 versiones; 0 bloqueos |
| s02 2 UUID distintos | gana uno; el otro `could not serialize access… pgdm_table_row_counts`; sin efectos parciales ni recibo |
| s03 `pg_terminate_backend` a mitad | 0 filas, 0 recibos, 0 bloqueos; reintento completa |
| s04a reinicio `immediate` a mitad | crash recovery limpio (0 filas/recibos); reintento completa |
| s04b reinicio entre commit de datos y versiones, servicio nuevo | reanuda el mismo job sin repetir datos; 6 versiones; 1 recibo de control |
| s05 respuestas perdidas (datos y versiones) + reintentos | las mismas 6 versiones; 1 recibo de control |
| s06 nuevo registro de versión raw entre intento y reintento | el reintento se **rechaza** («job ID reused…»); ver L-2 |
| s07a dump/restore completo, mismos nombres | reanuda y termina; 6 versiones |
| s07b restore con otro nombre de base | rechazado («reused…»); ver L-3 |
| s07c restore sin esquema `qf_recovery` | sin duplicados (conteos iguales); error `table4_pkey`; ver L-4 |
| s07d control restaurado a dump anterior a las versiones | re-registra 6 versiones una sola vez, 1 recibo |
| s08 alteración de destinos | mismo conteo + contenido distinto: **detectado** (refused); fila legítima extra: refused (versiones pendientes, L-1) |
| s09 payload fuente cambiado tras commit | repite éxito sin revalidar (límite documentado, L-5) |
| s10 segundo job tras vaciar destinos | versión 0002 en las 6 tablas, 12 en total, monótonas |
| s11 cliente `psql` matado a mitad | rollback; 0 sesiones y 0 bloqueos tras 3 s; reintento completa |

## Defectos menores (reproducibles)
**D-1 · carrera de instalación inicial (punto 2 de Codex).** `INSTALL_SQL` (`dbperf/receipts.py:9-15`) usa
`CREATE SCHEMA/TABLE IF NOT EXISTS`, que no es seguro ante concurrencia. Con 6 clientes sobre una base sin `qf_recovery`:
**26 de 60 clientes fallaron** (10 pruebas) con `duplicate key … pg_namespace_nspname_index (qf_recovery)`; la instalación
quedó siempre consistente (1 esquema/tabla, 0 recibos) y un reintento posterior funcionó sin intervención manual, pero el
cliente afectado ve un error. Corrección probada: envolver con `pg_advisory_lock(hashtextextended('qf_recovery_install',0))`
… `pg_advisory_unlock` → **0 fallos en 60**, instalación consistente. Un patrón parecido aparece en el `ensure` de metadatos de
control de upstream (`delete_audit_id_seq`, `delete_audit` «already exists»); no es modificable (`app/`) y se mitiga calentando
una vez antes de lanzar clientes en paralelo. Repro: `t003_replica.py s00` (más la medición 10×6 descrita arriba).
**D-2 · la excepción original queda enmascarada.** En s04a el cliente vio «`FATAL: the database system is shutting down`»:
el `except` de `durable_service.py` ejecuta `_read_receipt`, que falla también y oculta el error real (conexión cortada).
No cambia datos, sí el diagnóstico. Sugerencia: capturar el fallo de `_read_receipt` y re-lanzar el original encadenado.
**D-3 · (ya visto, H-2)** el reintento sólo reutiliza el UUID dentro del mismo servicio; tras un proceso nuevo hay que usar `--resume-job`.

## Límites (comportamiento seguro pero bloqueante)
- **L-1** Tras el commit de datos, una edición legítima del destino (fila extra en `table3`) hace que la reanudación se
  **rechace**: las versiones quedan pendientes y no hay vía automática; se desbloquea revirtiendo la edición (probado: tras
  revertir, `finalize` registra 6). Necesita un procedimiento de recuperación manual documentado.
- **L-2 (H-5 confirmada)** Un nuevo registro de versión raw entre intento y reintento cambia la firma: tras reiniciar el
  proceso no se puede re-derivar el resultado y las versiones quedan sin registrar. Con el resultado previo al reinicio sí finaliza.
- **L-3** La firma liga el nombre de la base: una copia restaurada con otro nombre rechaza el mismo UUID.
- **L-4** Restaurar datos sin `qf_recovery` (recibos perdidos) deja datos presentes sin recibo: el reintento falla por unicidad sin
  duplicar, pero queda «atascado» sin ruta automática.
- **L-5** Cambios de payload fuente tras el commit, o con `raw_hash` igual, no se detectan en la repetición (ya documentado).

## Pruebas pendientes / no cubiertas (Codex, puntos 3–8)
SSH real (corte durante expansión/commit/versiones; cierre de la GUI y reanudación desde proceso nuevo) · semántica de `lazy_keys`
con cambios de `work_mem`/paralelismo/JIT/estadísticas · 1M con 5 repeticiones equilibradas · monitor de disco intermedio ·
3M/10M · bloques/checkpoints. Requieren el lab Docker/Linux de Codex (recurso compartido) y cargas largas; no iniciadas.

## Clasificación
Fallos de corrección: **0**. Defectos menores: D-1, D-2, D-3. Límites: L-1…L-5. Pendiente: puntos 3–8.
Propuesta de parche (para Codex, dueño de `dbperf/`): D-1 (lock de instalación) y D-2 (conservar la excepción original).
