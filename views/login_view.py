import logging
import threading
import time
import customtkinter as ctk
from tkinter import messagebox
import mysql.connector
from database.connection import obter_conexao
from utils.security import verificar_senha

# Após esse número de tentativas erradas seguidas, o login fica bloqueado
# temporariamente. O tempo de bloqueio dobra a cada novo bloqueio (30s, 60s,
# 120s...) até um teto, para dificultar tentativas automatizadas de força
# bruta sem travar permanentemente um usuário que só errou a senha.
MAX_TENTATIVAS = 5
BLOQUEIO_INICIAL_SEGUNDOS = 30
BLOQUEIO_MAXIMO_SEGUNDOS = 300


class TelaLogin(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Acesso ao Sistema")
        self.geometry("380x450") # Ajustado para acomodar a caixa de seleção
        self.resizable(False, False)

        # Adiciona o ícone se ele existir
        try:
            self.iconbitmap("assets/icon.ico")
        except Exception:
            pass

        self.nome_logado = None
        self.usuario_logado = None
        self.perfil_logado = None

        # Controle de tentativas de login malsucedidas, para bloqueio
        # progressivo (ver constantes no topo do arquivo).
        self._tentativas_falhas = 0
        self._bloqueio_segundos = BLOQUEIO_INICIAL_SEGUNDOS
        self._bloqueado_ate = None
        self._tick_bloqueio_id = None

        ctk.CTkLabel(self, text="Sistema de Gestão", font=("Arial", 22, "bold")).pack(pady=(25, 5))
        ctk.CTkLabel(self, text="Informe suas credenciais para acessar", font=("Arial", 11), text_color="gray").pack(pady=(0, 20))

        # Campo de Login
        self.e_login = ctk.CTkEntry(self, placeholder_text="Login", width=280, height=35)
        self.e_login.pack(pady=8)

        # Campo de Senha
        self.e_senha = ctk.CTkEntry(self, placeholder_text="Senha", show="*", width=280, height=35)
        self.e_senha.pack(pady=8)

        # CAIXA DE SELEÇÃO: MOSTRAR SENHA
        self.chk_mostrar_senha_var = ctk.BooleanVar(value=False)
        self.chk_mostrar_senha = ctk.CTkCheckBox(
            self,
            text="Mostrar Senha",
            variable=self.chk_mostrar_senha_var,
            command=self.alternar_mostrar_senha,
            font=("Arial", 11),
            checkbox_width=18,
            checkbox_height=18
        )
        # Alinhado à esquerda na largura do campo de texto
        self.chk_mostrar_senha.pack(pady=(2, 15), padx=50, anchor="w")

        # Botão Entrar
        self.btn_entrar = ctk.CTkButton(self, text="Entrar", command=self.autenticar, width=280, height=40, font=("Arial", 12, "bold"))
        self.btn_entrar.pack(pady=10)

        # Mensagem de status (ex.: "Autenticando...", contagem de bloqueio)
        self.lbl_status = ctk.CTkLabel(self, text="", font=("Arial", 10), text_color="#e0a030")
        self.lbl_status.pack(pady=(0, 4))

        # Atalho da tecla Enter
        self.bind("<Return>", lambda event: self.autenticar())

        # Rodapé de Direitos Autorais
        f_footer = ctk.CTkFrame(self, height=20, fg_color="transparent")
        f_footer.pack(side="bottom", fill="x", padx=10, pady=(2, 5))

        lbl_copyright = ctk.CTkLabel(
            f_footer, 
            text="© 2026 - Desenvolvido por Gabriel Costa | Todos os direitos reservados", 
            font=("Arial", 10), 
            text_color="gray"
        )
        lbl_copyright.pack(side="right", padx=10)        

    def alternar_mostrar_senha(self):
        """Alterna a exibição dos caracteres da senha"""
        if self.chk_mostrar_senha_var.get():
            self.e_senha.configure(show="")
        else:
            self.e_senha.configure(show="*")

    def autenticar(self):
        # Se está bloqueado por tentativas repetidas, nem chega a consultar
        # o banco — só reforça quanto tempo falta.
        if self._bloqueado_ate is not None:
            restante = int(self._bloqueado_ate - time.time())
            if restante > 0:
                self.lbl_status.configure(text=f"Aguarde {restante}s para tentar novamente.")
                return
            self._bloqueado_ate = None

        login = self.e_login.get().strip()
        senha = self.e_senha.get().strip()

        if not login or not senha:
            messagebox.showwarning("Aviso", "Preencha o login e a senha.")
            return

        # A consulta ao banco roda em uma thread separada para a janela de
        # login não congelar caso a rede/banco estejam lentos — antes disso,
        # autenticar() rodava inteiramente na thread principal da UI.
        self.btn_entrar.configure(state="disabled", text="Autenticando...")
        self.lbl_status.configure(text="")

        def _tarefa_autenticacao():
            con = obter_conexao()
            if con is None:
                self.after(0, lambda: self._tratar_resultado(
                    erro="Não foi possível conectar ao banco de dados. Verifique sua conexão e tente novamente."
                ))
                return
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_usuarios WHERE login=%s", (login,))
                usuario = cur.fetchone()

                if usuario and verificar_senha(senha, usuario['senha']):
                    nome_completo = usuario.get('nome') or usuario['login']
                    self.after(0, lambda: self._tratar_resultado(sucesso=True, usuario=usuario, nome_completo=nome_completo, login=login))
                else:
                    self.after(0, lambda: self._tratar_resultado(sucesso=False))
            except mysql.connector.Error as err:
                logging.error(f"Erro ao autenticar: {err}")
                self.after(0, lambda: self._tratar_resultado(erro=f"Falha ao consultar o banco de dados:\n{err}"))
            finally:
                cur.close()
                con.close()

        threading.Thread(target=_tarefa_autenticacao, daemon=True).start()

    def _tratar_resultado(self, sucesso=False, erro=None, usuario=None, nome_completo=None, login=None):
        """
        Executa sempre na thread principal (via self.after), atualizando a
        interface com o resultado da tentativa de login.
        """
        self.btn_entrar.configure(state="normal", text="Entrar")

        if erro:
            self.lbl_status.configure(text="")
            messagebox.showerror("Erro de Conexão", erro)
            return

        if sucesso:
            self._tentativas_falhas = 0
            self._bloqueio_segundos = BLOQUEIO_INICIAL_SEGUNDOS
            self.lbl_status.configure(text="")
            self.nome_logado = nome_completo.strip().split()[0].capitalize()
            self.usuario_logado = usuario['login']
            self.perfil_logado = usuario['perfil']
            logging.info(f"Usuário '{self.nome_logado}' ({login}) autenticado.")
            self.destroy()
            return

        # Falha de credenciais: incrementa contador e, se atingir o limite,
        # bloqueia temporariamente novas tentativas.
        self._tentativas_falhas += 1
        restantes = MAX_TENTATIVAS - self._tentativas_falhas

        if restantes <= 0:
            self._bloqueado_ate = time.time() + self._bloqueio_segundos
            self._tentativas_falhas = 0
            self._bloqueio_segundos = min(self._bloqueio_segundos * 2, BLOQUEIO_MAXIMO_SEGUNDOS)
            self._atualizar_contagem_bloqueio()
            messagebox.showerror("Erro de Acesso", "Muitas tentativas incorretas. Login temporariamente bloqueado.")
        else:
            self.lbl_status.configure(text=f"Login ou senha incorretos. Tentativas restantes: {restantes}")
            messagebox.showerror("Erro de Acesso", "Login ou senha incorretos.")

    def _atualizar_contagem_bloqueio(self):
        """Atualiza a cada segundo a mensagem com o tempo restante de bloqueio."""
        if self._bloqueado_ate is None:
            self.lbl_status.configure(text="")
            return

        restante = int(self._bloqueado_ate - time.time())
        if restante <= 0:
            self._bloqueado_ate = None
            self.lbl_status.configure(text="")
            self.btn_entrar.configure(state="normal")
            return

        self.btn_entrar.configure(state="disabled")
        self.lbl_status.configure(text=f"Bloqueado por tentativas incorretas. Aguarde {restante}s.")
        self._tick_bloqueio_id = self.after(1000, self._atualizar_contagem_bloqueio)