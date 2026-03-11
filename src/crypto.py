"""
Módulo de cifrado para proteger contraseñas almacenadas localmente.
Usa Fernet (AES-128-CBC + HMAC-SHA256) con clave aleatoria por usuario.
"""
from pathlib import Path
from cryptography.fernet import Fernet

_KEY_FILE = Path.home() / ".server_monitor" / "secret.key"


def _get_or_create_key() -> bytes:
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    _KEY_FILE.chmod(0o600)
    return key


def encrypt_password(password: str) -> str:
    f = Fernet(_get_or_create_key())
    return f.encrypt(password.encode("utf-8")).decode("utf-8")


def decrypt_password(token: str) -> str:
    f = Fernet(_get_or_create_key())
    return f.decrypt(token.encode("utf-8")).decode("utf-8")
