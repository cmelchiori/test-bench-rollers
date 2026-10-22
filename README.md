# Test Bench Rollers

Aplicación de terminal para probar nodos roller mediante el protocolo serial del
gateway Mesh.

## Ejecución

```bash
uv sync
uv run test-bench-rollers --port /dev/ttyUSB0
```

La interfaz predeterminada es un panel táctil con búsqueda, selección, prueba,
desvinculación y métricas. El registro serial está en la pestaña `Depuración`.
Para usar el
flujo de prompts tradicional, ejecuta `uv run test-bench-rollers --ui cli` y
conserva los argumentos de puerto y límites.

Opciones disponibles:

- `--baudrate`: velocidad serial (115200 por defecto).
- `--scan-seconds`: duración de la búsqueda (8 s por defecto).
- `--link-timeout`: espera máxima de confirmación `[LINK] linked` (10 s).
- `--identify-timeout`: espera máxima de la respuesta MAC de `identify` (5 s).
- `--link-settle-seconds`: pausa tras confirmar el vínculo (0.5 s).
- `--response-timeout`: espera máxima de telemetría (10 s).
- `--unlink-listen-seconds`: ventana para procesar y mostrar RX después de
  `remote unlink group` (3 s por defecto).
- `--remote-port`: puerto local de la consola UART remota (8765 por defecto).
- `--rpm-min` / `--rpm-max`: límites opcionales de RPM.
- `--temperature-min` / `--temperature-max`: límites opcionales de temperatura.

El flujo sigue esta secuencia:

1. La persona toca **Buscar nodos**; se envía `remote scan group` y se escucha
   durante `--scan-seconds`.
2. Muestra los nodos detectados para que el operador seleccione uno y pulse
   **REQUEST NODE** cuando quiera iniciar la prueba.
3. Envía `remote identify <mesh_id>` y espera la respuesta `[MAC]` hasta
   `--identify-timeout`.
4. Envía `remote link <mesh_id>` y espera `<mesh_id> [LINK] linked`.
5. Tras la pausa de estabilización, solicita confirmación manual antes de enviar
   `remote request <mesh_id>`.
6. Decodifica `DATA`/`DATAX` usando `packages/mesh-schemas`. Solo emite
   resultado cuando están disponibles MAC y `device-id`; si falta alguno, deja
   la prueba pendiente y permite reintentar únicamente la consulta necesaria,
   sin volver a vincular. Luego evalúa RPM, temperaturas y alarmas latched.

En la TUI, las acciones principales usan botones grandes y permanecen en una
sola pantalla para operar sin teclado. `Operación` muestra nodos detectados,
selector, identidad y el resultado en dos columnas; `Depuración` contiene el
registro serial. Cada solicitud requiere una acción del operador. Si falta la
MAC o el `device-id`, el resultado queda pendiente y se puede reintentar solo la
consulta que falta, sin volver a vincular. Un toque
en `Unlink Group` pide confirmación antes de enviar el comando. Tras enviarlo,
escucha la ventana configurada y registra los mensajes RX; si no hay respuesta,
lo indica claramente. En la CLI tradicional
se puede repetir (`r`), consultar el estado (`status`) o salir (`q`); `u` envía
`remote unlink group`. `Ctrl+C` cancela.

El botón **LIMPIAR PRUEBA** borra el resultado, los datos y el nodo seleccionado,
conserva la lista de nodos detectados y permite iniciar otra prueba.

## Consola remota UART

La TUI mantiene disponible una consola remota local en `127.0.0.1:8765`. El
servicio sigue siendo el único proceso que abre el UART y distribuye las líneas
RX a la operación local y a la consola remota. La consola remota observa RX y
los mensajes de depuración mientras corre una prueba. Si se conecta cuando el
banco está libre, toma el control automáticamente; el panel muestra
**BANCO BLOQUEADO POR ACCESO REMOTO** y deshabilita las acciones locales. Si el
banco está ocupado, la consola queda en observación y puede pedir control con
`:take` cuando termine la operación. `:release` devuelve el UART al panel y
`:exit` cierra la sesión.

El puerto escucha solo en loopback; no lo publiques directamente en la red.
Para usarlo desde un equipo de soporte, crea un túnel SSH hacia la Raspberry:

```bash
ssh -L 8765:127.0.0.1:8765 pi@raspberry
```

Luego, en otra terminal del equipo remoto:

```bash
uv run test-bench-remote
```

El cliente abre una TUI con estado del banco, bloqueo, registro en vivo RX/TX y
un campo para comandos. Se conecta en modo observación y solicita tomar el
control automáticamente; si el banco está ocupado, usa `TOMAR CONTROL UART`
cuando termine la prueba. `LIBERAR UART` devuelve el control al panel. Escribe
comandos del gateway como `remote scan group`; `remote unlink group` vuelve a
pedir confirmación en la pantalla local. Si hace falta otro puerto local en el túnel, usa
`test-bench-remote --port <puerto-local>`. El puerto del servicio se cambia con
`--remote-port`.

Los rangos configurables de RPM y temperatura no tienen valores predeterminados
porque deben definirse para el proceso de calidad. Además, se mantiene el límite
crítico de hardware de la vista QA del gateway: cualquier canal sobre 160 °C da
FAIL, aunque no se configuren rangos. La pantalla muestra el resultado y los
criterios aplicados junto a RPM, conexión, link, temperaturas y alarmas.
La cuenta regresiva sugerida para el primer request comienza al hacer la primera
búsqueda de nodos y no bloquea el envío. Después, el intervalo de 65 s vuelve a
comenzar al recibir telemetría, una respuesta `DATA None` o vencer el timeout de
una solicitud. La TUI mantiene visible el tiempo restante; durante ese intervalo
el botón ofrece **REQUEST FORZADO**.
Después de un `FAIL` o timeout, el mismo botón permite solicitar telemetría otra
vez para el nodo vinculado, sin repetir búsqueda ni vínculo.
En la CLI se muestra el tiempo restante y se confirma explícitamente si se quiere
forzar la solicitud. La consola remota sigue rechazando solicitudes anticipadas.

Ejemplo con límites definidos por el operador:

```bash
uv run test-bench-rollers --port /dev/ttyUSB0 \
  --rpm-min 100 --rpm-max 3000 \
  --temperature-min 0 --temperature-max 80
```
