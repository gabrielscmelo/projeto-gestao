import re
import logging
import threading
from datetime import datetime
from tkinter import ttk, messagebox
import mysql.connector
import customtkinter as ctk

from database.connection import obter_conexao
from utils.debouncer import Debouncer
from utils.pdf_exporter import exportar_treeview_pdf
from utils.formatters import aplicar_mascara_data_auto, calcular_status_diligencia, formatar_e_validar_data
from services.robo_cobranca import iniciar_automacao_completa


class AbaCobranca:
    def __init__(self, master, app_instance):
        self.master = master
        self.app = app_instance
        self.id_diligencia_cob_sel = None
        self.janela_log = None
        self.txt_log_robo = None
        self.montar_aba()

    def montar_aba(self):
        # ----------------------------------------------------------------------
        # PAINEL ESQUERDO: FORMULÁRIO E AUTOMAÇÃO (RO LÁVEL PARA CABER TUDO)
        # ----------------------------------------------------------------------
        self.f_form_cob = ctk.CTkScrollableFrame(self.master, width=320)
        self.f_form_cob.pack(side="left", fill="y", padx=10, pady=10)

        ctk.CTkLabel(self.f_form_cob, text="Cobrança de Resposta", font=("Arial", 18, "bold")).pack(pady=(10, 5))

        # --- SEÇÃO DE AUTOMAÇÃO SEI / SAJ ---
        ctk.CTkLabel(self.f_form_cob, text="🤖 Automação Robô (Em Lote)", font=("Arial", 12, "bold"), text_color="#3b82f6").pack(anchor="w", padx=15, pady=(5, 2))
        
        self.e_user_sei = ctk.CTkEntry(self.f_form_cob, placeholder_text="Usuário / CPF SEI", width=260)
        self.e_user_sei.pack(pady=2)

        self.e_pass_sei = ctk.CTkEntry(self.f_form_cob, placeholder_text="Senha SEI", show="*", width=260)
        self.e_pass_sei.pack(pady=2)

        self.btn_executar_robo = ctk.CTkButton(
            self.f_form_cob, 
            text="🚀 Executar Robô em Lote", 
            command=self.cob_executar_robo_automacao, 
            fg_color="#1f538d", 
            hover_color="#14375e", 
            width=260,
            font=("Arial", 12, "bold")
        )
        self.btn_executar_robo.pack(pady=(4, 8))

        # Linha Divisória
        ctk.CTkFrame(self.f_form_cob, height=2, fg_color="#333333").pack(fill="x", padx=10, pady=6)

        # --- SEÇÃO DE REGISTRO MANUAL ---
        ctk.CTkLabel(self.f_form_cob, text="📝 Registro Manual", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))

        self.e_saj_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="SAJ", width=260)
        self.e_saj_cob.pack(pady=2)

        self.e_sei_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="SEI", width=260)
        self.e_sei_cob.pack(pady=2)

        self.e_orgao_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="Órgão / Secretaria Cobrada", width=260)
        self.e_orgao_cob.pack(pady=2)

        self.e_caminho_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="Caminho / Pasta", width=260)
        self.e_caminho_cob.pack(pady=2)

        self.e_data_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="Data Cobrança (DD/MM/AAAA)", width=260)
        self.e_data_cob.pack(pady=2)
        self.e_data_cob.insert(0, datetime.now().strftime("%d/%m/%Y"))
        self.e_data_cob.bind("<KeyRelease>", aplicar_mascara_data_auto)

        ctk.CTkLabel(self.f_form_cob, text="Meio de Cobrança:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(2, 0))
        self.cmb_meio_cob = ctk.CTkComboBox(self.f_form_cob, values=["E-mail", "Telefone", "Ofício", "Sistema SEI", "Presencial"], width=260)
        self.cmb_meio_cob.pack(pady=2)

        ctk.CTkLabel(self.f_form_cob, text="Prorrogação para Nova Cobrança:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(2, 0))
        self.cmb_prazo_cob = ctk.CTkComboBox(self.f_form_cob, values=["+5 dias", "+10 dias", "+15 dias", "+30 dias"], width=260)
        self.cmb_prazo_cob.pack(pady=2)
        self.cmb_prazo_cob.set("+5 dias")

        self.e_obs_cob = ctk.CTkEntry(self.f_form_cob, placeholder_text="Observações / Resumo da Cobrança", width=260)
        self.e_obs_cob.pack(pady=2)

        # --- BOTÕES DE AÇÃO DO REGISTRO MANUAL ---
        self.btn_cobranca_manual = ctk.CTkButton(
            self.f_form_cob, 
            text="📌 Registrar Cobrança Manual", 
            command=self.cob_registrar_cobranca, 
            fg_color="#2e7d32", 
            hover_color="#1b5e20", 
            width=260,
            font=("Arial", 12, "bold")
        )
        self.btn_cobranca_manual.pack(pady=(8, 3))

        ctk.CTkButton(self.f_form_cob, text="Ver Histórico de Cobranças", command=self.cob_abrir_historico, fg_color="#454545", hover_color="#2b2b2b", width=260).pack(pady=3)
        ctk.CTkButton(self.f_form_cob, text="Limpar Campos", command=self.cob_limpar_campos, fg_color="gray", width=260).pack(pady=(3, 10))

        # ----------------------------------------------------------------------
        # PAINEL DIREITO: TABELA DE DILIGÊNCIAS EM ATRASO
        # ----------------------------------------------------------------------
        self.f_lista_cob = ctk.CTkFrame(self.master)
        self.f_lista_cob.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        f_pesq = ctk.CTkFrame(self.f_lista_cob, fg_color="transparent")
        f_pesq.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(f_pesq, text="Buscar Diligência em Atraso:", font=("Arial", 12, "bold")).pack(side="left", padx=(0, 5))
        self.e_pesq_cob = ctk.CTkEntry(f_pesq, placeholder_text="Digite SAJ, SEI, Secretaria ou Caminho...", width=240)
        self.e_pesq_cob.pack(side="left", fill="x", expand=True, padx=5)

        self.debouncer_cob = Debouncer(self.e_pesq_cob)
        self.debouncer_cob.bind(self.cob_listar)

        cols = ("id", "saj", "sei", "diligencia", "secretaria", "caminho", "id_sei", "envio", "prazo", "dias_atraso")
        self.tab_cob_tree = ttk.Treeview(self.f_lista_cob, columns=cols, show="headings", selectmode="browse")

        headers = ("ID", "SAJ", "SEI", "DILIGÊNCIA", "SECRETARIA", "CAMINHO", "ID SEI", "ENVIO", "PRAZO VENCIDO", "ATRASO")
        for c, h in zip(cols, headers):
            self.tab_cob_tree.heading(c, text=h)

        self.tab_cob_tree.column("id", width=40, anchor="center")
        self.tab_cob_tree.column("saj", width=110)
        self.tab_cob_tree.column("sei", width=110)
        self.tab_cob_tree.column("diligencia", width=80)
        self.tab_cob_tree.column("secretaria", width=130)
        self.tab_cob_tree.column("caminho", width=110)
        self.tab_cob_tree.column("id_sei", width=90, anchor="center")
        self.tab_cob_tree.column("envio", width=80, anchor="center")
        self.tab_cob_tree.column("prazo", width=80, anchor="center")
        self.tab_cob_tree.column("dias_atraso", width=80, anchor="center")

        self.tab_cob_tree.tag_configure("atrasado", foreground="#ff4d4d")

        self.tab_cob_tree.pack(pady=10, padx=10, fill="both", expand=True)
        self.tab_cob_tree.bind("<<TreeviewSelect>>", self.cob_carregar_campos)
        self.tab_cob_tree.bind("<Double-1>", self.cob_carregar_campos)

        f_act = ctk.CTkFrame(self.f_lista_cob, fg_color="transparent")
        f_act.pack(fill="x", padx=10, pady=(0, 10))

        self.lbl_total_cob = ctk.CTkLabel(f_act, text="Diligências Espelhadas (Em Atraso): 0", font=("Arial", 12, "bold"), text_color="#ffb74d")
        self.lbl_total_cob.pack(side="left", padx=10)

        # Botão de geração de PDF
        ctk.CTkButton(
            f_act, 
            text="📄 Gerar PDF", 
            command=lambda: exportar_treeview_pdf(self.tab_cob_tree, "Relatório de Diligências Pendentes de Cobrança"), 
            fg_color="#2e7d32", 
            hover_color="#1b5e20", 
            width=110
        ).pack(side="right", padx=5)

        # Botão adicional para acesso rápido ao histórico de cobranças na área da tabela
        ctk.CTkButton(
            f_act, 
            text="📜 Histórico de Cobranças", 
            command=self.cob_abrir_historico, 
            fg_color="#3b82f6", 
            hover_color="#1d4ed8", 
            width=170
        ).pack(side="right", padx=5)

        self.cob_listar()

    # ----------------------------------------------------------------------
    # AUTOMAÇÃO DO ROBÔ
    # ----------------------------------------------------------------------
    def cob_executar_robo_automacao(self):
        usuario_sei = self.e_user_sei.get().strip()
        senha_sei = self.e_pass_sei.get().strip()

        if not usuario_sei or not senha_sei:
            messagebox.showwarning("Credenciais Ausentes", "Informe o Usuário e a Senha do SEI para iniciar a automação.")
            return

        texto_prorrogacao = self.cmb_prazo_cob.get()
        dias_prorrogacao = int(re.sub(r"\D", "", texto_prorrogacao)) if texto_prorrogacao else 5

        self._abrir_janela_log_robo()
        self.btn_executar_robo.configure(state="disabled", text="⏳ Executando Automação...")

        def _rodar_thread():
            try:
                usuario_app = getattr(self.app, 'usuario_atual', 'Robô')
                iniciar_automacao_completa(
                    usuario_sei=usuario_sei,
                    senha_sei=senha_sei,
                    usuario_app=usuario_app,
                    dias_prorrogacao=dias_prorrogacao,
                    callback_log=self.adicionar_log_console
                )
            except Exception as err:
                logging.error(f"Erro na execução do robô: {err}")
                self.adicionar_log_console(f"❌ [Erro Crítico] Falha geral na execução do robô: {err}")
            finally:
                self.master.after(0, self._finalizar_execucao_robo)

        threading.Thread(target=_rodar_thread, daemon=True).start()

    def _abrir_janela_log_robo(self):
        self.janela_log = ctk.CTkToplevel(self.master)
        self.janela_log.title("Console de Automação - SEI ➔ SAJ Virtual")
        self.janela_log.geometry("650x450")
        self.janela_log.transient(self.master)

        lbl = ctk.CTkLabel(self.janela_log, text="Logs de Execução em Tempo Real", font=("Arial", 14, "bold"))
        lbl.pack(pady=(10, 5))

        self.txt_log_robo = ctk.CTkTextbox(self.janela_log, font=("Consolas", 11))
        self.txt_log_robo.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt_log_robo.configure(state="disabled")

    def adicionar_log_console(self, mensagem):
        if self.txt_log_robo and self.txt_log_robo.winfo_exists():
            def _update():
                self.txt_log_robo.configure(state="normal")
                self.txt_log_robo.insert("end", f"{mensagem}\n")
                self.txt_log_robo.see("end")
                self.txt_log_robo.configure(state="disabled")

            self.master.after(0, _update)

    def _finalizar_execucao_robo(self):
        self.btn_executar_robo.configure(state="normal", text="🚀 Executar Robô em Lote")
        self.cob_listar()
        if hasattr(self.app, 'aba_diligencias'):
            self.app.aba_diligencias.dil_listar()

    # ----------------------------------------------------------------------
    # MÉTODOS AUXILIARES DE MANUTENÇÃO E LISTAGEM
    # ----------------------------------------------------------------------
    def cob_limpar_campos(self):
        self.id_diligencia_cob_sel = None
        for e in (self.e_saj_cob, self.e_sei_cob, self.e_orgao_cob, self.e_caminho_cob, self.e_obs_cob):
            e.delete(0, 'end')
        self.e_data_cob.delete(0, 'end')
        self.e_data_cob.insert(0, datetime.now().strftime("%d/%m/%Y"))
        self.cmb_meio_cob.set("E-mail")
        self.cmb_prazo_cob.set("+5 dias")

    def cob_listar(self):
        for item in self.tab_cob_tree.get_children(): 
            self.tab_cob_tree.delete(item)
        busca = self.e_pesq_cob.get().strip()

        def tarefa_banco():
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    query = "SELECT id, saj, sei, diligencia, categoria, secretaria, caminho, `Id SEI` AS id_sei, data_envio, prazo FROM tb_gestao WHERE prazo < CURDATE()"
                    params = []

                    if busca:
                        query += " AND (saj LIKE %s OR sei LIKE %s OR diligencia LIKE %s OR secretaria LIKE %s OR categoria LIKE %s OR caminho LIKE %s)"
                        lk = f"%{busca}%"
                        params.extend([lk, lk, lk, lk, lk, lk])

                    cur.execute(query, tuple(params))
                    atrasadas = cur.fetchall()

                    self.master.after(0, lambda: self._preencher_tabela(atrasadas))
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao listar cobranças: {err}")
                finally: 
                    cur.close()
                    con.close()

        threading.Thread(target=tarefa_banco, daemon=True).start()

    def _preencher_tabela(self, atrasadas):
        for row in atrasadas:
            pz_str = row['prazo'].strftime('%Y-%m-%d') if hasattr(row['prazo'], 'strftime') else str(row['prazo'])
            env_str = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else (row['data_envio'] or "")
            ex_pz = row['prazo'].strftime('%d/%m/%Y') if hasattr(row['prazo'], 'strftime') else pz_str
            _, dias = calcular_status_diligencia(pz_str)

            sec_valor = row.get('secretaria') or "-"
            cam_valor = row.get('caminho') or "-"
            id_sei_valor = row.get('id_sei') or "-"

            self.tab_cob_tree.insert("", "end", values=(
                row['id'], 
                row['saj'] or "-", 
                row['sei'] or "-", 
                row['diligencia'] or "-",
                sec_valor, 
                cam_valor,
                id_sei_valor,
                env_str, 
                ex_pz, 
                f"{abs(dias)}d atrás"
            ), tags=("atrasado",))
        self.lbl_total_cob.configure(text=f"Diligências Espelhadas (Em Atraso): {len(atrasadas)}")

    def cob_carregar_campos(self, event=None):
        sel = self.tab_cob_tree.selection()
        if sel:
            v = self.tab_cob_tree.item(sel[0], "values")
            self.id_diligencia_cob_sel = v[0]
            self.e_saj_cob.delete(0, 'end'); self.e_saj_cob.insert(0, v[1] if v[1] != "-" else "")
            self.e_sei_cob.delete(0, 'end'); self.e_sei_cob.insert(0, v[2] if v[2] != "-" else "")
            self.e_orgao_cob.delete(0, 'end'); self.e_orgao_cob.insert(0, v[4] if v[4] != "-" else "")
            self.e_caminho_cob.delete(0, 'end'); self.e_caminho_cob.insert(0, v[5] if v[5] != "-" else "")

    def cob_registrar_cobranca(self):
        if not self.id_diligencia_cob_sel:
            sel = self.tab_cob_tree.selection()
            if sel:
                v = self.tab_cob_tree.item(sel[0], "values")
                self.id_diligencia_cob_sel = v[0]

        if not self.id_diligencia_cob_sel:
            messagebox.showwarning("Aviso", "Selecione uma diligência na tabela para registrar a cobrança.")
            return

        saj = self.e_saj_cob.get().strip()
        sei = self.e_sei_cob.get().strip()
        orgao = self.e_orgao_cob.get().strip()
        caminho = self.e_caminho_cob.get().strip()
        meio = self.cmb_meio_cob.get()
        obs = self.e_obs_cob.get().strip()

        dias_str = re.sub(r'\D', '', self.cmb_prazo_cob.get())
        dias_adicionais = int(dias_str) if dias_str else 5

        dt_fmt, d_err = formatar_e_validar_data(self.e_data_cob.get())
        if d_err: 
            messagebox.showwarning("Data Cobrança", d_err)
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)

                cur.execute("SELECT saj, sei, secretaria, caminho, `Id SEI` AS id_sei FROM tb_gestao WHERE id = %s", (self.id_diligencia_cob_sel,))
                dados = cur.fetchone() or {}
                saj = saj or dados.get('saj') or ''
                sei = sei or dados.get('sei') or ''
                orgao = orgao or dados.get('secretaria') or ''
                caminho = caminho or dados.get('caminho') or ''
                id_sei = dados.get('id_sei') or ''
                
                sql_log = """
                    INSERT INTO tb_cobranca_respostas 
                    (diligencia_id, saj, sei, orgao_secretaria, caminho, `Id SEI`, data_cobranca, meio_cobranca, dias_prorrogados, observacao, usuario_criacao, data_criacao) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cur.execute(sql_log, (self.id_diligencia_cob_sel, saj, sei, orgao, caminho, id_sei, dt_fmt, meio, dias_adicionais, obs, getattr(self.app, 'usuario_atual', 'N/A')))

                sql_update = """
                    UPDATE tb_gestao 
                    SET prazo = DATE_ADD(CURDATE(), INTERVAL %s DAY) 
                    WHERE id = %s
                """
                cur.execute(sql_update, (dias_adicionais, self.id_diligencia_cob_sel))
                con.commit()

                messagebox.showinfo("Sucesso", f"Cobrança realizada com sucesso!\n\n• Prazo prorrogado por +{dias_adicionais} dias.\n• A diligência retornou à Lotação e a tarja vermelha foi removida.")
                
                self.cob_limpar_campos()
                if hasattr(self.app, 'aba_diligencias'): 
                    self.app.aba_diligencias.dil_listar()
                self.cob_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao salvar cobrança: {err}")
                messagebox.showerror("Erro ao Salvar Cobrança", f"Ocorreu um erro no banco de dados:\n{err}")
            finally: 
                cur.close()
                con.close()

    def cob_abrir_historico(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Histórico de Cobranças Realizadas")
        janela.after(0, lambda: janela.wm_state('zoomed'))
        janela.transient(self.master)

        ctk.CTkLabel(janela, text="Histórico de Cobranças Efetuadas", font=("Arial", 16, "bold")).pack(pady=10)

        cols = ("id", "saj", "sei", "orgao", "caminho", "id_sei", "data_cob", "meio", "prorrogacao", "obs", "por")
        tb_hist = ttk.Treeview(janela, columns=cols, show="headings")

        headers = ("ID", "SAJ", "SEI", "Órgão/Secretaria", "Caminho", "ID SEI", "Data Cobrança", "Meio", "Prorrogação", "Observação", "Registrado Por")
        for c, h in zip(cols, headers):
            tb_hist.heading(c, text=h)

        tb_hist.column("id", width=40, anchor="center")
        tb_hist.column("saj", width=100)
        tb_hist.column("sei", width=100)
        tb_hist.column("orgao", width=110)
        tb_hist.column("caminho", width=110)
        tb_hist.column("id_sei", width=90, anchor="center")
        tb_hist.column("data_cob", width=90, anchor="center")
        tb_hist.column("meio", width=90)
        tb_hist.column("prorrogacao", width=80, anchor="center")
        tb_hist.column("obs", width=140)
        tb_hist.column("por", width=90)

        tb_hist.pack(fill="both", expand=True, padx=10, pady=10)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_cobranca_respostas ORDER BY data_criacao DESC")
                for row in cur.fetchall():
                    dt_str = row['data_cobranca'].strftime('%d/%m/%Y') if hasattr(row['data_cobranca'], 'strftime') else str(row['data_cobranca'])
                    tb_hist.insert("", "end", values=(
                        row['id'], row['saj'] or "-", row['sei'] or "-", row['orgao_secretaria'] or "-",
                        row.get('caminho') or "-", row.get('Id SEI') or "-", dt_str, row['meio_cobranca'], f"+{row['dias_prorrogados']} dias",
                        row['observacao'] or "-", row['usuario_criacao'] or "N/A"
                    ))
            except mysql.connector.Error as err:
                logging.error(f"Erro ao buscar histórico: {err}")
            finally: 
                cur.close()
                con.close()

        if hasattr(self.app, 'ajustar_colunas_treeview'):
            self.app.ajustar_colunas_treeview(tb_hist)