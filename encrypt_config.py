# encrypt_config.py
from cryptography.fernet import Fernet

# Gera a chave (rode isso só uma vez e guarde a chave)
key = Fernet.generate_key()
with open("secret.key", "wb") as f:
    f.write(key)

# Criptografa o config.ini
with open("config.ini", "rb") as f:
    data = f.read()

fernet = Fernet(key)
encrypted = fernet.encrypt(data)

with open("config.ini.enc", "wb") as f:
    f.write(encrypted)

print("config.ini.enc gerado com sucesso.")