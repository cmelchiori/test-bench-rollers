# Despliegue en Raspberry Pi

El playbook instala Python 3.13 y `uv`, copia la aplicación, crea su entorno y
configura `test-bench-rollers.service` para abrir la TUI automáticamente en
una ventana de terminal de pantalla completa del escritorio local. Instala
`xterm` para esa ventana. También habilita el UART integrado (`enable_uart=1`),
libera `serial0` de la consola de login y lo usa como puerto de la aplicación.
Estos cambios de arranque reinician la Raspberry durante el despliegue. El
usuario `pi` debe tener una sesión gráfica activa y permisos de `sudo`. El
servicio usa `/dev/serial0` por defecto y escucha en la IP de inventario de la
Raspberry por el puerto UART remoto 8765. Usa una IP Tailscale en el inventario
para habilitar acceso directo dentro de la tailnet.

## Requisitos del equipo de despliegue

- Ansible instalado localmente.
- Acceso SSH a `pi@100.114.144.125` y conectividad de red con la Raspberry.
- La Raspberry debe tener una distribución Debian/Raspberry Pi OS basada en
  `apt` y conexión a Internet para instalar `uv` y las dependencias.

## Ejecutar

Desde la raíz del repositorio, pide la contraseña SSH de forma interactiva:

```bash
ansible-playbook -i deploy/inventory.ini deploy/site.yml --ask-pass --ask-become-pass
```

No guardes la contraseña SSH en el inventario ni en el repositorio. Si `pi`
puede ejecutar `sudo` sin contraseña, omite `--ask-become-pass`.

Para actualizar el código y reiniciar la TUI que corre dentro de `xterm`, usa
el script desde cualquier directorio del repositorio. Solo requiere la IP de la
Raspberry; se conecta como `pi`, sincroniza el proyecto, ejecuta `uv sync` y
reinicia `test-bench-rollers.service`:

```bash
./deploy/update-raspberry.sh 100.114.144.125
```

SSH y `sudo` pueden pedir sus contraseñas de forma interactiva. El script no
modifica la configuración del UART ni reinicia la Raspberry.

Para cambiar el dispositivo serial u otros valores:

```bash
ansible-playbook -i deploy/inventory.ini deploy/site.yml --ask-pass \
  --ask-become-pass -e serial_port=/dev/ttyUSB0
```

El usuario `pi` se agrega al grupo `dialout` para acceso al UART.

## Operación

La TUI aparece sobre el escritorio local en una terminal de pantalla completa.
Para revisar el servicio por SSH:

```bash
sudo systemctl status test-bench-rollers
sudo journalctl -u test-bench-rollers -f
```

Para abrir la consola UART remota desde otro equipo de la tailnet:

```bash
uv run test-bench-remote --host 100.114.144.125 --port 8765
```

El servicio se liga exclusivamente a esa IP Tailscale; no usa `0.0.0.0` ni se
expone a la LAN. La ACL de Tailscale debe restringir qué dispositivos pueden
acceder al puerto 8765.
