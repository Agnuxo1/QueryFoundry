# T-001 — Auditoría independiente de recuperación y versiones

Autor: Claude · Fecha: 2026-09-30 (Europe/Madrid) · Commit inspeccionado: `72a4643`
(código idéntico a `b1758ea`; `verify_upstream.py`: 32 archivos intactos).
JEV: `v2-query` exit_code=0, status=connected, provenance=jev; devolvió criterios
(fidelity 0,78 / impact 1,0 / safety 1,0), interpretados como: reproducir en PostgreSQL real
y aislado, ordenar por impacto, sin ediciones concurrentes en `dbperf/`.

## Alcance y límites

Revisado: `dbperf/durable_service.py`, `receipts.py`, `service.py`, `recipes.py`,
`scripts/test_recovery.py`, `test_durable_pipeline.py`, `benchmark_gui.py`,
`launch.py`, `docs/RECOVERY.md`, STATUS/README/WRITEUP, `reports/durable-*` y las
funciones upstream `_capture_expansion_source_manifest` (8085),
`_build_expansion_manifest_sql` (13700), `execute_cross_table_expansion` (13788),
`register_cross_table_expansion_versions` (14952) y el DDL del generador.
Ejecutado: PostgreSQL 17.11 propio y desechable (RES-CL-01, ya detenido y borrado);
no se tocó el lab Docker ni `.runtime/pgdata`. **No** se ejecutó el servicio completo
(GUI/SSH), ni versiones concurrentes, ni dump/restore reales: lo no ejecutado se marca
como HIPÓTESIS. No se encontró ninguna corrupción de datos ni duplicación efectiva.

## Hallazgos (por impacto)

### H-1 · MEDIO · CONFIRMADO por ejecución — el guard de duplicado concurrente no funciona
`dbperf/durable_service.py:36-37` (`_guard`) se inserta en `:61` justo tras
`BEGIN ISOLATION LEVEL REPEATABLE READ;`. En REPEATABLE READ la instantánea se toma en
la primera sentencia, que es el propio `pg_advisory_xact_lock`: la petición que espera
el bloqueo conserva una instantánea anterior al commit de la otra y el `IF EXISTS
(receipts)` no ve el recibo. (La rama `versions`, `:79`, usa READ COMMITTED y no sufre esto.)
- Escenario: dos conexiones con el mismo `job_id`, T2 empieza mientras T1 trabaja.
- Resultado observado: T2 espera, T1 confirma, T2 **repite todo el trabajo** y sólo falla
  al insertar el recibo: `duplicate key value violates unique constraint "receipts_pkey"`;
  sus efectos se revierten. Datos consistentes (una expansión), pero la seguridad la da la
  PK, no el guard; se paga una expansión completa duplicada. Contradice el mensaje del
  guard (`QueryFoundry job already committed`) y el gate de R-001 en coste, no en corrección.
  En el servicio real `:136-141` capta la excepción y devuelve el recibo, por lo que el
  efecto visible es tiempo y carga desperdiciados (~una expansión) más bloqueos
  `SHARE ROW EXCLUSIVE` en los seis destinos.
- Reproducción mínima (SQL puro, PG 17): sesión A: `BEGIN ISOLATION LEVEL REPEATABLE READ;
  SELECT pg_advisory_xact_lock(k); DO $$ IF EXISTS(SELECT 1 FROM qf_recovery.receipts WHERE
  job_id=J) THEN RAISE ...$$; INSERT INTO eff VALUES('T1'); SELECT pg_sleep(3); INSERT INTO
  qf_recovery.receipts ...; COMMIT;` — sesión B idéntica 1 s después → error de PK en B.
- Corrección probada (mismo test): tomar `SELECT pg_advisory_lock(k);` **antes** de
  `BEGIN ISOLATION LEVEL REPEATABLE READ;` y `pg_advisory_unlock(k)` tras `COMMIT` (el bloqueo
  de sesión se libera al cerrarse la conexión si hay error con `ON_ERROR_STOP`). Resultado:
  B falla al instante con `QueryFoundry job already committed`, un solo efecto.
  Propuesta para Codex (dueño de `dbperf/`): aplicar así y añadir test R-001.

### H-2 · MEDIO · DISEÑO (verificado por lectura y por DDL) — reintento en la misma sesión GUI no reutiliza el job
`durable_service.py:102`: sin `QF_JOB_ID` cada llamada crea `uuid4()`. `launch.py:19-22`
sólo fija `QF_JOB_ID` con `--resume-job`. Tras una respuesta SSH perdida con el GUI vivo,
reintentar en la misma sesión crea otro job: la segunda expansión choca con las restricciones
únicas (`table4` PK `generate_database.py:528`, `table1.id_column_3` UNIQUE `:544`) y se
revierte entera → **sin duplicados**, pero el usuario ve un error mientras el primer intento
ya confirmó datos **sin versiones**; sólo `--resume-job UUID` (tomado de `.runtime/jobs`) lo
completa. Coincide con la advertencia de RECOVERY.md, pero el mensaje de error no lo indica.
Nota: `table3` no tiene clave natural única; su protección es que la transacción entera
falla por `table4`. Es una protección accidental: si una receta futura no tuviera restricción
única, el reintento duplicaría. Sugerencia: reutilizar el último job pendiente de
`.runtime/jobs` cuando la firma coincida, o mostrar el `job_id` en el error.
Sin reproducir con el servicio completo (HIPÓTESIS sobre la conducta exacta de la GUI).

### H-3 · MEDIO · CONFIRMADO (semántica) — el recibo no verifica que los efectos sigan presentes
Un recibo existente devuelve éxito aunque las tablas destino se hayan vaciado, restaurado
o sustituido después. Reproducido en SQL: recibo presente, `TRUNCATE eff`, el recibo sigue
devolviendo el resultado guardado con 0 filas. Impacto en dump/restore: restaurar el dump
completo de la base de datos restaura tabla y recibos coherentes; restaurar sólo algunas
tablas (o una versión anterior de un destino con el mecanismo RESTORE upstream) y conservar
`qf_recovery.receipts` produce «éxito» sin datos. Al revés (recibos ausentes, datos
presentes) el reintento falla por unicidad sin corrupción. `RECOVERY.md` sólo dice «no
truncar entre intento y reintento». Sugerencia: el recibo debe guardar recuento por destino
y la ruta de recuperación debe compararlo antes de devolver éxito; pruebas T-003 con
`pg_dump`/`pg_restore` reales (HIPÓTESIS hasta ejecutarlas).

### H-4 · BAJO · CONFIRMADO por lectura — etiqueta «con recibos» incorrecta para el original
`scripts/benchmark_gui.py:63`: con `--durable`, `mode=='baseline'` usa `PostgresAdminService()`
(upstream puro, **sin recibos**); sólo `payload_once` usa `DurableQueryFoundryService`.
`reports/durable-gui/gui-benchmark.json` (`recovery_gui_integrated: true`), STATUS.md:12,
README.md:24-25 y WRITEUP.md:51 presentan 13,868/12,911 s como «con recuperación» para ambos.
Realmente compara original **sin** recibos contra candidato **con** recibos (sesgo
conservador: el 6,9 % incluye coste de recibos sólo en el candidato). Corregir la
redacción o medir el original con recibos. Además: la mediana 13,868 sale de
16,76/13,42/13,87 (dispersión alta, la primera ejecución es fría y siempre es el
original) frente a 14,06/12,81/12,91 del candidato: **rangos solapados** (13,42 < 14,06);
con n=3 la ganancia no es estadísticamente sostenible. `payload_once-3.json` (15,31 s,
diagnóstico, pico 790 MiB) queda en el mismo directorio y patrón de nombre que las series y
no lleva `six_table_multiset_equivalence` en el JSON, aunque STATUS afirma equivalencia
verificada: mantener fuera del patrón `gui-*-N.json` o marcarlo.

### H-5 · BAJO — comentarios y documentación obsoletos / recuperación incompleta
- `dbperf/receipts.py:3-4` aún dice «not enabled in the GUI… versions still need its own
  receipt»; ya lo hace `durable_service.py`.
- `_recovered_result` (`:87-97`) no rellena `total_rows_inserted`/`destination_rows`
  (por defecto 0 y `[]`, upstream `postgres_service.py:313-314`): el informe de un job
  recuperado muestra «Inserted rows: 0». Aceptable pero conviene una nota en el informe.
- Con la firma de versiones (`:153`) que incluye `source_versions` del control DB en
  el momento de la captura (`:105`): si entre el intento y el reintento cambia el registro
  de versiones raw, la firma difiere y se rechaza «job ID reused» aunque los datos ya estén
  confirmados. Escenario no reproducido (HIPÓTESIS); documentar como límite.
- Los reintentos rápidos tras fallo de red: `:137` `_read_receipt` puede volver a fallar y
  enmascarar la excepción original (HIPÓTESIS).

## Lo que no se encontró fallo
- Recibo y datos comparten transacción (`:71`); las inserciones de `receipts` antes del
  `COMMIT` upstream son correctas; el parcheo textual falla cerrado (`count!=1` →
  `RuntimeError`), y la recomprobación de huella dentro de la instantánea (`:62-68`) es
  correcta para su alcance. Límite conocido y documentado: no detecta payloads alterados
  con `raw_hash` intacto.
- Versiones: el bloqueo por tabla y `MAX(version_code)+1` bajo READ COMMITTED (upstream) más
  el guard READ COMMITTED evitan versiones duplicadas por diseño (no ejecutado).
- Guarda de firma con petición modificada: probada por el propio test y coherente con la lectura.

## Reproducción mínima (todo en PostgreSQL desechable, ya eliminado)
1. `initdb` de un clúster aparte en D:, puerto loopback, tabla `eff(who)` y `qf_recovery.receipts`
   (DDL de `dbperf/receipts.py:9-15`).
2. Dos `psql -f -` con la secuencia de H-1 (T2 retrasado 1 s). Observar el error de PK en T2.
3. Sustituir el bloqueo por `pg_advisory_lock` de sesión antes del `BEGIN`: T2 falla con
   `job already committed`, `count(eff)=1`.
4. H-3: tras el commit, `TRUNCATE eff` y releer el recibo: sigue devolviendo éxito.

## Siguiente acción propuesta
- Codex decide si acepta el parche de H-1 (dueño de `dbperf/`); yo puedo entregar el diff
  en `entregas/` y el test R-001. H-3/H-2 → T-003 (pruebas de reinicio y dump/restore).
- Corregir las etiquetas de H-4 antes de cualquier texto de entrega.
- Nota de coordinación: `scripts/consult_claude.py` escribe en **este** archivo
  (`entregas/T-001-claude.md`) y lo sobrescribiría; no ejecutarlo sin acordar otro destino.
