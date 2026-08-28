import logging
import customtkinter as ctk
from tkinter import messagebox
from database.connection import obter_conexao
from utils.security import verificar_senha

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
        ctk.CTkButton(self, text="Entrar", command=self.autenticar, width=280, height=40, font=("Arial", 12, "bold")).pack(pady=10)

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
        login = self.e_login.get().strip()
        senha = self.e_senha.get().strip()

        if not login or not senha:
            messagebox.showwarning("Aviso", "Preencha o login e a senha.")
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_usuarios WHERE login=%s", (login,))
                usuario = cur.fetchone()

                if usuario and verificar_senha(senha, usuario['senha']):
                    nome_completo = usuario.get('nome') or usuario['login']
                    self.nome_logado = nome_completo.strip().split()[0].capitalize()

                    self.usuario_logado = usuario['login']
                    self.perfil_logado = usuario['perfil']

                    logging.info(f"Usuário '{self.nome_logado}' ({login}) autenticado.")
                    self.destroy()
                else:
                    messagebox.showerror("Erro de Acesso", "Login ou senha incorretos.")
            finally:
                cur.close()
                con.close()