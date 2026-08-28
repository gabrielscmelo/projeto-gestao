import threading
from datetime import datetime
from tkinter import ttk, messagebox
import customtkinter as ctk
from database.connection import obter_conexao
from utils.debouncer import Debouncer
from utils.pdf_exporter import exportar_treeview_pdf
from utils.formatters import aplicar_mascara_data_auto, formatar_e_validar_data, formatar_saj, formatar_sei

class AbaUsucapiao:
    def __init__(self, master, app_instance):
        self.master = master
        self.app = app_instance
        self.id_usucapiao_sel = None
        self.montar_aba()

    def montar_aba(self):
        self.f_form_usu = ctk.CTkFrame(self.master, width=320)
        self.f_form_usu.pack(side="left", fill="y", padx=10, pady=10)

        ctk.CTkLabel(self.f_form_usu, text="Lotação Usucapião", font=("Arial", 18, "bold")).pack(pady=15)

        self.e_saj_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="SAJ (ex: 2025.01.24)", width=260)
        self.e_saj_usu.pack(pady=6)
        self.e_saj_usu.bind("<FocusOut>", lambda e: self.aplicar_mascara_campo(self.e_saj_usu, formatar_saj))

        self.e_sei_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="SEI (ex: 05.123/2024-01)", width=260)
        self.e_sei_usu.pack(pady=6)
        self.e_sei_usu.bind("<FocusOut>", lambda e: self.aplicar_mascara_campo(self.e_sei_usu, formatar_sei))

        self.e_idsei_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Id SEI (documento)", width=260)
        self.e_idsei_usu.pack(pady=6)

        self.e_interessado_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Interessado", width=260)
        self.e_interessado_usu.pack(pady=6)

        self.e_sec_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Secretarias (ex: SEMOC, SEFIN, SEDUR)", width=260)
        self.e_sec_usu.pack(pady=6)
        self.e_sec_usu.bind("<KeyRelease>", self.atualizar_rotulos_secretarias)

        self.e_envio_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Data Envio (DD/MM/AAAA)", width=260)
        self.e_envio_usu.pack(pady=6)
        self.e_envio_usu.bind("<KeyRelease>", aplicar_mascara_data_auto)

        ctk.CTkLabel(self.f_form_usu, text="Tipo de Solicitação:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=(4, 0))
        self.cmb_tipo_usu = ctk.CTkComboBox(self.f_form_usu, values=["Judicial", "Extrajudicial"], width=260)
        self.cmb_tipo_usu.pack(pady=4)

        ctk.CTkLabel(self.f_form_usu, text="Secretarias Respondidas:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=(6, 0))
        
        self.chk_sec1_var = ctk.IntVar(value=0)
        self.chk_sec2_var = ctk.IntVar(value=0)
        self.chk_sec3_var = ctk.IntVar(value=0)

        self.chk_sec1 = ctk.CTkCheckBox(self.f_form_usu, text="1ª Secretaria", variable=self.chk_sec1_var)
        self.chk_sec1.pack(anchor="w", padx=35, pady=2)
        
        self.chk_sec2 = ctk.CTkCheckBox(self.f_form_usu, text="2ª Secretaria", variable=self.chk_sec2_var)
        self.chk_sec2.pack(anchor="w", padx=35, pady=2)

        self.chk_sec3 = ctk.CTkCheckBox(self.f_form_usu, text="3ª Secretaria", variable=self.chk_sec3_var)
        self.chk_sec3.pack(anchor="w", padx=35, pady=2)

        self.btn_salvar_usu = ctk.CTkButton(self.f_form_usu, text="Salvar Novo", command=self.usu_cadastrar, fg_color="green", hover_color="darkgreen", width=260)
        self.btn_salvar_usu.pack(pady=10)
        self.btn_atualizar_usu = ctk.CTkButton(self.f_form_usu, text="Atualizar Selecionado", command=self.usu_atualizar, fg_color="orange", hover_color="#b37400", width=260)
        self.btn_atualizar_usu.pack(pady=4)
        ctk.CTkButton(self.f_form_usu, text="Limpar Campos", command=self.usu_limpar_campos, fg_color="gray", width=260).pack(pady=4)

        self.f_lista_usu = ctk.CTkFrame(self.master)
        self.f_lista_usu.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        f_pesq = ctk.CTkFrame(self.f_lista_usu, fg_color="transparent")
        f_pesq.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(f_pesq, text="Buscar:", font=("Arial", 12, "bold")).pack(side="left", padx=(0, 5))
        self.e_pesq_usu = ctk.CTkEntry(f_pesq, placeholder_text="Digite SAJ, SEI, Interessado...", width=240)
        self.e_pesq_usu.pack(side="left", fill="x", expand=True, padx=5)

        self.debouncer_usu = Debouncer(self.e_pesq_usu)
        self.debouncer_usu.bind(self.usu_listar)

        ctk.CTkLabel(f_pesq, text="Ano:", font=("Arial", 12, "bold")).pack(side="left", padx=(10, 5))
        anos_opcoes = ["Todos"] + [str(y) for y in range(datetime.now().year, 2019, -1)]
        self.cmb_ano_usu = ctk.CTkComboBox(f_pesq, values=anos_opcoes, width=90, command=lambda e: self.usu_listar())
        self.cmb_ano_usu.set("Todos")
        self.cmb_ano_usu.pack(side="left", padx=5)

        cols = ("id", "saj", "sei", "id_sei", "interessado", "secretarias", "envio", "tipo", "status", "enviado_por")
        self.tab_usu_tree = ttk.Treeview(self.f_lista_usu, columns=cols, show="headings", selectmode="browse")
        
        headers = ("ID", "SAJ", "SEI", "ID SEI", "INTERESSADO", "SECRETARIAS", "ENVIO", "TIPO", "STATUS RESPOSTAS", "ENVIADO POR")
        for c, h in zip(cols, headers):
            self.tab_usu_tree.heading(c, text=h)

        self.tab_usu_tree.column("id", width=30, anchor="center")
        self.tab_usu_tree.column("saj", width=110)
        self.tab_usu_tree.column("sei", width=110)
        self.tab_usu_tree.column("id_sei", width=90, anchor="center")
        self.tab_usu_tree.column("interessado", width=120)
        self.tab_usu_tree.column("secretarias", width=120)
        self.tab_usu_tree.column("envio", width=75, anchor="center")
        self.tab_usu_tree.column("tipo", width=85)
        self.tab_usu_tree.column("status", width=160)
        self.tab_usu_tree.column("enviado_por", width=90)

        self.tab_usu_tree.pack(pady=10, padx=10, fill="both", expand=True)
        self.tab_usu_tree.bind("<Double-1>", self.usu_carregar_campos)

        f_act = ctk.CTkFrame(self.f_lista_usu, fg_color="transparent")
        f_act.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_act, text="✔ Concluir Usucapião", command=self.usu_concluir, fg_color="#1f538d", hover_color="#14375e", width=140).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="Ver Concluídas", command=self.usu_abrir_concluidas, fg_color="#454545", hover_color="#2b2b2b", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="📄 Gerar PDF", command=lambda: exportar_treeview_pdf(self.tab_usu_tree, "Relatório de Usucapião"), fg_color="#2e7d32", hover_color="#1b5e20", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="✖ Excluir", command=self.usu_excluir, fg_color="red", hover_color="darkred", width=110).pack(side="right", padx=5)

        self.usu_listar()

    def aplicar_mascara_campo(self, widget, funcao_mascara):
        val = widget.get()
        if val.strip():
            widget.delete(0, 'end'); widget.insert(0, funcao_mascara(val))

    def atualizar_rotulos_secretarias(self, event=None):
        texto = self.e_sec_usu.get().strip()
        secs = [s.strip() for s in texto.split(',') if s.strip()]
        self.chk_sec1.configure(text=f"Respondido: {secs[0]}" if len(secs) > 0 else "1ª Secretaria Respondida")
        self.chk_sec2.configure(text=f"Respondido: {secs[1]}" if len(secs) > 1 else "2ª Secretaria Respondida")
        self.chk_sec3.configure(text=f"Respondido: {secs[2]}" if len(secs) > 2 else "3ª Secretaria Respondida")

    def calcular_status_usucapiao(self, s1, s2, s3, lista_sec_str=""):
        secs = [s.strip() for s in lista_sec_str.split(',') if s.strip()]
        respondidas = []
        if s1 and len(secs) > 0: respondidas.append(secs[0])
        elif s1: respondidas.append("Sec 1")
        if s2 and len(secs) > 1: respondidas.append(secs[1])
        elif s2: respondidas.append("Sec 2")
        if s3 and len(secs) > 2: respondidas.append(secs[2])
        elif s3: respondidas.append("Sec 3")

        if len(respondidas) == len(secs) and len(secs) > 0:
            return f"Concluído ({', '.join(respondidas)})"
        elif respondidas:
            return f"Resp: {', '.join(respondidas)}"
        return "Aguardando Respostas"

    def usu_limpar_campos(self):
        self.id_usucapiao_sel = None
        for e in (self.e_saj_usu, self.e_sei_usu, self.e_idsei_usu, self.e_interessado_usu, self.e_sec_usu, self.e_envio_usu):
            e.delete(0, 'end')
        self.cmb_tipo_usu.set("Judicial")
        self.chk_sec1_var.set(0); self.chk_sec2_var.set(0); self.chk_sec3_var.set(0)
        self.atualizar_rotulos_secretarias()
        self.btn_salvar_usu.configure(state="normal")

    def usu_cadastrar(self):
        self.aplicar_mascara_campo(self.e_saj_usu, formatar_saj)
        self.aplicar_mascara_campo(self.e_sei_usu, formatar_sei)

        saj, sei = self.e_saj_usu.get(), self.e_sei_usu.get()
        id_sei = self.e_idsei_usu.get().strip()
        interessado = self.e_interessado_usu.get()
        secretarias = self.e_sec_usu.get()
        tipo = self.cmb_tipo_usu.get()

        if not saj or not interessado:
            messagebox.showwarning("Aviso", "SAJ e Interessado são obrigatórios!"); return

        envio_fmt, e_err = formatar_e_validar_data(self.e_envio_usu.get())
        if e_err: messagebox.showwarning("Data Envio", e_err); return

        s1, s2, s3 = self.chk_sec1_var.get(), self.chk_sec2_var.get(), self.chk_sec3_var.get()
        status_txt = self.calcular_status_usucapiao(s1, s2, s3, secretarias)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                sql = """
                    INSERT INTO tb_usucapiao (saj, sei, `Id SEI`, interessado, secretarias, data_envio, tipo_solicitacao, sec1_ok, sec2_ok, sec3_ok, status, usuario_criacao, data_criacao)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cur.execute(sql, (saj, sei, id_sei, interessado, secretarias, envio_fmt, tipo, s1, s2, s3, status_txt, self.app.usuario_atual))
                con.commit()
                messagebox.showinfo("Sucesso", "Processo cadastrado!")
                self.usu_limpar_campos(); self.usu_listar()
            finally: cur.close(); con.close()

    # LISTAR USANDO THREADING
    def usu_listar(self):
        for item in self.tab_usu_tree.get_children(): self.tab_usu_tree.delete(item)
        busca = self.e_pesq_usu.get().strip()
        ano = self.cmb_ano_usu.get()

        def tarefa_banco():
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    query = "SELECT * FROM tb_usucapiao WHERE 1=1"
                    params = []

                    if busca:
                        query += " AND (saj LIKE %s OR sei LIKE %s OR interessado LIKE %s OR secretarias LIKE %s)"
                        lk = f"%{busca}%"
                        params.extend([lk, lk, lk, lk])
                    if ano and ano != "Todos":
                        query += " AND YEAR(data_envio) = %s"
                        params.append(ano)

                    cur.execute(query, tuple(params))
                    linhas = cur.fetchall()

                    # Atualiza a interface gráfica com segurança
                    self.master.winfo_toplevel().after(0, lambda: self._preencher_tabela(linhas))
                finally: cur.close(); con.close()

        threading.Thread(target=tarefa_banco, daemon=True).start()

    def _preencher_tabela(self, linhas):
        for row in linhas:
            env_str = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else (row['data_envio'] or "")
            st_dinamico = self.calcular_status_usucapiao(row['sec1_ok'], row['sec2_ok'], row['sec3_ok'], row['secretarias'] or "")
            self.tab_usu_tree.insert("", "end", values=(
                row['id'], row['saj'], row['sei'], row.get('Id SEI') or "-", row['interessado'],
                row['secretarias'], env_str, row['tipo_solicitacao'], st_dinamico, row['usuario_criacao'] or "N/A"
            ))

    def usu_carregar_campos(self, event):
        sel = self.tab_usu_tree.selection()
        if sel:
            v = self.tab_usu_tree.item(sel[0], "values")
            self.id_usucapiao_sel = v[0]
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    cur.execute("SELECT * FROM tb_usucapiao WHERE id=%s", (self.id_usucapiao_sel,))
                    reg = cur.fetchone()
                    if reg:
                        self.e_saj_usu.delete(0, 'end'); self.e_saj_usu.insert(0, reg['saj'])
                        self.e_sei_usu.delete(0, 'end'); self.e_sei_usu.insert(0, reg['sei'])
                        self.e_idsei_usu.delete(0, 'end'); self.e_idsei_usu.insert(0, reg.get('Id SEI') or "")
                        self.e_interessado_usu.delete(0, 'end'); self.e_interessado_usu.insert(0, reg['interessado'])
                        self.e_sec_usu.delete(0, 'end'); self.e_sec_usu.insert(0, reg['secretarias'])
                        
                        env_str = reg['data_envio'].strftime('%d/%m/%Y') if hasattr(reg['data_envio'], 'strftime') else (reg['data_envio'] or "")
                        self.e_envio_usu.delete(0, 'end'); self.e_envio_usu.insert(0, env_str)
                        
                        self.cmb_tipo_usu.set(reg['tipo_solicitacao'])
                        self.chk_sec1_var.set(reg['sec1_ok'])
                        self.chk_sec2_var.set(reg['sec2_ok'])
                        self.chk_sec3_var.set(reg['sec3_ok'])
                        self.atualizar_rotulos_secretarias()
                        self.btn_salvar_usu.configure(state="disabled")
                finally: cur.close(); con.close()

    def usu_atualizar(self):
        if not self.id_usucapiao_sel: messagebox.showwarning("Aviso", "Selecione um processo."); return
        self.aplicar_mascara_campo(self.e_saj_usu, formatar_saj)
        self.aplicar_mascara_campo(self.e_sei_usu, formatar_sei)

        envio_fmt, e_err = formatar_e_validar_data(self.e_envio_usu.get())
        if e_err: messagebox.showwarning("Data Envio", e_err); return

        s1, s2, s3 = self.chk_sec1_var.get(), self.chk_sec2_var.get(), self.chk_sec3_var.get()
        secretarias = self.e_sec_usu.get()
        status_txt = self.calcular_status_usucapiao(s1, s2, s3, secretarias)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                sql = """
                    UPDATE tb_usucapiao 
                    SET saj=%s, sei=%s, `Id SEI`=%s, interessado=%s, secretarias=%s, data_envio=%s, tipo_solicitacao=%s, sec1_ok=%s, sec2_ok=%s, sec3_ok=%s, status=%s
                    WHERE id=%s
                """
                cur.execute(sql, (
                    self.e_saj_usu.get(), self.e_sei_usu.get(), self.e_idsei_usu.get().strip(), self.e_interessado_usu.get(),
                    secretarias, envio_fmt, self.cmb_tipo_usu.get(), s1, s2, s3, status_txt, self.id_usucapiao_sel
                ))
                con.commit()
                messagebox.showinfo("Sucesso", "Processo atualizado!")
                self.usu_limpar_campos(); self.usu_listar()
            finally: cur.close(); con.close()

    def usu_excluir(self):
        sel = self.tab_usu_tree.selection()
        if not sel: messagebox.showwarning("Aviso", "Selecione um processo."); return
        item_id = self.tab_usu_tree.item(sel[0], "values")[0]
        if messagebox.askyesno("Confirmar", "Tem certeza que deseja excluir?"):
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor()
                    cur.execute("DELETE FROM tb_usucapiao WHERE id=%s", (item_id,))
                    con.commit()
                    self.usu_limpar_campos(); self.usu_listar()
                finally: cur.close(); con.close()

    def usu_concluir(self):
        sel = self.tab_usu_tree.selection()
        if not sel: messagebox.showwarning("Aviso", "Selecione um processo."); return
        item_id = self.tab_usu_tree.item(sel[0], "values")[0]

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_usucapiao WHERE id=%s", (item_id,))
                reg = cur.fetchone()

                if reg['sec1_ok'] + reg['sec2_ok'] + reg['sec3_ok'] < 3:
                    if not messagebox.askyesno("Respostas Pendentes", "Nem todas as secretarias responderam. Concluir mesmo assim?"): return

                if messagebox.askyesno("Concluir Usucapião", "Marcar como CONCLUÍDO e mover para o histórico?"):
                    sql = """
                        INSERT INTO tb_usucapiao_concluidas (id, saj, sei, `Id SEI`, interessado, secretarias, data_envio, tipo_solicitacao, usuario_criacao, data_criacao, usuario_conclusao, data_conclusao)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """
                    cur.execute(sql, (
                        reg['id'], reg['saj'], reg['sei'], reg.get('Id SEI', ''), reg['interessado'], reg['secretarias'], 
                        reg['data_envio'], reg['tipo_solicitacao'], reg['usuario_criacao'], reg['data_criacao'], self.app.usuario_atual
                    ))
                    cur.execute("DELETE FROM tb_usucapiao WHERE id=%s", (item_id,))
                    con.commit()
                    self.usu_limpar_campos(); self.usu_listar()
            finally: cur.close(); con.close()

    def usu_abrir_concluidas(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Usucapião - Histórico de Concluídas")
        janela.after(0, lambda: janela.wm_state('zoomed'))
        janela.transient(self.master)

        ctk.CTkLabel(janela, text="Histórico de Usucapião Concluídos", font=("Arial", 16, "bold")).pack(pady=10)
        tb_conc = ttk.Treeview(janela, columns=("id", "saj", "sei", "id_sei", "interessado", "sec", "envio", "tipo", "enviado_por", "concluido_por", "conclusao"), show="headings")
        
        headers = ("ID", "SAJ", "SEI", "ID SEI", "Interessado", "Secretarias", "Envio", "Tipo", "Enviado por", "Concluído por", "Data Conclusão")
        for col, t in zip(("id", "saj", "sei", "id_sei", "interessado", "sec", "envio", "tipo", "enviado_por", "concluido_por", "conclusao"), headers):
            tb_conc.heading(col, text=t)
            
        tb_conc.pack(fill="both", expand=True, padx=10, pady=5)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_usucapiao_concluidas")
                for row in cur.fetchall():
                    env = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else ""
                    conc = row['data_conclusao'].strftime('%d/%m/%Y %H:%M') if hasattr(row['data_conclusao'], 'strftime') else ""
                    tb_conc.insert("", "end", values=(
                        row['id'], row['saj'], row['sei'], row.get('Id SEI') or "-", row['interessado'], row['secretarias'], 
                        env, row['tipo_solicitacao'], row['usuario_criacao'] or "N/A", row['usuario_conclusao'] or "N/A", conc
                    ))
            finally: cur.close(); con.close()

        self.app.ajustar_colunas_treeview(tb_conc)

        def restaurar():
            sel = tb_conc.selection()
            if not sel: return
            item_id = tb_conc.item(sel[0], "values")[0]
            if messagebox.askyesno("Restaurar", "Devolver este processo para a fila ativa de Usucapião?", parent=janela):
                c = obter_conexao()
                if c:
                    try:
                        cr = c.cursor(dictionary=True)
                        cr.execute("SELECT * FROM tb_usucapiao_concluidas WHERE id=%s", (item_id,))
                        reg = cr.fetchone()
                        
                        sql = """
                            INSERT INTO tb_usucapiao (id, saj, sei, `Id SEI`, interessado, secretarias, data_envio, tipo_solicitacao, sec1_ok, sec2_ok, sec3_ok, status, usuario_criacao, data_criacao)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 1, 1, 'Restaurado (3/3)', %s, %s)
                        """
                        cr.execute(sql, (
                            reg['id'], reg['saj'], reg['sei'], reg.get('Id SEI', ''), reg['interessado'], reg['secretarias'], 
                            reg['data_envio'], reg['tipo_solicitacao'], reg['usuario_criacao'], reg['data_criacao']
                        ))
                        cr.execute("DELETE FROM tb_usucapiao_concluidas WHERE id=%s", (item_id,))
                        c.commit()
                        janela.destroy(); self.usu_listar()
                    finally: cr.close(); c.close()

        if self.app.perfil_atual == 'admin':
            ctk.CTkButton(janela, text="↩ Restaurar Selecionado (Admin)", command=restaurar, fg_color="#b37400", hover_color="orange").pack(pady=10)
        else:
            ctk.CTkLabel(janela, text="* Apenas usuários administradores podem restaurar processos concluídos.", text_color="gray").pack(pady=10)