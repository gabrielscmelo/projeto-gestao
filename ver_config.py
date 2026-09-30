from cryptography.fernet import Fernet
chave = open("secret.key", "rb").read()
ini = Fernet(chave).decrypt(open("config.ini.enc", "rb").read()).decode()
print(ini)
