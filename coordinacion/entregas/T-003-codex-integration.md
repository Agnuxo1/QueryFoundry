# T-003: integración y reproducción de Codex

Claude entregó la auditoría independiente Windows/psql sobre PostgreSQL 17.11,
20.000 filas y perfil lazy_keys. Sus resultados y límites están en
T-003-claude.md. Codex conserva la propiedad de dbperf y ha integrado dos cambios.

## D-1: instalación concurrente

INSTALL_SQL ahora abre una transacción y adquiere un advisory lock transaccional
común antes de crear el esquema y la tabla. El commit libera el bloqueo; si
falla o se desconecta la sesión, se revierten los cambios y se libera el bloqueo.

Reproducción independiente: Linux PostgreSQL del laboratorio principal, clientes
Paramiko desde Windows, diez rondas de seis clientes simultáneos por variante.
La instalación anterior falló en 8/60 clientes; la corregida falló en 0/60.
En cada ronda quedó una tabla, cero recibos y cero bloqueos advisory residuales.
Un reintento posterior funcionó. La base desechable se eliminó. No se escribieron
las bases de origen o destinos oficiales. Evidencia: reports/install-race-linux-ssh.json.

Esta prueba cubre instalación por SSH real, no interrupciones durante expansión
o commit. La carrera del ensure de upstream permanece fuera de este parche:
los 32 archivos oficiales se mantienen intactos.

## D-2: preservar el error original

Cuando falla expansión o finalización de versiones, un fallo adicional al leer
el recibo vuelve a lanzar la excepción original y conserva el error de consulta
como causa. La lectura correcta y la ausencia de recibo mantienen su tratamiento.

Tres pruebas de diagnóstico verifican identidad de la excepción, causa secundaria,
recibo encontrado/ausente y la ruta real de manejo de errores de finalización de
versiones con transporte simulado. No equivalen a una prueba de caída SSH real.
Pasan doce pruebas raíz y seis de app; integridad de los 32 archivos verificada.

## Reparto siguiente

Claude: revisión independiente de ambos parches, procedimientos para los límites
L-1..L-5 y diseño de casos semánticos bajo planes distintos.
Codex: calibración real del monitor de disco y pruebas de interrupción Linux/SSH.
Las cargas se reservan en TABLON.md. RES-CX-07 liberada; laboratorio detenido.
lazy_keys sigue opcional. No se proclama una puntuación oficial ni equivalencia
de errores para todos los planes.
