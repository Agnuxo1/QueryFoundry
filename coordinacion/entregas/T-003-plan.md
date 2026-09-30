# T-003 — Diseño de la réplica independiente (concurrencia, reinicio, dumps)

Autor: Claude · 2026-09-30 (Europe/Madrid) · Base `64520b1` · Perfil de recetas: `lazy_keys`
Resultados y hallazgos: `T-003-claude.md`. Código: `entregas/t003/{harness.py,t003_replica.py}`.

## Principios (independencia)
- Se ejecuta el **`DurableQueryFoundryService` real** (parche de recibos, guard, huellas) — no los tests de Codex.
- Única sustitución: el transporte SSH se cambia por `psql.exe` local (mismo SQL, mismo texto parcheado).
  Se reimplementa `run_remote_command` con las mismas reglas de error (exit≠0 ⇒ `RuntimeError`; stderr sin stdout ⇒ error).
- PostgreSQL 17.11 **Windows propio y desechable** (RES-CL-03), generador oficial con 20.000 filas (conteos esperados
  table3=4, table4=24, table1=20.000, table5=20.000, table6=20.000, table2=46.383). Cada escenario parte de una
  copia `CREATE DATABASE … TEMPLATE` (limpia), de modo que no hay arrastre entre escenarios.
- Oráculos propios: conteos esperados, número de recibos en ambas bases, versiones registradas, bloqueos advisory
  residuales, sesiones residuales. Nada se da por bueno por «no hubo excepción».
- Límite de fidelidad: Windows ≠ Linux/Docker del lab; 20k ≠ 100k. Sirve para lógica de recuperación, no para rendimiento.

## Escenarios
| ID | Fallo / situación | Efecto esperado |
|---|---|---|
| s00 | 4 clientes, mismo UUID, **instalación inicial simultánea** (DB sin `qf_recovery`) | integridad intacta; los clientes que fallen deben poder reintentar |
| s01 | 4 clientes, mismo UUID, instalación previa | una sola expansión (un solo cliente ejecuta DML), 1 recibo por base, 6 versiones, 0 bloqueos |
| s02 | 2 clientes, UUID distintos | uno gana; el otro falla sin efectos parciales ni recibo |
| s03 | `pg_terminate_backend` a mitad de expansión + reintento | 0 filas, 0 recibos, 0 bloqueos; el reintento completa |
| s04a | reinicio `immediate` del servidor a mitad + reintento | crash recovery sin filas/recibo; el reintento completa |
| s04b | reinicio `immediate` entre commit de datos y versiones, **servicio nuevo** | reanuda el mismo job; 6 versiones; datos no se repiten |
| s05 | respuesta perdida tras commit (datos y versiones) + reintentos | mismas 6 versiones, 1 recibo de control |
| s06 | (H-5) cambia el registro de versiones raw entre intento y reintento | documentar si el reintento se rechaza (firma) |
| s07a | dump/restore completo, mismos nombres | reanuda y termina con 6 versiones |
| s07b | restore de la base de datos con **otro nombre** | documentar (la firma incluye el nombre) |
| s07c | restore sin el esquema `qf_recovery` | sin duplicados; documentar el estado atascado |
| s07d | control restaurado a un dump anterior a las versiones | re-registro único de las 6 versiones |
| s08 | alteración de destinos tras commit (mismo conteo / fila legítima extra) | detección por huella; cómo se desbloquea |
| s09 | cambio del payload fuente tras commit | documentar (no se revalida) |
| s10 | segundo job tras vaciar destinos | códigos de versión monótonos (0002) |
| s11 | matar el proceso cliente (psql) a mitad de transacción | rollback, sin sesión ni bloqueo residual |

## Criterio de aceptación
Cada escenario devuelve PASS (oráculos cumplidos) o un hallazgo con la salida literal. Un hallazgo se clasifica como
fallo (efecto incorrecto), límite (comportamiento seguro pero bloqueante) o hipótesis (no ejecutado).
No se modifica `dbperf/`, `app/` ni los laboratorios Docker; clúster y dumps se borran al terminar.
