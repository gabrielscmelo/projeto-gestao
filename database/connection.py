import logging
from tkinter import messagebox
import mysql.connector
from mysql.connector import pooling
from config.settings import config
from utils.security import gerar_hash_senha

pool_db = None

def obter_conexao():
    global pool_db
    try:
        if not pool_db:
            pool_db = pooling.MySQLConnectionPool(
                pool_name="pool_gestao",
                pool_size=10,
                host=config.get("DATABASE", "host", fallback="localhost"),
                user=config.get("DATABASE", "user", fallback="root"),
                password=config.get("DATABASE", "password", fallback=""),
                database=config.get("DATABASE", "database", fallback="db_gestao"),
                port=config.getint("DATABASE", "port", fallback=3306)
            )
        return pool_db.get_connection()
    except mysql.connector.Error as erro:
        logging.error(f"Erro no Pool de Conexão: {erro}")
        messagebox.showerror("Erro de Conexão", f"Erro ao conectar ao banco de dados unificado:\n{erro}")
        return None

def inicializar_banco_dados():
    con = obter_conexao()
    if con:
        try:
            cur = con.cursor()
            
            # 1. Tabela de Usuários
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_usuarios (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nome VARCHAR(100) NOT NULL,
                    login VARCHAR(50) UNIQUE NOT NULL,
                    senha VARCHAR(255) NOT NULL,
                    perfil VARCHAR(20) NOT NULL
                )
            """)
            try:
                cur.execute("ALTER TABLE tb_usuarios ADD COLUMN nome VARCHAR(100) DEFAULT 'Administrador'")
            except Exception:
                pass

            cur.execute("SELECT COUNT(*) FROM tb_usuarios")
            if cur.fetchone()[0] == 0:
                admin_hash = gerar_hash_senha("root")
                cur.execute(
                    "INSERT INTO tb_usuarios (nome, login, senha, perfil) VALUES (%s, %s, %s, %s)",
                    ("Administrador", "admin", admin_hash, "admin")
                )

            # 2. Tabela de Diligências (Ativas)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_gestao (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    saj VARCHAR(50),
                    sei VARCHAR(50),
                    diligencia VARCHAR(50),
                    secretaria VARCHAR(100),
                    data_envio DATE,
                    prazo DATE,
                    usuario_criacao VARCHAR(50),
                    data_criacao DATETIME
                )
            """)

            # 3. Tabela de Diligências (Concluídas)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_concluidas (
                    id INT PRIMARY KEY,
                    saj VARCHAR(50),
                    sei VARCHAR(50),
                    diligencia VARCHAR(50),
                    secretaria VARCHAR(100),
                    data_envio DATE,
                    prazo DATE,
                    usuario_criacao VARCHAR(50),
                    data_criacao DATETIME,
                    usuario_conclusao VARCHAR(50),
                    data_conclusao DATETIME
                )
            """)

            # 4. Tabela de Respostas de Cobrança
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_cobranca_respostas (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    diligencia_id INT,
                    saj VARCHAR(50),
                    sei VARCHAR(50),
                    orgao_secretaria VARCHAR(100),
                    data_cobranca DATE,
                    meio_cobranca VARCHAR(50),
                    dias_prorrogados INT,
                    observacao TEXT,
                    usuario_criacao VARCHAR(50),
                    data_criacao DATETIME
                )
            """)

            # 5. Tabela de Usucapião (Ativas)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_usucapiao (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    saj VARCHAR(50),
                    sei VARCHAR(50),
                    interessado VARCHAR(150),
                    secretarias VARCHAR(255),
                    data_envio DATE,
                    tipo_solicitacao VARCHAR(50),
                    sec1_ok TINYINT(1) DEFAULT 0,
                    sec2_ok TINYINT(1) DEFAULT 0,
                    sec3_ok TINYINT(1) DEFAULT 0,
                    status VARCHAR(100),
                    usuario_criacao VARCHAR(50),
                    data_criacao DATETIME
                )
            """)

            # 6. Tabela de Usucapião (Concluídas)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tb_usucapiao_concluidas (
                    id INT PRIMARY KEY,
                    saj VARCHAR(50),
                    sei VARCHAR(50),
                    interessado VARCHAR(150),
                    secretarias VARCHAR(255),
                    data_envio DATE,
                    tipo_solicitacao VARCHAR(50),
                    usuario_criacao VARCHAR(50),
                    data_criacao DATETIME,
                    usuario_conclusao VARCHAR(50),
                    data_conclusao DATETIME
                )
            """)

            # Usuário Admin Padrão
            cur.execute("SELECT COUNT(*) FROM tb_usuarios")
            if cur.fetchone()[0] == 0:
                admin_hash = gerar_hash_senha("admin123")
                cur.execute("INSERT INTO tb_usuarios (login, senha, perfil) VALUES (%s, %s, %s)", ("admin", admin_hash, "admin"))
            
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro na inicialização DDL: {err}")
        finally:
            cur.close()
            con.close()