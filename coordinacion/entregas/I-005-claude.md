# I-005 — `lazy_keys`: alternativa para `table2` que conserva errores y orden de evaluación

Autor: Claude · 2026-09-30 (Europe/Madrid) · Base: commit `abdb128` · Estado: **CANDIDATO — pasa la puerta de
semántica; falta medición GUI por Codex** (KEEP provisional, no un resultado de rendimiento oficial).
JEV (consulta previa, misma sesión): criterio `safety` → un cambio por vez y equivalencia antes de velocidad.

## Idea
`table2` expande cada fila fuente en 14 posiciones con un `VALUES` que **extrae ya** 5 textos JSON por
posición (70 `->>` por fila; `payload_once` sólo evita repetir `raw->'values'`). `Values Scan` los evalúa
todos aunque 12 de 14 posiciones se descartan por tipo. `lazy_keys` deja en el `VALUES` sólo los *nombres de
clave* y extrae el valor donde se usa (`qf_payload.payload ->> position_keys.type_key`, etc.) en el `WHERE`
y el `SELECT`. Casts, filtros, join, salidas y orden de quals son idénticos a la receta oficial, por lo que
se conserva el orden efectivo tipo → peso → declaración (y sus errores). A diferencia de
`sparse_positions` (rechazado), **no filtra por la declaración antes**.
Código: `coordinacion/entregas/i005/lazy_keys.py`; parche propuesto (sólo `dbperf/recipes.py`, modo
nuevo `lazy_keys`; las otras cinco recetas usan `payload_once`): `i005/recipes_lazy_keys.patch`
(`git apply --check` OK; las cinco primeras recetas coinciden con `payload_once` y `table2` con `lazy_keys`).

## Evidencia (PostgreSQL 17.11 Windows propio, RES-CL-02, 100.000 filas del generador oficial)
1. **Plan**: `EXPLAIN` confirma el filtro `type → weight → declaration` en el mismo orden que el original.
   El trigger FK `table2_id_column_2_fkey` cuesta ~1,3–1,7 s en todas las variantes (no se puede tocar).
2. **Equivalencia con datos oficiales**: 232.386 filas; `EXCEPT ALL` bidireccional (sin `id_column_1`) = 0
   y misma asignación de identidad por posición.
3. **Semántica con datos inválidos (fuzz diferencial)**: `i005/fuzz_semantics.py`, filas oficiales mutadas
   en una tabla TEMP; compara estado, **mensaje de error exacto** (incluye el valor ofensor) y hash del
   multiconjunto. `lazy_keys`: 400 pruebas (seed 11; 47 errores) + 400 pruebas de alta mutación (seed 21;
   155 errores) → **idénticas**. `payload_once`: 150 pruebas idénticas. **Control positivo**: el prototipo
   rechazado `sparse_positions` produce la discrepancia en la primera prueba (mensaje distinto), luego el
   arnés detecta cambios de semántica.
4. **Tiempo del INSERT de `table2`** (servidor, `EXPLAIN ANALYZE TIMING OFF`, `BEGIN…ROLLBACK`, 5 rondas
   alternadas; diagnóstico relativo, **no** métrica GUI, Windows ≠ lab Linux):

| Variante | Ejecuciones (ms) | Mediana |
|---|---|---:|
| baseline | 6384, 6477, 5975, 6027, 6071 | 6071 |
| payload_once | 5419, 5332, 5648, 5159, 5291 | 5332 (−12,2 %) |
| lazy_keys | 4388, 4244, 5162, 4057, 3946 | 4244 (−30,1 % vs baseline, −20,4 % vs payload_once) |

Los rangos no se solapan entre `payload_once` y `lazy_keys` salvo un valor (5162 vs 5159).

## Límites
- Sólo `table2`; sólo 100k en PG Windows con 1 CPU, sin paralelismo. Efecto en la métrica GUI **no medido**;
  extrapolar (~−1 s a 100k, quizá ~−10 s a 1M sobre ~58 s) es **hipótesis**.
- El orden de evaluación de quals depende del planificador (no de SQL estándar); el fuzz lo verifica en
  40 filas por prueba con un plan de tabla TEMP, no en todos los planes posibles a 1M/300M. Repetir el
  fuzz sobre el lab Linux antes de aceptar.
- No se modificó `dbperf/`, `app/` ni datos oficiales. Reserva RES-CL-02 liberada; clúster borrado.

## Siguiente (propuesto)
1. Codex aplica el parche (o pide cambios) y mide en GUI 100k alternando original/`payload_once`/`lazy_keys`
   con la huella reforzada; luego 1M.
2. Repetir `fuzz_semantics.py` contra el PostgreSQL Linux del lab (cambiar puerto/psql en la cabecera).
3. Si KEEP: registrar en THINKTANK/RESEARCH-LOG y actualizar README con la cifra GUI real.
