import os
import logging
import configparser

# Configuração de Logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# Leitura do INI
config = configparser.ConfigParser()
CONFIG_FILE = "config.ini"

if os.path.exists(CONFIG_FILE):
    config.read(CONFIG_FILE, encoding="utf-8")
else:
    logging.warning(f"O arquivo '{CONFIG_FILE}' não foi encontrado.")