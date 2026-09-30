# Reparación verificada — 2026-09-30

Docker Desktop estaba instalado pero WslService apuntaba a un ejecutable ausente
en C:/Program Files/WSL. Se instaló el MSI oficial firmado por Microsoft de
WSL 3.0.1, descargado y conservado en D: dentro de .runtime/wsl-repair.
El usuario autorizó la elevación y aceptó UAC. Resultado: exit_code=0,
reboot_required=false y binario de servicio presente.

WSL 3.0.1 y kernel 6.18.40.1-1 quedaron funcionales; Docker Linux 29.4.0 arrancó.
No se reinstaló ni se reseteó Docker y no se desregistraron distribuciones.
Se conservaron sus 23 imágenes y el contenedor previo. Los discos existentes
siguen en E:/DockerDesktopWSL: docker_data.vhdx y main/ext4.vhdx.
El volumen queryfoundry pertenece sólo a este proyecto.

Descargas, entorno Python, herramientas cliente y cachés de esta tarea están
en D:. Los componentes del sistema WSL permanecen en su ruta oficial de C:.
No ejecutar de nuevo los scripts de reparación si WSL funciona.
