from tkinter import ttk, messagebox
import mysql.connector
import customtkinter as ctk
from database.connection import obter_conexao
from utils.security import gerar_hash_senha
from utils.troca_senha import gerar_senha_aleatoria
from utils.lotacoes import LOTACOES, LOTACOES_CHAVES, normalizar_lotacoes, formatar_lista

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
        self.e_senha = ctk.CTkEntry(f_form, placeholder_text="Nova senha (só para redefinir)", show="*", width=220)
        self.e_senha.pack(pady=5)
        
        # Campo Perfil
        self.cmb_perfil = ctk.CTkComboBox(f_form, values=["usuario", "admin"], width=220)
        self.cmb_perfil.pack(pady=5)

        # Lotações visíveis para o usuário — define quais abas (Diligências,
        # Usucapião, Cobrança de Respostas) ele verá ao entrar no sistema.
        # Se nenhuma marcação for salva (ou o usuário for legado, cadastrado
        # antes dessa funcionalidade existir), o acesso é a TODAS as
        # lotações, para não quebrar contas já existentes.
        ctk.CTkLabel(f_form, text="Lotações Visíveis:", font=("Arial", 12, "bold")).pack(anchor="w", padx=20, pady=(12, 2))
        self._chk_lotacao_vars = {}
        for chave, rotulo_txt in LOTACOES:
            var = ctk.IntVar(value=1)  # padrão: acesso total ao cadastrar um novo usuário
            chk = ctk.CTkCheckBox(f_form, text=rotulo_txt, variable=var)
            chk.pack(anchor="w", padx=25, pady=2)
            self._chk_lotacao_vars[chave] = var

        self.btn_salvar = ctk.CTkButton(f_form, text="Salvar Novo", command=self.salvar, fg_color="green", width=220)
        self.btn_salvar.pack(pady=10)
        self.btn_atualizar = ctk.CTkButton(f_form, text="Atualizar", command=self.atualizar, fg_color="orange", width=220)
        self.btn_atualizar.pack(pady=3)
        ctk.CTkButton(f_form, text="Excluir", command=self.excluir, fg_color="red", width=220).pack(pady=3)
        ctk.CTkButton(f_form, text="Limpar Seleção", command=self.limpar, fg_color="gray", width=220).pack(pady=3)

        f_list = ctk.CTkFrame(self)
        f_list.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        # Atualização das colunas para incluir a coluna NOME e LOTAÇÕES
        cols = ("id", "nome", "login", "perfil", "lotacoes")
        self.tree = ttk.Treeview(f_list, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("id", text="ID"); self.tree.column("id", width=50, anchor="center")
        self.tree.heading("nome", text="NOME COMPLETO"); self.tree.column("nome", width=220)
        self.tree.heading("login", text="LOGIN"); self.tree.column("login", width=150)
        self.tree.heading("perfil", text="PERFIL"); self.tree.column("perfil", width=100)
        self.tree.heading("lotacoes", text="LOTAÇÕES"); self.tree.column("lotacoes", width=220)
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
        for var in self._chk_lotacao_vars.values():
            var.set(1)
        self.btn_salvar.configure(state="normal")

    def listar(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT id, nome, login, perfil, lotacoes FROM tb_usuarios")
                for u in cur.fetchall():
                    self.tree.insert("", "end", values=(
                        u['id'], u.get('nome', ''), u['login'], u['perfil'],
                        formatar_lista(u.get('lotacoes'))
                    ))
            except mysql.connector.Error as err:
                messagebox.showerror(
                    "Erro no Banco",
                    "Falha ao listar usuários. Se o erro mencionar a coluna "
                    "'lotacoes', é necessário criá-la na tabela tb_usuarios "
                    "(ver instruções de migração).\n\n" + str(err),
                    parent=self
                )
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

            # Busca o valor bruto de lotações (a coluna da Treeview já vem
            # formatada como texto amigável, não como as chaves originais).
            con = obter_conexao()
            lotacoes_brutas = None
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    cur.execute("SELECT lotacoes FROM tb_usuarios WHERE id=%s", (self.id_sel,))
                    reg = cur.fetchone()
                    lotacoes_brutas = reg['lotacoes'] if reg else None
                except mysql.connector.Error:
                    pass
                finally:
                    cur.close(); con.close()

            chaves_marcadas = normalizar_lotacoes(lotacoes_brutas)
            for chave, var in self._chk_lotacao_vars.items():
                var.set(1 if chave in chaves_marcadas else 0)

            self.btn_salvar.configure(state="disabled")

    def _lotacoes_selecionadas_str(self):
        """Monta a string separada por vírgulas com as chaves marcadas."""
        return ",".join(chave for chave in LOTACOES_CHAVES if self._chk_lotacao_vars[chave].get())

    def salvar(self):
        nom, log, sen, perf = self.e_nome.get().strip(), self.e_login.get().strip(), self.e_senha.get().strip(), self.cmb_perfil.get()
        if not nom or not log:
            messagebox.showwarning("Aviso", "Preencha todos os campos obrigatórios.", parent=self)
            return

        lotacoes_str = self._lotacoes_selecionadas_str()
        if not lotacoes_str:
            messagebox.showwarning("Aviso", "Marque ao menos uma lotação para o usuário — sem nenhuma, ele não conseguirá acessar nenhuma aba do sistema.", parent=self)
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                sen = gerar_senha_aleatoria()
                cur.execute(
                    "INSERT INTO tb_usuarios (nome, login, senha, perfil, lotacoes, trocar_senha, senha_alterada_em) "
                    "VALUES (%s, %s, %s, %s, %s, 1, NOW())",
                    (nom, log, gerar_hash_senha(sen), perf, lotacoes_str)
                )
                con.commit()
                messagebox.showinfo("Senha temporária", f"Usuário criado.\n\nSenha temporária: {sen}\n\nRepasse ao usuário; ela não será exibida novamente.", parent=self)
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

        lotacoes_str = self._lotacoes_selecionadas_str()
        if not lotacoes_str:
            messagebox.showwarning("Aviso", "Marque ao menos uma lotação para o usuário — sem nenhuma, ele não conseguirá acessar nenhuma aba do sistema.", parent=self)
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                if sen:
                    senha_hash = gerar_hash_senha(sen)
                    cur.execute(
                        "UPDATE tb_usuarios SET nome=%s, login=%s, senha=%s, perfil=%s, lotacoes=%s, trocar_senha=1, senha_alterada_em=NOW() WHERE id=%s",
                        (nom, log, senha_hash, perf, lotacoes_str, self.id_sel)
                    )
                else:
                    cur.execute(
                        "UPDATE tb_usuarios SET nome=%s, login=%s, perfil=%s, lotacoes=%s WHERE id=%s",
                        (nom, log, perf, lotacoes_str, self.id_sel)
                    )
                con.commit()
                self.limpar(); self.listar()
            except mysql.connector.Error as err:
                messagebox.showerror("Erro", str(err), parent=self)
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
                except mysql.connector.Error as err:
                    messagebox.showerror("Erro", str(err), parent=self)
                finally: cur.close(); con.close()