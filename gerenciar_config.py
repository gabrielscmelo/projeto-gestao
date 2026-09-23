import os
from cryptography.fernet import Fernet

def atualizar_ip_servidor(novo_ip, arquivo_config="config.ini.enc", arquivo_chave="secret.key"):
    # 1. Verifica se a chave secreta existe
    if not os.path.exists(arquivo_chave):
        print(f"Erro: O arquivo de chave '{arquivo_chave}' não foi encontrado.")
        return

    # 2. Carrega a chave Fernet
    with open(arquivo_chave, "rb") as f:
        key = f.read()
    
    fernet = Fernet(key)

    # 3. Lê o conteúdo criptografado do config.ini
    try:
        with open(arquivo_config, "rb") as f:
            conteudo_encriptado = f.read()

        # Descriptografa o conteúdo para texto puro
        conteudo_descriptografado = fernet.decrypt(conteudo_encriptado).decode("utf-8")
    except Exception as e:
        print(f"Erro ao descriptografar o arquivo: {e}")
        return

    # 4. Substitui 'localhost' ou '127.0.0.1' pelo novo IP do servidor
    conteudo_atualizado = conteudo_descriptografado.replace("localhost", novo_ip)
    conteudo_atualizado = conteudo_atualizado.replace("127.0.0.1", novo_ip)

    # 5. Criptografa novamente o arquivo com o IP atualizado
    novo_conteudo_encriptado = fernet.encrypt(conteudo_atualizado.encode("utf-8"))

    # 6. Salva de volta no arquivo de configuração
    with open(arquivo_config, "wb") as f:
        f.write(novo_conteudo_encriptado)

    print(f"Sucesso! O banco de dados foi redirecionado para {novo_ip} e re-criptografado.")

if __name__ == "__main__":
    # Solcita o IP do servidor ao executar
    ip_servidor = input("Digite o IP do Servidor MySQL (ex: 192.168.1.50): ").strip()
    if ip_servidor:
        atualizar_ip_servidor(ip_servidor)
    else:
        print("Nenhum IP informado. Operação cancelada.")