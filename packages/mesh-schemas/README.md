# mesh-schemas

Paquete Python con los esquemas versionados y decodificadores Z85/Base85 de
telemetría Mesh. No conoce UART, estado de nodos, identificadores de transporte,
persistencia ni gRPC: recibe un payload y un schema explícito, y devuelve un modelo
Pydantic validado.

## Uso

```python
from mesh_schemas import ROLLER_DATA_V10_SCHEMA, decode_base85_payload

payload = decode_base85_payload(encoded, schema=ROLLER_DATA_V10_SCHEMA)
if payload is not None:
    data = payload.model_dump(mode="json", by_alias=True)
```

Cada resultado normalizado contiene `schema`, `tags`, `fields` y `metadata`. Los
identificadores Mesh, timestamps, la persistencia y la publicación pertenecen a la
aplicación consumidora. Actualmente se incluyen `roller-data-1.0` (DATA legado),
`roller-data-1.1` (`DATA`) y `roller-data-1.2` (`DATAX`); la aplicación decide
cuál corresponde a cada mensaje.

La decodificación sigue el algoritmo de `apps/decoder` del gateway de referencia:
VIN usa el rango 0–51 V y el RMS de armónicos incluye el factor 4. DATAX conserva
los campos DATA y agrega 256 bits; crest y kurtosis usan resolución 0.1
(`max_code=250`). Los campos adicionales no se redondean: RMS y amplitudes
invierten la escala de raíz cúbica elevando al cubo el código normalizado.
Las amplitudes de rodamientos son porcentajes del RMS (0–140), no aceleraciones.
Se exigen exactamente 120 o 160 caracteres, sin eliminar espacios ni saltos de línea.

`roller-data-1.0` modela la cabecera legada como
`metadata.hardware_version` y `metadata.firmware_version`. En `roller-data-1.1`
y `roller-data-1.2`, las alarmas `b0`…`b7` se conservan como máscara en
`fields.alarms.latched.all`, con `b0` en el bit menos significativo. En todos
los schemas `device.id` se representa en `tags.device_id` y las métricas en
`fields`.
`stat.crest`/`stat.kurt` corresponden a `statistics.crest_factor`/`statistics.kurtosis`;
`stat.disp`/`stat.angle` radial a `axis_preference`/`cross_coupling`.
Los rodamientos usan `frequency` y `amplitude.axial`/`amplitude.radial`, con claves
en minúsculas. `accel.angle` está en grados; `cross_coupling` es adimensional.

Las pruebas incluyen las salidas JSON DATA y DATAX de referencia en `tests/fixtures`
y verifican todos sus valores bajo este mapeo, sin depender del directorio externo.

## Agregar un schema

Define el modelo de salida y un decoder que registre su schema. Los decoders no
infieren formatos ni toman decisiones de routing.

```python
from pydantic import BaseModel, Field

from mesh_schemas import TelemetryPayload, register_decoder


class BatteryTags(BaseModel):
    device_id: str


class BatteryFields(BaseModel):
    charging: bool
    voltage: float


class BatteryMetadata(BaseModel):
    decoder_version: str = "1.0.0"


class BatteryV1(TelemetryPayload[BatteryTags, BatteryFields, BatteryMetadata]):
    schema_name: str = Field("battery-1.0", alias="schema")


@register_decoder
class BatteryV1Decoder:
    schema = "battery-1.0"

    def decode(self, encoded: str) -> BatteryV1 | None:
        return BatteryV1(
            tags=BatteryTags(device_id="device-1"),
            fields=BatteryFields(charging=False, voltage=3.7),
            metadata=BatteryMetadata(),
        )
```

Los paquetes permanecen agnósticos entre sí. Cuando `mesh-interface` tenga sus
contratos Protobuf, las aplicaciones compondrán el payload normalizado con ese
transporte sin que `mesh-schemas` dependa de él.
