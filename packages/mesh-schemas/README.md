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
`roller-data-1.1` (`DATA`), `roller-data-1.2` (DATAX anterior) y
`roller-data-1.3` (nuevo DATAX); la aplicación decide cuál corresponde a cada
mensaje.

En `roller-data-1.0` a `1.2`, VIN usa el rango 0–51 V y el RMS de armónicos
incluye el factor 4 del decoder de referencia. `roller-data-1.2` conserva la
trama DATA y agrega 256 bits; sus crest y kurtosis usan resolución 0.1
(`max_code=250`). Los RMS estadísticos y amplitudes de rodamientos invierten la
escala de raíz cúbica elevando al cubo el código normalizado. Las amplitudes de
rodamientos son porcentajes del RMS (0–140), no aceleraciones.

`roller-data-1.3` define una trama nueva de 124 bytes: recibe 155 caracteres Z85
y representa armónicos como diez pares de frecuencia y amplitud. La amplitud
física se obtiene multiplicando el peak por el porcentaje recibido; el modelo
incluye ambos valores y conserva la unidad del peak (mV para audio, g para
aceleración).
El shield elimina el prefijo `d]` antes de entregar el payload. `power.vin` usa
el rango 0–51 V. Cada decoder exige el largo exacto de su formato, sin eliminar
espacios ni saltos de línea.

`roller-data-1.0` modela la cabecera legada como
`metadata.hardware_version` y `metadata.firmware_version`. En `roller-data-1.1`
y `roller-data-1.2`, las alarmas `b0`…`b7` se conservan como máscara en
`fields.alarms.latched.all`, con `b0` en el bit menos significativo. En
`roller-data-1.3`, `fields.alarms.code` conserva el byte y expone por nombre los
seis bits latched y los dos bits de captura no latched, desde el bit menos
significativo. En todos los schemas `device.id` se representa en
`tags.device_id` y las métricas en `fields`.
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
