"""Minimal hand-rolled protobuf encoder/decoder."""


def encode_varint(v):
    r = []
    while v > 0x7F:
        r.append((v & 0x7F) | 0x80)
        v >>= 7
    r.append(v & 0x7F)
    return bytes(r)


def read_varint(d, o):
    r = s = 0
    while o < len(d):
        b = d[o]; r |= (b & 0x7F) << s; o += 1
        if not (b & 0x80): break
        s += 7
    return r, o


def iter_fields(d):
    o = 0
    while o < len(d):
        tag, o = read_varint(d, o)
        fn, wt = tag >> 3, tag & 7
        if   wt == 0: v, o = read_varint(d, o);                        yield fn, wt, v
        elif wt == 2: ln, o = read_varint(d, o); yield fn, wt, d[o:o+ln]; o += ln
        elif wt == 1: yield fn, wt, int.from_bytes(d[o:o+8], "little"); o += 8
        elif wt == 5: yield fn, wt, int.from_bytes(d[o:o+4], "little"); o += 4
        else: break


def encode_string(f, s):
    b = s.encode()
    return bytes([(f << 3) | 2]) + encode_varint(len(b)) + b


def encode_bool(f, v):
    return bytes([(f << 3), 1]) if v else b""
