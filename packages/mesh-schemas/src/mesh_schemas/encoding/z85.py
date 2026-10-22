"""Z85 decoding in the little-endian word order used by Mesh nodes."""

Z85_TABLE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#"


def decode_z85(data: str) -> bytes:
    """Decode a Z85 string using the Mesh wire's little-endian word order."""
    decoded: list[int] = []
    for index in range(0, len(data), 5):
        chunk = data[index : index + 5]
        word = 0
        for char in reversed(chunk):
            word = word * 85 + Z85_TABLE.index(char)
        for _ in range(len(chunk) - 1):
            decoded.append(word & 0xFF)
            word >>= 8
        if len(chunk) == 1:
            decoded.append(word & 0xFF)
    return bytes(decoded)


def decode_z85_frame(data: str, *, encoded_length: int, decoded_length: int) -> bytes | None:
    """Decode one complete Z85 frame after validating its wire dimensions."""
    text = data
    if len(text) != encoded_length or any(char not in Z85_TABLE for char in text):
        return None
    try:
        decoded = decode_z85(text)
    except ValueError:
        return None
    return decoded if len(decoded) == decoded_length else None
