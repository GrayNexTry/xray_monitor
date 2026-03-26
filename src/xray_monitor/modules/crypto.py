"""Генерация ключей X25519 и UUID."""

import os
import uuid
import base64
import secrets
import subprocess


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_short_id(length: int = 8) -> str:
    n = max(2, min(16, length if length % 2 == 0 else length + 1))
    return secrets.token_hex(n // 2)


# ── X25519: единая реализация (RFC 7748) ─────────────────────

_PRIME = (2 ** 255) - 19
_BASE_POINT = (9).to_bytes(32, "little")


def _clamp(k: bytes) -> bytes:
    """Clamping приватного ключа по RFC 7748."""
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def _x25519_ladder_step(R0: tuple, R1: tuple, u_int: int) -> tuple:
    X, Z = R0
    Xp, Zp = R1
    A = (X + Z) % _PRIME
    AA = A * A % _PRIME
    B = (X - Z) % _PRIME
    BB = B * B % _PRIME
    E = (AA - BB) % _PRIME
    C = (Xp + Zp) % _PRIME
    D2 = (Xp - Zp) % _PRIME
    DA = D2 * A % _PRIME
    CB = C * B % _PRIME
    X5 = (DA + CB) ** 2 % _PRIME
    Z5 = u_int * (DA - CB) ** 2 % _PRIME
    X4 = AA * BB % _PRIME
    Z4 = E * (AA + 121665 * E) % _PRIME
    return (X4, Z4), (X5, Z5)


def _x25519_scalar_mult(scalar_bytes: bytes, u_bytes: bytes) -> bytes:
    """Скалярное умножение X25519 (Montgomery ladder)."""
    u_int = int.from_bytes(u_bytes, "little")
    k_int = int.from_bytes(scalar_bytes, "little")
    R0, R1 = (1, 0), (u_int, 1)
    for bit_pos in range(254, -1, -1):
        if (k_int >> bit_pos) & 1:
            R1, R0 = _x25519_ladder_step(R1, R0, u_int)
        else:
            R0, R1 = _x25519_ladder_step(R0, R1, u_int)
    X, Z = R0
    res = (X * pow(Z, _PRIME - 2, _PRIME)) % _PRIME
    return res.to_bytes(32, "little")


def _b64url_encode(raw: bytes) -> str:
    """Base64url без padding (стандарт для xray/reality ключей)."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    """Base64url декодер с автоматическим padding."""
    padding = 4 - len(s) % 4
    if padding < 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# ── Публичный API ─────────────────────────────────────────────

def gen_x25519_keypair() -> tuple[str, str]:
    """Пара ключей X25519. Пробует xray, затем cryptography, затем pure-python."""
    # 1. Пробуем xray binary (самый надёжный источник)
    for xray_bin in ["/usr/local/bin/xray", "/usr/bin/xray", "xray"]:
        try:
            r = subprocess.run([xray_bin, "x25519"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                priv = pub = ""
                for line in r.stdout.splitlines():
                    if "Private" in line:
                        priv = line.split(":")[-1].strip()
                    if "Public" in line:
                        pub = line.split(":")[-1].strip()
                if priv and pub:
                    return priv, pub
        except Exception:
            pass

    # 2. Пробуем cryptography library
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption)
        priv_key = X25519PrivateKey.generate()
        pub_key = priv_key.public_key()
        priv_b = priv_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub_b = pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64url_encode(priv_b), _b64url_encode(pub_b)
    except Exception:
        pass

    # 3. Fallback: pure-python (единая реализация — без дупликации)
    raw_priv = _clamp(os.urandom(32))
    raw_pub = _x25519_scalar_mult(raw_priv, _BASE_POINT)
    return _b64url_encode(raw_priv), _b64url_encode(raw_pub)


def derive_public_key(private_key_b64: str) -> str:
    """Вычисляет публичный ключ X25519 из приватного (base64url).

    Использует ту же единую реализацию что и gen_x25519_keypair.
    """
    # 1. Пробуем xray binary
    for xray_bin in ["/usr/local/bin/xray", "/usr/bin/xray", "xray"]:
        try:
            r = subprocess.run([xray_bin, "x25519", "-i", private_key_b64],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if "Public" in line:
                        return line.split(":")[-1].strip()
        except Exception:
            pass

    # 2. Pure-python fallback (единая реализация)
    try:
        priv_bytes = _b64url_decode(private_key_b64)[:32]
        clamped = _clamp(priv_bytes)
        raw_pub = _x25519_scalar_mult(clamped, _BASE_POINT)
        return _b64url_encode(raw_pub)
    except Exception:
        return ""
