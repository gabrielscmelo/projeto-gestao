import logging
import threading
import time
import customtkinter as ctk
from tkinter import messagebox
import mysql.connector
from database.connection import obter_conexao
from utils.security import verificar_senha
from utils.troca_senha import senha_expirada, exigir_troca_senha
from utils.lotacoes import LOTACOES, normalizar_lotacoes, rotulo

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

        # Lotações que o usuário autenticado pode acessar (definidas no
        # cadastro, em Gerenciar Usuários) e a lotação escolhida para a
        # sessão atual — None significa "mostrar todas as permitidas".
        self.lotacoes_permitidas = []
        self.lotacao_selecionada = None

        # Controle de tentativas de login malsucedidas, para bloqueio
        # progressivo (ver constantes no topo do arquivo).
        self._tentativas_falhas = 0
        self._bloqueio_segundos = BLOQUEIO_INICIAL_SEGUNDOS
        self._bloqueado_ate = None
        self._tick_bloqueio_id = None

        # Container que agrupa TODOS os widgets do formulário de login, para
        # poder escondê-los de uma vez e mostrar a tela de seleção de
        # lotação no lugar, sem abrir uma segunda janela.
        self.f_login_container = ctk.CTkFrame(self, fg_color="transparent")
        self.f_login_container.pack(fill="both", expand=True)

        ctk.CTkLabel(self.f_login_container, text="Sistema de Gestão", font=("Arial", 22, "bold")).pack(pady=(25, 5))
        ctk.CTkLabel(self.f_login_container, text="Informe suas credenciais para acessar", font=("Arial", 11), text_color="gray").pack(pady=(0, 20))

        # Campo de Login
        self.e_login = ctk.CTkEntry(self.f_login_container, placeholder_text="Login", width=280, height=35)
        self.e_login.pack(pady=8)

        # Campo de Senha
        self.e_senha = ctk.CTkEntry(self.f_login_container, placeholder_text="Senha", show="*", width=280, height=35)
        self.e_senha.pack(pady=8)

        # CAIXA DE SELEÇÃO: MOSTRAR SENHA
        self.chk_mostrar_senha_var = ctk.BooleanVar(value=False)
        self.chk_mostrar_senha = ctk.CTkCheckBox(
            self.f_login_container,
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
        self.btn_entrar = ctk.CTkButton(self.f_login_container, text="Entrar", command=self.autenticar, width=280, height=40, font=("Arial", 12, "bold"))
        self.btn_entrar.pack(pady=10)

        # Mensagem de status (ex.: "Autenticando...", contagem de bloqueio)
        self.lbl_status = ctk.CTkLabel(self.f_login_container, text="", font=("Arial", 10), text_color="#e0a030")
        self.lbl_status.pack(pady=(0, 4))

        # Tela de seleção de lotação — construída aqui mas só populada e
        # exibida depois da autenticação (ver _mostrar_selecao_lotacao).
        # Fica fora do f_login_container para poder alternar entre as duas.
        self.f_selecao_lotacao = ctk.CTkFrame(self, fg_color="transparent")

        # Atalho da tecla Enter
        self.bind("<Return>", lambda event: self.autenticar())

        # Rodapé de Direitos Autorais
        f_footer = ctk.CTkFrame(self, height=20, fg_color="transparent")
        f_footer.pack(side="bottom", fill="x", padx=10, pady=(2, 5))

        lbl_copyright = ctk.CTkLabel(
            f_footer, 
            text="© 2026 - Desenvolvido por Gabriel Melo | Todos os direitos reservados", 
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
            if senha_expirada(usuario.get('trocar_senha'), usuario.get('senha_alterada_em')):
                if not exigir_troca_senha(self, usuario['id']):
                    return  # cancelou a troca: não entra
            self.nome_logado = nome_completo.strip().split()[0].capitalize()
            self.usuario_logado = usuario['login']
            self.perfil_logado = usuario['perfil']
            logging.info(f"Usuário '{self.nome_logado}' ({login}) autenticado.")

            self.lotacoes_permitidas = normalizar_lotacoes(usuario.get('lotacoes'))

            if len(self.lotacoes_permitidas) <= 1:
                # Só uma lotação liberada (ou nenhuma configurada, o que
                # por retrocompatibilidade equivale a "todas" — mas nesse
                # caso normalizar_lotacoes já devolveria a lista completa,
                # então "<=1" aqui só cobre o caso de exatamente 1
                # lotação, sem necessidade de perguntar nada ao usuário).
                self.lotacao_selecionada = self.lotacoes_permitidas[0] if self.lotacoes_permitidas else None
                self.destroy()
            else:
                self._mostrar_selecao_lotacao()
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

    # ------------------------------------------------------------------
    # SELEÇÃO DE LOTAÇÃO (pós-login, quando o usuário tem mais de uma)
    # ------------------------------------------------------------------
    def _mostrar_selecao_lotacao(self):
        """
        Troca a tela de login pela tela de seleção de lotação. Usada apenas
        quando o usuário autenticado tem MAIS DE UMA lotação liberada — com
        apenas uma, o sistema já entra direto nela (ver _tratar_resultado).
        """
        self.f_login_container.pack_forget()
        # A tela de seleção tem mais elementos que o formulário de login
        # (um botão por lotação + as opções extras), então a janela precisa
        # crescer um pouco para não cortar conteúdo — ela volta ao tamanho
        # original em _voltar_para_login.
        self.geometry("380x600")

        # Limpa qualquer conteúdo anterior (ex.: se o usuário voltou e
        # logou de novo com outra conta) antes de reconstruir os botões.
        for widget in self.f_selecao_lotacao.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            self.f_selecao_lotacao, text=f"Bem-vindo(a), {self.nome_logado}!",
            font=("Arial", 18, "bold")
        ).pack(pady=(35, 5))
        ctk.CTkLabel(
            self.f_selecao_lotacao, text="Em qual lotação você vai trabalhar agora?",
            font=("Arial", 12), text_color="gray"
        ).pack(pady=(0, 20))

        for chave in self.lotacoes_permitidas:
            ctk.CTkButton(
                self.f_selecao_lotacao, text=rotulo(chave), width=280, height=40,
                font=("Arial", 12, "bold"),
                command=lambda c=chave: self._confirmar_lotacao(c)
            ).pack(pady=6)

        # Opção de ver todas as lotações liberadas ao mesmo tempo (o
        # comportamento antigo, com todas as abas abertas de uma vez) —
        # útil para quem tem múltiplas lotações e não quer escolher uma só
        # a cada login.
        ctk.CTkButton(
            self.f_selecao_lotacao, text="🗂 Ver todas as minhas lotações", width=280, height=36,
            fg_color="#454545", hover_color="#2b2b2b",
            command=lambda: self._confirmar_lotacao(None)
        ).pack(pady=(14, 6))

        ctk.CTkButton(
            self.f_selecao_lotacao, text="↩ Voltar e entrar com outra conta", width=280,
            fg_color="transparent", hover_color="#2b2b2b", text_color="gray",
            command=self._voltar_para_login
        ).pack(pady=(10, 0))

        self.f_selecao_lotacao.pack(fill="both", expand=True)

    def _confirmar_lotacao(self, chave):
        """
        chave=None significa "ver todas as lotações liberadas para este
        usuário nesta sessão"; qualquer outra chave restringe a sessão a
        essa única lotação/aba.
        """
        self.lotacao_selecionada = chave
        self.destroy()

    def _voltar_para_login(self):
        """Desfaz a autenticação atual e volta para a tela de usuário/senha."""
        self.nome_logado = None
        self.usuario_logado = None
        self.perfil_logado = None
        self.lotacoes_permitidas = []
        self.lotacao_selecionada = None
        self.e_senha.delete(0, 'end')

        self.f_selecao_lotacao.pack_forget()
        self.geometry("380x450")
        self.f_login_container.pack(fill="both", expand=True)