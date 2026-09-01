import os
import sys
import logging
import configparser
from cryptography.fernet import Fernet

# Configuração de Logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)


def resource_path(filename):
    """Resolve o caminho de um arquivo tanto rodando como .py quanto empacotado com PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    return os.path.join(base, filename)


CONFIG_FILE_ENC = "config.ini.enc"
CHAVE_FILE = "secret.key"

# Leitura do INI criptografado
config = configparser.ConfigParser()

caminho_key = resource_path(CHAVE_FILE)
caminho_enc = resource_path(CONFIG_FILE_ENC)

if os.path.exists(caminho_key) and os.path.exists(caminho_enc):
    try:
        with open(caminho_key, "rb") as f:
            chave = f.read()
        with open(caminho_enc, "rb") as f:
            dados_criptografados = f.read()

        fernet = Fernet(chave)
        dados_ini = fernet.decrypt(dados_criptografados).decode("utf-8")
        config.read_string(dados_ini)
    except Exception as e:
        logging.error(f"Falha ao descriptografar '{CONFIG_FILE_ENC}': {e}")
else:
    logging.warning(f"'{CHAVE_FILE}' e/ou '{CONFIG_FILE_ENC}' não encontrados em: {resource_path('')}")