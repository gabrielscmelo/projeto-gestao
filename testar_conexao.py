import configparser
from cryptography.fernet import Fernet
import mysql.connector
import os

try:
    print("1. A ler chave e descriptografar...")
    with open("secret.key", "rb") as f:
        key = f.read()
    
    fernet = Fernet(key)

    # Verifica qual ficheiro criptografado está a ser usado
    arquivo_enc = "config.ini.enc" if os.path.exists("config.ini.enc") else "config.ini"
    
    with open(arquivo_enc, "rb") as f:
        dados_encriptados = f.read()
    
    dados_descriptografados = fernet.decrypt(dados_encriptados).decode("utf-8")
    
    # Carrega as configurações
    config = configparser.ConfigParser()
    config.read_string(dados_descriptografados)
    
    db_config = config['database']
    print(f"-> Host: {db_config['host']}")
    print(f"-> User: {db_config['user']}")
    print(f"-> Database: {db_config['database']}")
    print(f"-> Password lida (tamanho): {len(db_config['password'])} caracteres")

    print("\n2. A tentar conectar ao MySQL...")
    conn = mysql.connector.connect(
        host=db_config['host'],
        port=int(db_config['port']),
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )
    print("SUCESSO: Conexão ao MySQL estabelecida com êxito!")
    conn.close()

except Exception as e:
    print(f"\nERRO ENCONTRADO: {e}")