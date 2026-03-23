"""X25519 key generation and UUID helpers."""

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


def gen_x25519_keypair():
    """X25519 keypair. Tries xray binary, then cryptography lib, then pure-python."""
    for xray_bin in ["/usr/local/bin/xray", "/usr/bin/xray", "xray"]:
        try:
            r = subprocess.run([xray_bin, "x25519"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                priv = pub = ""
                for line in r.stdout.splitlines():
                    if "Private" in line: priv = line.split(":")[-1].strip()
                    if "Public"  in line: pub  = line.split(":")[-1].strip()
                if priv and pub:
                    return priv, pub
        except Exception:
            pass

    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption)
        priv_key = X25519PrivateKey.generate()
        pub_key  = priv_key.public_key()
        priv_b = priv_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub_b  = pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return (base64.urlsafe_b64encode(priv_b).decode().rstrip("="),
                base64.urlsafe_b64encode(pub_b).decode().rstrip("="))
    except Exception:
        pass

    return _pure_python_x25519_keypair()


def _pure_python_x25519_keypair():
    """Pure-Python Montgomery ladder (RFC 7748)."""
    PRIME = (2 ** 255) - 19

    def clamp(k: bytes) -> bytes:
        b = bytearray(k)
        b[0] &= 248; b[31] &= 127; b[31] |= 64
        return bytes(b)

    def ladder_step(R0, R1, u_int):
        X,  Z  = R0
        Xp, Zp = R1
        A  = (X  + Z)  % PRIME;  AA = A  * A  % PRIME
        B  = (X  - Z)  % PRIME;  BB = B  * B  % PRIME
        E  = (AA - BB) % PRIME
        C  = (Xp + Zp) % PRIME;  D2 = (Xp - Zp) % PRIME
        DA = D2 * A % PRIME;      CB = C  * B  % PRIME
        X5 = (DA + CB) ** 2               % PRIME
        Z5 = u_int * (DA - CB) ** 2       % PRIME
        X4 = AA * BB                      % PRIME
        Z4 = E  * (AA + 121665 * E)       % PRIME
        return (X4, Z4), (X5, Z5)

    def x25519_mul(scalar_bytes: bytes, u_bytes: bytes) -> bytes:
        u_int = int.from_bytes(u_bytes,    'little')
        k_int = int.from_bytes(scalar_bytes, 'little')
        R0, R1 = (1, 0), (u_int, 1)
        for bit_pos in range(254, -1, -1):
            if (k_int >> bit_pos) & 1:
                R1, R0 = ladder_step(R1, R0, u_int)
            else:
                R0, R1 = ladder_step(R0, R1, u_int)
        X, Z = R0
        res = (X * pow(Z, PRIME - 2, PRIME)) % PRIME
        return res.to_bytes(32, 'little')

    raw_priv = clamp(os.urandom(32))
    raw_pub  = x25519_mul(raw_priv, (9).to_bytes(32, 'little'))
    return (base64.urlsafe_b64encode(raw_priv).decode().rstrip("="),
            base64.urlsafe_b64encode(raw_pub).decode().rstrip("="))


def derive_public_key(private_key_b64: str) -> str:
    """Compute X25519 public key from private (base64url)."""
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
    try:
        priv_bytes = base64.urlsafe_b64decode(private_key_b64 + "==")[:32]
        PRIME = (2**255) - 19

        def _ls(R0, R1, u):
            X, Z = R0; Xp, Zp = R1
            A = (X+Z) % PRIME; AA = A*A % PRIME; B = (X-Z) % PRIME; BB = B*B % PRIME
            E = (AA-BB) % PRIME; C = (Xp+Zp) % PRIME; D2 = (Xp-Zp) % PRIME
            DA = D2*A % PRIME; CB = C*B % PRIME
            return (AA*BB % PRIME, E*(AA+121665*E) % PRIME), ((DA+CB)**2 % PRIME, u*(DA-CB)**2 % PRIME)

        b = bytearray(priv_bytes); b[0] &= 248; b[31] &= 127; b[31] |= 64
        k = int.from_bytes(bytes(b), 'little')
        R0, R1 = (1, 0), (9, 1)
        for i in range(254, -1, -1):
            if (k >> i) & 1:
                R1, R0 = _ls(R1, R0, 9)
            else:
                R0, R1 = _ls(R0, R1, 9)
        X, Z = R0
        res = (X * pow(Z, PRIME-2, PRIME)) % PRIME
        return base64.urlsafe_b64encode(res.to_bytes(32, 'little')).decode().rstrip("=")
    except Exception:
        return ""
