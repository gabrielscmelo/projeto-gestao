import secrets
import hashlib
import logging
import os

def gerar_hash_senha(senha: str) -> str:
    salt = os.urandom(16)
    hash_obj = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + hash_obj.hex()

def verificar_senha(senha_digitada: str, hash_armazenado: str) -> bool:
    try:
        if not hash_armazenado or ":" not in hash_armazenado:
            return False
        salt_hex, hash_hex = hash_armazenado.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        novo_hash = hashlib.pbkdf2_hmac('sha256', senha_digitada.encode('utf-8'), salt, 100000)
        return novo_hash.hex() == hash_hex
    except Exception:
        return False