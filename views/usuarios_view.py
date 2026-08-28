from tkinter import ttk, messagebox
import mysql.connector
import customtkinter as ctk
from database.connection import obter_conexao
from utils.security import gerar_hash_senha

class JanelaUsuarios(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Gerenciamento de Usuários")
        self.after(0, lambda: self.wm_state('zoomed'))
        self.transient(parent)
        self.id_sel = None

        f_form = ctk.CTkFrame(self, width=260)
        f_form.pack(side="left", fill="y", padx=10, pady=10)

        ctk.CTkLabel(f_form, text="Usuário", font=("Arial", 14, "bold")).pack(pady=10)
        
        # Campo Nome Completo
        self.e_nome = ctk.CTkEntry(f_form, placeholder_text="Nome Completo", width=220)
        self.e_nome.pack(pady=5)

        # Campo Login
        self.e_login = ctk.CTkEntry(f_form, placeholder_text="Login", width=220)
        self.e_login.pack(pady=5)

        # Campo Senha
        self.e_senha = ctk.CTkEntry(f_form, placeholder_text="Senha (Preencha para alterar)", show="*", width=220)
        self.e_senha.pack(pady=5)
        
        # Campo Perfil
        self.cmb_perfil = ctk.CTkComboBox(f_form, values=["usuario", "admin"], width=220)
        self.cmb_perfil.pack(pady=5)

        self.btn_salvar = ctk.CTkButton(f_form, text="Salvar Novo", command=self.salvar, fg_color="green", width=220)
        self.btn_salvar.pack(pady=10)
        self.btn_atualizar = ctk.CTkButton(f_form, text="Atualizar", command=self.atualizar, fg_color="orange", width=220)
        self.btn_atualizar.pack(pady=3)
        ctk.CTkButton(f_form, text="Excluir", command=self.excluir, fg_color="red", width=220).pack(pady=3)
        ctk.CTkButton(f_form, text="Limpar Seleção", command=self.limpar, fg_color="gray", width=220).pack(pady=3)

        f_list = ctk.CTkFrame(self)
        f_list.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        # Atualização das colunas para incluir a coluna NOME
        cols = ("id", "nome", "login", "perfil")
        self.tree = ttk.Treeview(f_list, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("id", text="ID"); self.tree.column("id", width=50, anchor="center")
        self.tree.heading("nome", text="NOME COMPLETO"); self.tree.column("nome", width=220)
        self.tree.heading("login", text="LOGIN"); self.tree.column("login", width=150)
        self.tree.heading("perfil", text="PERFIL"); self.tree.column("perfil", width=100)
        self.tree.pack(fill="both", expand=True, padx=5, pady=5)
        self.tree.bind("<Double-1>", self.carregar)

        self.limpar()
        self.listar()

    def limpar(self):
        self.id_sel = None
        self.e_nome.delete(0, 'end')
        self.e_login.delete(0, 'end')
        self.e_senha.delete(0, 'end')
        self.cmb_perfil.set("usuario")
        self.btn_salvar.configure(state="normal")

    def listar(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT id, nome, login, perfil FROM tb_usuarios")
                for u in cur.fetchall():
                    self.tree.insert("", "end", values=(u['id'], u.get('nome', ''), u['login'], u['perfil']))
            finally:
                cur.close(); con.close()

    def carregar(self, event):
        sel = self.tree.selection()
        if sel:
            v = self.tree.item(sel[0], "values")
            self.id_sel = v[0]
            self.e_nome.delete(0, 'end'); self.e_nome.insert(0, v[1])
            self.e_login.delete(0, 'end'); self.e_login.insert(0, v[2])
            self.cmb_perfil.set(v[3])
            self.btn_salvar.configure(state="disabled")

    def salvar(self):
        nom, log, sen, perf = self.e_nome.get().strip(), self.e_login.get().strip(), self.e_senha.get().strip(), self.cmb_perfil.get()
        if not nom or not log or not sen: 
            messagebox.showwarning("Aviso", "Preencha todos os campos obrigatórios.", parent=self)
            return
        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                senha_hash = gerar_hash_senha(sen)
                cur.execute("INSERT INTO tb_usuarios (nome, login, senha, perfil) VALUES (%s, %s, %s, %s)", (nom, log, senha_hash, perf))
                con.commit()
                self.limpar(); self.listar()
            except mysql.connector.Error as err:
                messagebox.showerror("Erro", str(err), parent=self)
            finally: cur.close(); con.close()

    def atualizar(self):
        if not self.id_sel: return
        nom, log, sen, perf = self.e_nome.get().strip(), self.e_login.get().strip(), self.e_senha.get().strip(), self.cmb_perfil.get()
        if not nom or not log:
            messagebox.showwarning("Aviso", "Nome e Login são obrigatórios.", parent=self)
            return
        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                if sen:
                    senha_hash = gerar_hash_senha(sen)
                    cur.execute("UPDATE tb_usuarios SET nome=%s, login=%s, senha=%s, perfil=%s WHERE id=%s", (nom, log, senha_hash, perf, self.id_sel))
                else:
                    cur.execute("UPDATE tb_usuarios SET nome=%s, login=%s, perfil=%s WHERE id=%s", (nom, log, perf, self.id_sel))
                con.commit()
                self.limpar(); self.listar()
            finally: cur.close(); con.close()

    def excluir(self):
        if not self.id_sel: return
        if messagebox.askyesno("Confirmar", "Deseja excluir o usuário?", parent=self):
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor()
                    cur.execute("DELETE FROM tb_usuarios WHERE id=%s", (self.id_sel,))
                    con.commit()
                    self.limpar(); self.listar()
                finally: cur.close(); con.close()