import sys
import os
import json
import logging
import threading
from tkinter import ttk, messagebox
import tkinter.font as tkfont
import customtkinter as ctk

from database.connection import obter_conexao
from views.usuarios_view import JanelaUsuarios
from views.diligencias_view import AbaDiligencias
from views.usucapiao_view import AbaUsucapiao
from views.cobranca_view import AbaCobranca
from views.gestao_pessoas_view import AbaGestaoPessoas
from utils.lotacoes import rotulo

# Arquivo simples (JSON) só para lembrar a preferência de tema entre uma
# abertura e outra do programa — antes, a escolha de "Modo Claro" se perdia
# a cada reinício e o app sempre voltava para o tema escuro padrão.
ARQUIVO_PREFERENCIAS_UI = "ui_prefs.json"

# Intervalo (ms) entre checagens automáticas de conexão com o banco, para o
# indicador de status na barra superior.
INTERVALO_CHECAGEM_BD_MS = 30000

class Aplicativo(ctk.CTk):
    def __init__(self, usuario_atual, perfil_atual, nome_atual=None,
                 lotacao_selecionada=None, lotacoes_permitidas=None):
        super().__init__()
        self.usuario_atual = usuario_atual
        self.perfil_atual = perfil_atual
        self.nome_atual = nome_atual if nome_atual else usuario_atual
        self.deseja_fazer_logout = False

        # Lotação escolhida na tela de login (None = todas as permitidas).
        # abas_liberadas define quais chaves de lotação esta sessão pode
        # ver: apenas a selecionada, ou todas as permitidas ao usuário.
        self.lotacao_selecionada = lotacao_selecionada
        self.lotacoes_permitidas = lotacoes_permitidas or []
        if lotacao_selecionada:
            self.abas_liberadas = {lotacao_selecionada}
        elif self.lotacoes_permitidas:
            self.abas_liberadas = set(self.lotacoes_permitidas)
        else:
            self.abas_liberadas = {"diligencias", "usucapiao", "cobranca"}

        # Carrega e aplica a preferência de tema salva ANTES de montar
        # qualquer widget, para a tela já abrir no tema certo em vez de
        # sempre iniciar escura e só depois trocar.
        self.tema_atual = self._carregar_tema_salvo()
        ctk.set_appearance_mode(self.tema_atual)

        self.title("Sistema de Gestão - Diligências, Usucapião e Cobrança de Respostas")
        self.after(0, lambda: self.wm_state('zoomed'))

        # CAPTURA O CLIQUE NO 'X' DA JANELA PARA PERGUNTAR E ENCERRAR O PROGRAMA
        self.protocol("WM_DELETE_WINDOW", self.fechar_programa)

        # Barra Superior
        f_top = ctk.CTkFrame(self, height=40)
        f_top.pack(fill="x", padx=10, pady=(5, 0))

        lbl_user = f"Usuário Logado: {self.nome_atual} ({self.perfil_atual.upper()})"
        ctk.CTkLabel(f_top, text=lbl_user, font=("Arial", 12, "bold")).pack(side="left", padx=15)

        # Indicador de saúde da conexão com o banco de dados — como o
        # sistema depende 100% do MySQL, isso ajuda o usuário a entender
        # rapidamente se uma falha é "o banco caiu" e não um bug pontual.
        self.lbl_status_bd = ctk.CTkLabel(f_top, text="🟡 Verificando conexão...", font=("Arial", 11), text_color="gray")
        self.lbl_status_bd.pack(side="left", padx=10)

        # Só faz sentido oferecer troca de lotação se o usuário tem mais
        # de uma liberada — com apenas uma não há entre o que escolher.
        if len(self.lotacoes_permitidas) > 1:
            ctk.CTkButton(f_top, text="🔀 Alterar Lotação", command=self.abrir_alterar_lotacao, fg_color="#555555", hover_color="#333333", height=28).pack(side="left", padx=10)

        if self.perfil_atual == 'admin':
            ctk.CTkButton(f_top, text="⚙ Gerenciar Usuários", command=self.abrir_gerencial_usuarios, fg_color="#3a7ebf", height=28).pack(side="left", padx=10)

        # Botão Sair (Logout voluntário para trocar de usuário)
        ctk.CTkButton(f_top, text="🚪 Sair", command=self.fazer_logout, fg_color="#a83232", hover_color="#7a2323", height=28, width=70).pack(side="right", padx=10)

        # Switch de Tema — o estado inicial reflete a preferência salva
        # (self.tema_atual), carregada e aplicada no início do __init__.
        self.switch_tema = ctk.CTkSwitch(f_top, text="Modo Claro", command=self.alternar_tema)
        self.switch_tema.pack(side="right", padx=10)
        if self.tema_atual == "light":
            self.switch_tema.select()
        else:
            self.switch_tema.deselect()

        # Botão "Sobre o Sistema"
        ctk.CTkButton(f_top, text="ℹ Sobre", command=self.abrir_sobre, fg_color="#555555", hover_color="#333333", height=28, width=80).pack(side="right", padx=10)

        # Rodapé de Direitos Autorais
        f_footer = ctk.CTkFrame(self, height=20, fg_color="transparent")
        f_footer.pack(side="bottom", fill="x", padx=10, pady=(2, 5))

        lbl_copyright = ctk.CTkLabel(
            f_footer, 
            text="© 2026 - Desenvolvido por Gabriel Melo - Todos os direitos reservados", 
            font=("Arial", 10), 
            text_color="gray"
        )
        lbl_copyright.pack(side="right", padx=10)

        # Tabview
        self.f_tabs = ctk.CTkFrame(self, fg_color="transparent")
        self.f_tabs.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self._criar_abas()

        # Inicia o monitoramento periódico da conexão com o banco (atualiza
        # o indicador 🟢/🔴 na barra superior a cada 30s).
        self._checar_conexao_bd()

        try:
            self.iconbitmap("assets/icon.ico")
        except Exception:
            pass

    def fechar_programa(self):
        """Método acionado ao clicar no 'X' da janela"""
        if messagebox.askyesno("Confirmar Saída", "Você tem certeza que quer sair do programa?"):
            self.deseja_fazer_logout = False
            self.destroy()
            sys.exit(0) # Encerra o processo da aplicação de vez

    def fazer_logout(self):
        """Método acionado ao clicar no botão 'Sair' do topo"""
        if messagebox.askyesno("Logout", "Deseja realmente sair da sua conta?"):
            self.deseja_fazer_logout = True
            self.destroy()

    def abrir_sobre(self):
        janela_sobre = ctk.CTkToplevel(self)
        janela_sobre.title("Sobre o Sistema")
        janela_sobre.geometry("400x260")
        janela_sobre.resizable(False, False)
        janela_sobre.transient(self)

        ctk.CTkLabel(janela_sobre, text="Sistema de Gestão", font=("Arial", 18, "bold")).pack(pady=(20, 5))
        ctk.CTkLabel(janela_sobre, text="Versão 0.3.0", font=("Arial", 11), text_color="gray").pack(pady=(0, 15))

        texto_licenca = (
            "Este software é protegido por leis de direitos autorais.\n"
            "Uso exclusivo e autorizado para a instituição.\n\n"
            "Desenvolvido por: Gabriel Melo"
            "© 2026 Todos os direitos reservados."
        )
        
        ctk.CTkLabel(janela_sobre, text=texto_licenca, font=("Arial", 11), justify="center").pack(pady=10)
        ctk.CTkButton(janela_sobre, text="Fechar", command=janela_sobre.destroy, width=100).pack(pady=10)

    def abrir_gerencial_usuarios(self):
        JanelaUsuarios(self)

    def _criar_abas(self):
        """(Re)monta o tabview só com as abas liberadas para a lotação atual."""
        self.tabview = ctk.CTkTabview(
            self.f_tabs,
            text_color=("black", "white"),
            segmented_button_selected_color=("#1f538d", "#1f538d"),
            segmented_button_unselected_color=("#cccccc", "#4a4d50")
        )
        self.tabview.pack(fill="both", expand=True)

        self.aba_diligencias = None
        self.aba_usucapiao = None
        self.aba_cobranca = None
        self.aba_gestao_pessoas = None

        if "diligencias" in self.abas_liberadas:
            self.tab_diligencias = self.tabview.add("Lotação Diligência")
            self.aba_diligencias = AbaDiligencias(self.tab_diligencias, self)
        if "usucapiao" in self.abas_liberadas:
            self.tab_usucapiao = self.tabview.add("Lotação Usucapião")
            self.aba_usucapiao = AbaUsucapiao(self.tab_usucapiao, self)
        if "cobranca" in self.abas_liberadas:
            self.tab_cobranca = self.tabview.add("Cobrança de Respostas")
            self.aba_cobranca = AbaCobranca(self.tab_cobranca, self)
        if "gestao_pessoas" in self.abas_liberadas:
            self.tab_gestao_pessoas = self.tabview.add("Gestão de Pessoas")
            self.aba_gestao_pessoas = AbaGestaoPessoas(self.tab_gestao_pessoas, self)

        self.configurar_estilo_treeview(self.tema_atual)

    def abrir_alterar_lotacao(self):
        """Janela com checkboxes para escolher quais lotações ficam visíveis."""
        janela = ctk.CTkToplevel(self)
        janela.title("Alterar Lotação")
        janela.geometry("300x300")
        janela.transient(self)

        ctk.CTkLabel(janela, text="Quais lotações devem aparecer?", font=("Arial", 13, "bold")).pack(pady=(20, 10))
        vars_chk = {}
        for chave in self.lotacoes_permitidas:
            var = ctk.BooleanVar(value=chave in self.abas_liberadas)
            ctk.CTkCheckBox(janela, text=rotulo(chave), variable=var, width=240).pack(pady=5, anchor="w", padx=30)
            vars_chk[chave] = var
        ctk.CTkButton(janela, text="Aplicar", width=240,
                      command=lambda: self._trocar_lotacao(vars_chk, janela)).pack(pady=(20, 5))

    def _trocar_lotacao(self, vars_chk, janela):
        selecionadas = {c for c, v in vars_chk.items() if v.get()}
        if not selecionadas:
            messagebox.showwarning("Aviso", "Selecione ao menos uma lotação.")
            return
        janela.destroy()
        self.abas_liberadas = selecionadas
        self.lotacao_selecionada = next(iter(selecionadas)) if len(selecionadas) == 1 else None
        self.tabview.destroy()
        self._criar_abas()

    # ------------------------------------------------------------------
    # PERSISTÊNCIA DE TEMA
    # ------------------------------------------------------------------
    def _carregar_tema_salvo(self):
        """Lê a preferência de tema salva em disco. Padrão: 'dark'."""
        try:
            if os.path.exists(ARQUIVO_PREFERENCIAS_UI):
                with open(ARQUIVO_PREFERENCIAS_UI, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                    tema = dados.get("tema")
                    if tema in ("dark", "light"):
                        return tema
        except (OSError, json.JSONDecodeError) as err:
            logging.warning(f"Não foi possível ler preferências de UI ({ARQUIVO_PREFERENCIAS_UI}): {err}")
        return "dark"

    def _salvar_tema(self, tema):
        """Grava a preferência de tema em disco, para persistir entre reinícios."""
        try:
            with open(ARQUIVO_PREFERENCIAS_UI, "w", encoding="utf-8") as f:
                json.dump({"tema": tema}, f)
        except OSError as err:
            logging.warning(f"Não foi possível salvar preferências de UI ({ARQUIVO_PREFERENCIAS_UI}): {err}")

    # ------------------------------------------------------------------
    # MONITOR DE CONEXÃO COM O BANCO
    # ------------------------------------------------------------------
    def _checar_conexao_bd(self):
        """Dispara, em thread separada, uma checagem rápida da conexão com o banco."""
        threading.Thread(target=self._tarefa_checar_conexao_bd, daemon=True).start()

    def _tarefa_checar_conexao_bd(self):
        conectado = False
        try:
            con = obter_conexao()
            if con:
                con.close()
                conectado = True
        except Exception as err:
            logging.error(f"Erro ao checar conexão com o banco: {err}")
        self.after(0, lambda: self._atualizar_status_bd(conectado))

    def _atualizar_status_bd(self, conectado):
        if not self.lbl_status_bd.winfo_exists():
            return
        if conectado:
            self.lbl_status_bd.configure(text="🟢 Conectado", text_color="#4caf50")
        else:
            self.lbl_status_bd.configure(text="🔴 Sem conexão com o banco", text_color="#e57373")
        # Reagenda a próxima checagem
        self.after(INTERVALO_CHECAGEM_BD_MS, self._checar_conexao_bd)

    def configurar_estilo_treeview(self, modo):
        style = ttk.Style()
        style.theme_use("clam")
        if modo == "dark":
            style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", rowheight=28, font=("Arial", 10))
            style.configure("Treeview.Heading", background="#1f1f1f", foreground="white", font=("Arial", 10, "bold"))
            style.map("Treeview", background=[("selected", "#1f538d")])
            if self.aba_diligencias:
                self.aba_diligencias.tab_dil_tree.tag_configure("vencido", background="#782525", foreground="#ffffff")
        else:
            style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", rowheight=28, font=("Arial", 10))
            style.configure("Treeview.Heading", background="#e0e0e0", foreground="#000000", font=("Arial", 10, "bold"))
            style.map("Treeview", background=[("selected", "#3a7ebf")])
            if self.aba_diligencias:
                self.aba_diligencias.tab_dil_tree.tag_configure("vencido", background="#f8d7da", foreground="#842029")

        # Demais abas com cores de fundo dependentes do tema
        if self.aba_usucapiao:
            self.aba_usucapiao.atualizar_cores_tema("Light" if modo != "dark" else "Dark")
        if self.aba_gestao_pessoas:
            self.aba_gestao_pessoas.renderizar_calendario()

    def alternar_tema(self):
        if self.switch_tema.get() == 1:
            self.tema_atual = "light"
            ctk.set_appearance_mode("light")
            self.configurar_estilo_treeview("light")
        else:
            self.tema_atual = "dark"
            ctk.set_appearance_mode("dark")
            self.configurar_estilo_treeview("dark")
        self._salvar_tema(self.tema_atual)

    def ajustar_colunas_treeview(self, tree):
        fonte = tkfont.Font(font=("Arial", 10))
        for col in tree["columns"]:
            largura_max = fonte.measure(str(tree.heading(col)["text"]))
            for item in tree.get_children():
                val = str(tree.set(item, col))
                largura_max = max(largura_max, fonte.measure(val))
            tree.column(col, width=max(largura_max + 25, 50))