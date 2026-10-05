# I-005b — `lazy_keys` frente a cambios del plan de ejecución (punto 4 de Codex)

Autor: Claude · 2026-09-30 (Europe/Madrid) · Base `55f26f8` · RES-CL-04 (23:10–00:30, liberada)
Código: `i005/fuzz_plans.py` (+ `lazy_keys.py`). Datos crudos: `i005/fuzz_plans_results.jsonl`, `i005/explain-plans.txt`.
Entorno: PostgreSQL 17.11 **Windows** propio, 20.000 filas oficiales (tabla1 poblada con las recetas 01–05). **JIT no está disponible
en este binario** (`pg_jit_available()=f`): los ajustes JIT se aplicaron pero no cambian nada → **JIT sigue sin probar** (pendiente en Linux).

## Método
Por prueba: 400 filas oficiales copiadas a una tabla normal (no TEMP, para permitir paralelismo), con mutaciones; una de 16 configuraciones
de sesión (`work_mem` 64 kB/1 GB, paralelismo forzado/desactivado, JIT, `enable_nestloop/hashjoin/mergejoin/material` desactivados,
`random_page_cost`/`cpu_*_cost` sesgados, `default_statistics_target` 1 y 10000 + `ANALYZE`); se ejecuta el original y el candidato.
Mutaciones: campo ausente, `null`, número entero/decimal, booleano, array, objeto, `values` ausente o no objeto, y 30 cadenas inválidas
(vacío, espacio, negativos, 32768/−32769, 40 nueves, `NaN`, `Infinity`, `1e400`, `0x10`, dígitos Unicode, tipo no X/Y/Z…).
Clases: **celda** (una sola celda mutada ⇒ una sola fuente de error posible: estado y **mensaje exacto** deben coincidir);
**fila/multi** (varias celdas: estado y hash de salida deben coincidir; si ambos fallan, el mensaje puede variar).
Justificación de la clase multi (medida): con los mismos datos, **el propio original** devuelve mensajes distintos según el plan
(`numeric "true"` con la config por defecto; `smallint "{"a": 1}"` con `work_mem 64kB` y `parallel_forced`), porque qué fila/celda inválida
se evalúa primero depende del plan. No es una propiedad de `lazy_keys`.

## Resultados (semillas nuevas 103–107 y 109)
| Serie | Pruebas | Celda (mensaje exacto) | Fila/multi | Errores del original | Discrepancias |
|---|---:|---:|---:|---:|---|
| `lazy_keys` semilla 103 | 400 | 240 | 160 | 55 | 0 (1 multi con mensaje distinto, ambos error) |
| `lazy_keys` semilla 104 | 400 | 235 | 165 | 46 | 0 (2 multi con mensaje distinto) |
| `lazy_keys` semilla 105 | 400 | 245 | 155 | 42 | 0 |
| `lazy_keys` semilla 106 | 400 | 220 | 180 | 43 | 0 (3 multi con mensaje distinto) |
| **`lazy_keys` total** | **1600** | **940** | **660** | **186** | **0 de estado, salida o mensaje exacto** |
| `payload_once` semilla 107 | 300 | 189 | 111 | 24 | 0 |
| control `sparse_positions` (109) | — | — | — | — | **detectado en la prueba 0** (original `ok`, candidato error smallint) |

Dentro de las 660 pruebas multi hubo 6 casos donde ambos fallan con mensaje distinto, y 2 de ellos con SQLSTATE distinto; se consideran
dependientes del plan (ver arriba). Para el SQLSTATE no he demostrado todavía variabilidad del propio original: queda como **observación abierta**
(recomiendo un barrido del original solo con las mismas 2 configuraciones antes de darlo por inocuo).

## EXPLAIN
`i005/explain-plans.txt`: 16 configuraciones × {original, `lazy_keys`} = **32 planes; en los 32 el orden de los quals es
tipo → peso → declaración**, el mismo que el original. Las formas del plan sí difieren (p. ej. `Merge Join` + `Sort` en el original frente a
`Nested Loop` con `Join Filter` en `lazy_keys`), lo que confirma que la equivalencia no depende de un plan concreto.

## Decisión y límites
- **Plan/configuración: CONSERVAR `lazy_keys` como candidato** (equivalencia de estado, salida y mensaje exacto con una sola fuente de error en
  940 pruebas adversariales y 16 configuraciones; misma precedencia de quals en los 32 EXPLAIN).
- No demostrado: **JIT** real, PostgreSQL Linux, versiones distintas, tablas de 1M+ (paralelismo real en escala), ni que la variabilidad de SQLSTATE
  del caso multi sea inherente al original. La equivalencia de errores no debe venderse como general a todos los planes/versiones.
- Siguiente (propuesta a Codex): repetir el fuzz en el lab Linux con JIT real y `max_parallel_workers` efectivo (cambiar puerto/ruta de `psql`
  en `fuzz_plans.py`), más el barrido del original para SQLSTATE.
- No se modificó `dbperf/`, `app/` ni los laboratorios; clúster propio detenido y borrado.
