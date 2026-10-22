"""Sequential reader for bit-packed telemetry frames."""

from pydantic import BaseModel


class BitsDecoder(BaseModel):
    """Consume unsigned integers from a most-significant-bit-first bit string."""

    bits: str

    def extract_int(self, length: int) -> int:
        if length <= 0 or len(self.bits) < length:
            raise ValueError("insufficient payload bits")
        raw, self.bits = self.bits[:length], self.bits[length:]
        return int(raw, 2)

    def extract_array(self, size: int, count: int) -> list[int]:
        return [self.extract_int(size) for _ in range(count)]
