import sys
from tkinter import ttk, messagebox
import tkinter.font as tkfont
import customtkinter as ctk

from views.usuarios_view import JanelaUsuarios
from views.diligencias_view import AbaDiligencias
from views.usucapiao_view import AbaUsucapiao
from views.cobranca_view import AbaCobranca

class Aplicativo(ctk.CTk):
    def __init__(self, usuario_atual, perfil_atual, nome_atual=None):
        super().__init__()
        self.usuario_atual = usuario_atual
        self.perfil_atual = perfil_atual
        self.nome_atual = nome_atual if nome_atual else usuario_atual
        self.deseja_fazer_logout = False

        self.title("Sistema de Gestão - Diligências, Usucapião e Cobrança de Respostas")
        self.after(0, lambda: self.wm_state('zoomed'))

        # CAPTURA O CLIQUE NO 'X' DA JANELA PARA PERGUNTAR E ENCERRAR O PROGRAMA
        self.protocol("WM_DELETE_WINDOW", self.fechar_programa)

        # Barra Superior
        f_top = ctk.CTkFrame(self, height=40)
        f_top.pack(fill="x", padx=10, pady=(5, 0))

        lbl_user = f"Usuário Logado: {self.nome_atual} ({self.perfil_atual.upper()})"
        ctk.CTkLabel(f_top, text=lbl_user, font=("Arial", 12, "bold")).pack(side="left", padx=15)

        if self.perfil_atual == 'admin':
            ctk.CTkButton(f_top, text="⚙ Gerenciar Usuários", command=self.abrir_gerencial_usuarios, fg_color="#3a7ebf", height=28).pack(side="left", padx=10)

        # Botão Sair (Logout voluntário para trocar de usuário)
        ctk.CTkButton(f_top, text="🚪 Sair", command=self.fazer_logout, fg_color="#a83232", hover_color="#7a2323", height=28, width=70).pack(side="right", padx=10)

        # Switch de Tema
        self.switch_tema = ctk.CTkSwitch(f_top, text="Modo Claro", command=self.alternar_tema)
        self.switch_tema.pack(side="right", padx=10)

        # Botão "Sobre o Sistema"
        ctk.CTkButton(f_top, text="ℹ Sobre", command=self.abrir_sobre, fg_color="#555555", hover_color="#333333", height=28, width=80).pack(side="right", padx=10)

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

        # Tabview
        self.tabview = ctk.CTkTabview(
            self,
            text_color=("black", "white"),
            segmented_button_selected_color=("#1f538d", "#1f538d"),
            segmented_button_unselected_color=("#cccccc", "#4a4d50")
        )
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self.tab_diligencias = self.tabview.add("Lotação Diligência")
        self.tab_usucapiao = self.tabview.add("Lotação Usucapião")
        self.tab_cobranca = self.tabview.add("Cobrança de Respostas")

        # Inicializa Abas
        self.aba_diligencias = AbaDiligencias(self.tab_diligencias, self)
        self.aba_usucapiao = AbaUsucapiao(self.tab_usucapiao, self)
        self.aba_cobranca = AbaCobranca(self.tab_cobranca, self)

        self.configurar_estilo_treeview("dark")

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
            "Desenvolvido por: Gabriel Melo\n"
            "© 2026 Todos os direitos reservados."
        )
        
        ctk.CTkLabel(janela_sobre, text=texto_licenca, font=("Arial", 11), justify="center").pack(pady=10)
        ctk.CTkButton(janela_sobre, text="Fechar", command=janela_sobre.destroy, width=100).pack(pady=10)

    def abrir_gerencial_usuarios(self):
        JanelaUsuarios(self)

    def configurar_estilo_treeview(self, modo):
        style = ttk.Style()
        style.theme_use("clam")
        if modo == "dark":
            style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", rowheight=28, font=("Arial", 10))
            style.configure("Treeview.Heading", background="#1f1f1f", foreground="white", font=("Arial", 10, "bold"))
            style.map("Treeview", background=[("selected", "#1f538d")])
            self.aba_diligencias.tab_dil_tree.tag_configure("vencido", background="#782525", foreground="#ffffff")
        else:
            style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", rowheight=28, font=("Arial", 10))
            style.configure("Treeview.Heading", background="#e0e0e0", foreground="#000000", font=("Arial", 10, "bold"))
            style.map("Treeview", background=[("selected", "#3a7ebf")])
            self.aba_diligencias.tab_dil_tree.tag_configure("vencido", background="#f8d7da", foreground="#842029")

    def alternar_tema(self):
        if self.switch_tema.get() == 1:
            ctk.set_appearance_mode("light")
            self.configurar_estilo_treeview("light")
        else:
            ctk.set_appearance_mode("dark")
            self.configurar_estilo_treeview("dark")

    def ajustar_colunas_treeview(self, tree):
        fonte = tkfont.Font(font=("Arial", 10))
        for col in tree["columns"]:
            largura_max = fonte.measure(str(tree.heading(col)["text"]))
            for item in tree.get_children():
                val = str(tree.set(item, col))
                largura_max = max(largura_max, fonte.measure(val))
            tree.column(col, width=max(largura_max + 25, 50))