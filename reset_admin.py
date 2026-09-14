from database.connection import obter_conexao
from utils.security import gerar_hash_senha, verificar_senha

def recriar_admin():
    con = obter_conexao()
    if not con:
        print("\n❌ ERRO: Não foi possível conectar ao banco de dados.")
        return

    try:
        cur = con.cursor(dictionary=True)
        
        # Mostra em qual banco está conectado
        cur.execute("SELECT DATABASE()")
        banco_atual = cur.fetchone()['DATABASE()']
        print(f"\n📌 Conectado ao banco: {banco_atual}")

        # Recria a tabela garantindo VARCHAR(255) para a senha
        cur.execute("DROP TABLE IF EXISTS tb_usuarios")
        cur.execute("""
            CREATE TABLE tb_usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                login VARCHAR(50) UNIQUE NOT NULL,
                senha VARCHAR(255) NOT NULL,
                perfil VARCHAR(20) NOT NULL
            )
        """)

        # Insere o admin com hash
        senha_plana = "admin123"
        admin_hash = gerar_hash_senha(senha_plana)
        cur.execute(
            "INSERT INTO tb_usuarios (login, senha, perfil) VALUES (%s, %s, %s)",
            ("admin", admin_hash, "admin")
        )
        con.commit()

        # Teste imediato de validação da senha
        cur.execute("SELECT * FROM tb_usuarios WHERE login=%s", ("admin",))
        usr = cur.fetchone()

        if usr and verificar_senha(senha_plana, usr['senha']):
            print("✅ SUCESSO! Tabela recriada e usuário 'admin' autenticado com sucesso.")
            print(f"👉 Login: admin | Senha: {senha_plana}\n")
        else:
            print("❌ ATENÇÃO: Falha na verificação do hash da senha.\n")

    except Exception as e:
        print(f"\n❌ ERRO ao executar script: {e}\n")
    finally:
        cur.close()
        con.close()

if __name__ == "__main__":
    recriar_admin()