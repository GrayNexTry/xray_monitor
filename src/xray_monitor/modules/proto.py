"""Минимальный ручной кодировщик/декодировщик protobuf."""

# Максимальное число шагов varint (10 байт = 70 бит, покрывает uint64)
_VARINT_MAX_BYTES = 10


def encode_varint(v: int) -> bytes:
    r = bytearray()  # bytearray эффективнее list → bytes
    while v > 0x7F:
        r.append((v & 0x7F) | 0x80)
        v >>= 7
    r.append(v & 0x7F)
    return bytes(r)


def read_varint(d: bytes, o: int) -> tuple:
    """Читает varint из буфера. Возвращает (value, new_offset).

    Raises ValueError при выходе за границы буфера или слишком длинном varint.
    """
    r = s = 0
    limit = min(o + _VARINT_MAX_BYTES, len(d))
    while o < limit:
        b = d[o]; r |= (b & 0x7F) << s; o += 1
        if not (b & 0x80):
            return r, o
        s += 7
    raise ValueError(f"malformed varint at offset {o}")


def iter_fields(d: bytes):
    """Итерирует protobuf-поля. Пропускает повреждённые данные вместо крэша."""
    o = 0
    dlen = len(d)
    while o < dlen:
        try:
            tag, o = read_varint(d, o)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            try:
                v, o = read_varint(d, o)
            except ValueError:
                break
            yield fn, wt, v
        elif wt == 2:
            try:
                ln, o = read_varint(d, o)
            except ValueError:
                break
            # Проверка границ буфера — защита от buffer overread
            if ln < 0 or o + ln > dlen:
                break
            yield fn, wt, d[o:o + ln]
            o += ln
        elif wt == 1:
            if o + 8 > dlen:
                break
            yield fn, wt, int.from_bytes(d[o:o + 8], "little")
            o += 8
        elif wt == 5:
            if o + 4 > dlen:
                break
            yield fn, wt, int.from_bytes(d[o:o + 4], "little")
            o += 4
        else:
            break  # неизвестный wire type — безопасная остановка


def encode_string(f: int, s: str) -> bytes:
    b = s.encode()
    return bytes([(f << 3) | 2]) + encode_varint(len(b)) + b


def encode_bool(f: int, v: bool) -> bytes:
    return bytes([(f << 3), 1]) if v else b""
