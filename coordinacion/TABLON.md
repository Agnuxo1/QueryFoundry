# Tablón compartido — QueryFoundry

Creado: 2026-09-30 10:23, Europe/Madrid. Canal común de Codex y Claude;
las recomendaciones verificadas de JEV se registran aquí por quien lo consulta.
Leer antes de trabajar y actualizar al tomar una tarea, cambiar una reserva,
resolver un bloqueo o terminar un hito. No es un servicio de mensajería automática.

## Estado inicial comprobado

- Código y entrega local: commit `b1758eada44046e3de46498132e88da84474e671`.
- 100.000 filas oficiales; equivalencia de las seis tablas; diez tests aprobados.
- GUI/SSH/Linux con recuperación: medianas 13,868 s original y 12,911 s candidato.
- Cinco escenarios de recuperación aprobados. Faltan dumps, reinicios y concurrencia.
- Docker/WSL reparados; laboratorios detenidos, datos conservados. Revalidar antes de usarlos.
- Sin publicación GitHub ni entrega Kaggle. Sin puntuación oficial.
- Evidencia: `docs/STATUS.md`, `reports/`, `.cognition/checkpoint.md`.

## Equipo y responsabilidades

| Participante | Responsabilidad | Estado inicial |
|---|---|---|
| Codex | Integración, integridad del origen, metodología y benchmarks; cerrar decisiones con evidencia | Sistema de coordinación preparado; nuevas tareas pendientes |
| Claude | Auditoría independiente de recuperación/versiones/dumps; proponer mejoras acotadas y verificables | Pendiente de incorporación mediante PROMPT_CLAUDE.md |
| JEV | Recomendar prioridades y revisar decisiones sustanciales mediante consulta compacta | Consulta de coordinación verificada; no ejecuta tareas ni vigila archivos |
| Usuario | Dirección del proyecto y decisiones que requieran su intervención | Responsable humano |

## Actividad y bloqueos

Ninguna tarea del equipo está actualmente en ejecución. Asignaciones propuestas
en TAREAS.md, pendientes de aceptación. No confundir una asignación con trabajo realizado.
Bloqueos conocidos: escala mayor sin medir, fecha exacta sin verificar, entrega sin publicar.

## Reservas de recursos

| Reserva | Responsable | Inicio/fin Madrid | Recurso | Límite | Estado | Tarea |
|---|---|---|---|---|---|---|
| Sin reservas activas | — | — | — | — | LIBRE | — |

Registrar antes de arrancar: ID, propietario, intervalo, contenedor/base,
commit medido, CPU, RAM, espacio previsto en D:/E:, estado y criterio de parada.
Sólo una carga de benchmark/recuperación que modifique bases compartidas a la vez.
Configuración inicial de laboratorio: 2 CPU y 2 GiB de RAM; GPU no requerida.
Revalidar RAM libre, espacio y cargas ajenas. No trasladar datos a C: ni tocar
VHDX, volúmenes o contenedores de otros proyectos. Escalar a 1 millón sólo tras
reserva y plan revisado; 300 millones requiere otra evaluación de capacidad.

## Mensajes y decisiones

Añadir entradas fechadas; no borrar las de otro participante. Una propuesta no
es una decisión aceptada. Si dos participantes quieren editar el mismo archivo,
acordar turno y propietario antes de escribir. Si no hay acceso directo, usar
un mensaje de entrega explícito; no afirmar que el otro participante lo recibió.

### 2026-09-30 10:23 Madrid — Codex — COORD-001 — COMPLETADO

Se preparó el protocolo documental solicitado. JEV devolvió `status=connected`,
`exit_code=0`, `provenance=jev`, decisión `shared_board_single_compute`, confianza
1,0: tablón compartido, auditoría acotada y reserva única de cómputo.
Claude todavía no ha sido contactado. No se iniciaron agentes ni automatizaciones.
Siguiente paso: pegar PROMPT_CLAUDE.md en Claude con acceso a este repositorio.

### Plantilla de nueva entrada

`AAAA-MM-DD HH:MM Madrid — autor — tarea/idea — estado`

Hecho o propuesta; evidencia/ruta y commit; decisión solicitada; recursos;
bloqueo y siguiente acción. Registrar resultados observables, no deliberaciones privadas.
