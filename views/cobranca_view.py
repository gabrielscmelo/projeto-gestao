import re
import logging
import threading
from datetime import datetime
from tkinter import ttk, messagebox, Menu, BooleanVar
import mysql.connector
import customtkinter as ctk

from database.connection import obter_conexao
from utils.debouncer import Debouncer
from utils.perfis_colunas import adicionar_perfis
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

        # Guarda a ordenação (coluna + direção) escolhida pelo usuário, para
        # que ela seja reaplicada automaticamente após qualquer recarga da
        # lista (registro de cobrança, execução do robô, nova busca).
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        self._col_headers = {}

        # Contador de requisições ao banco: só o resultado da requisição MAIS
        # RECENTE preenche a tabela, evitando que uma busca antiga e lenta
        # sobrescreva por engano o resultado de uma busca mais nova.
        self._listar_seq = 0

        self._toast_label = None
        self._toast_after_id = None

        # Sinalizador cooperativo de cancelamento do robô — ver observação
        # em cob_cancelar_robo() sobre os limites dessa funcionalidade.
        self._robo_cancelar_solicitado = False

        # IDs das diligências marcadas (checkbox) para cobrança pelo robô.
        self._ids_marcados = set()

        self.montar_aba()

    # ------------------------------------------------------------------
    # ORDENAÇÃO DE COLUNAS (persistente entre recargas da lista)
    # ------------------------------------------------------------------
    def _ordenar_coluna(self, col, reverse, tree=None):
        tree = tree or self.tab_cob_tree
        itens = [(tree.set(k, col), k) for k in tree.get_children('')]

        def chave_ordenacao(item):
            val = str(item[0]).strip()
            if re.match(r'^\d{2}/\d{2}/\d{4}', val):
                try:
                    return datetime.strptime(val[:10], '%d/%m/%Y')
                except ValueError:
                    pass
            # Coluna "ATRASO" tem o formato "12d atrás" — extrai o número
            # para ordenar numericamente em vez de alfabeticamente.
            m = re.match(r'^(\d+)d atrás$', val)
            if m:
                return int(m.group(1))
            if val.isdigit():
                return int(val)
            return val.lower()

        try:
            itens.sort(key=chave_ordenacao, reverse=reverse)
        except Exception:
            itens.sort(key=lambda x: str(x[0]).lower(), reverse=reverse)

        for index, (val, k) in enumerate(itens):
            tree.move(k, '', index)

        tree.heading(
            col,
            command=lambda _col=col: self._ordenar_coluna(_col, not reverse, tree)
        )

        if tree is not self.tab_cob_tree:
            return

        self._sort_coluna_atual = col
        self._sort_reverse_atual = reverse
        self._atualizar_estado_btn_ordenacao()
        self._atualizar_headers_ordenacao()

    def _atualizar_headers_ordenacao(self):
        for col, texto_base in self._col_headers.items():
            if col == self._sort_coluna_atual:
                seta = " ▼" if self._sort_reverse_atual else " ▲"
                self.tab_cob_tree.heading(col, text=texto_base + seta)
            else:
                self.tab_cob_tree.heading(col, text=texto_base)

    def _reaplicar_ordenacao(self):
        if self._sort_coluna_atual is not None:
            self._ordenar_coluna(self._sort_coluna_atual, self._sort_reverse_atual)

    def _atualizar_estado_btn_ordenacao(self):
        if not hasattr(self, 'btn_ordenacao_ativa'):
            return
        if self._sort_coluna_atual is not None:
            self.btn_ordenacao_ativa.configure(fg_color="#1f538d", hover_color="#14375e")
        else:
            self.btn_ordenacao_ativa.configure(fg_color="#454545", hover_color="#2b2b2b")

    def cob_limpar_ordenacao(self):
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        self._atualizar_estado_btn_ordenacao()
        self._atualizar_headers_ordenacao()
        self.cob_listar()

    # ------------------------------------------------------------------
    # FEEDBACK LEVE (TOAST) E INDICADOR DE CARREGAMENTO
    # ------------------------------------------------------------------
    def _mostrar_toast(self, texto, cor="#2e7d32"):
        if self._toast_label is not None and self._toast_label.winfo_exists():
            if self._toast_after_id:
                self.master.after_cancel(self._toast_after_id)
            self._toast_label.destroy()

        self._toast_label = ctk.CTkLabel(
            self.f_lista_cob, text=texto, fg_color=cor, text_color="white",
            corner_radius=6, font=("Arial", 12, "bold")
        )
        self._toast_label.place(relx=0.5, rely=0.03, anchor="n")
        self._toast_after_id = self.master.after(2800, self._esconder_toast)

    def _esconder_toast(self):
        if self._toast_label is not None and self._toast_label.winfo_exists():
            self._toast_label.destroy()
        self._toast_label = None
        self._toast_after_id = None

    def _mostrar_carregando(self, mostrar):
        if not hasattr(self, 'lbl_carregando_cob'):
            return
        if mostrar:
            self.lbl_carregando_cob.pack(side="left", padx=(10, 0))
        else:
            self.lbl_carregando_cob.pack_forget()

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

        # Botão para sinalizar cancelamento ao robô. Fica oculto enquanto não
        # há execução em andamento. Ver observação em cob_cancelar_robo()
        # sobre os limites reais dessa funcionalidade.
        self.btn_parar_robo = ctk.CTkButton(
            self.f_form_cob,
            text="🛑 Parar Robô",
            command=self.cob_cancelar_robo,
            fg_color="#a83232",
            hover_color="#7a2323",
            width=260
        )

        # Linha Divisória
        self.f_divisor_robo = ctk.CTkFrame(self.f_form_cob, height=2, fg_color="#333333")
        self.f_divisor_robo.pack(fill="x", padx=10, pady=6)

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
        self.cmb_prazo_cob.set("+15 dias")

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

        # Botão com símbolo de ordenação: fica destacado (azul) quando existe
        # uma ordenação de coluna ativa e, ao ser clicado, limpa essa
        # ordenação, voltando a lista para a ordem padrão por ID.
        self.btn_ordenacao_ativa = ctk.CTkButton(
            f_pesq, text="🔽 Ordenação", width=100,
            fg_color="#454545", hover_color="#2b2b2b",
            command=self.cob_limpar_ordenacao
        )
        self.btn_ordenacao_ativa.pack(side="right", padx=(10, 0))

        # Indicador de carregamento — visível apenas enquanto a consulta ao
        # banco está em andamento.
        self.lbl_carregando_cob = ctk.CTkLabel(f_pesq, text="⏳ Carregando...", text_color="gray")

        cols = ("sel", "id", "saj", "sei", "diligencia", "secretaria", "caminho", "id_sei", "envio", "prazo", "dias_atraso", "procurador")
        self.tab_cob_tree = ttk.Treeview(self.f_lista_cob, columns=cols, show="headings", selectmode="browse")
        cols_exib = [c for c in cols if c not in ("sel", "procurador")]
        cols_exib.insert(cols_exib.index("diligencia") + 1, "procurador")
        self.tab_cob_tree.configure(displaycolumns=["sel"] + cols_exib)

        headers = ("✔", "ID", "SAJ", "SEI", "DILIGÊNCIA", "SECRETARIA", "CAMINHO", "ID SEI", "ENVIO", "PRAZO VENCIDO", "ATRASO", "PROCURADOR")
        self._col_headers = dict(zip(cols, headers))
        for c, h in zip(cols, headers):
            self.tab_cob_tree.heading(c, text=h, command=lambda _col=c: self._ordenar_coluna(_col, False))

        col_vars_cob = {c: BooleanVar(value=True) for c in cols if c != "sel"}
        menu_cols_cob = Menu(self.master, tearoff=0)
        for c, h in zip(cols, headers):
            if c == "sel":
                continue
            menu_cols_cob.add_checkbutton(
                label=h, variable=col_vars_cob[c],
                command=lambda: self.tab_cob_tree.configure(displaycolumns=["sel"] + [c for c in cols_exib if col_vars_cob[c].get()])
            )
        adicionar_perfis(menu_cols_cob, self.master, "cobranca", col_vars_cob, lambda: self.tab_cob_tree.configure(displaycolumns=["sel"] + [c for c in cols_exib if col_vars_cob[c].get()]), self.tab_cob_tree)
        ctk.CTkButton(
            f_pesq, text="☰", width=36,
            fg_color="#454545", hover_color="#2b2b2b",
            command=lambda: menu_cols_cob.tk_popup(self.master.winfo_pointerx(), self.master.winfo_pointery())
        ).pack(side="right", padx=(10, 0))

        self.tab_cob_tree.column("sel", width=30, anchor="center")
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
        self.tab_cob_tree.column("procurador", width=120)

        self.tab_cob_tree.tag_configure("atrasado", foreground="#ff4d4d")

        self.tab_cob_tree.pack(pady=10, padx=10, fill="both", expand=True)
        self.tab_cob_tree.bind("<<TreeviewSelect>>", self.cob_carregar_campos)
        self.tab_cob_tree.bind("<Double-1>", self.cob_carregar_campos)
        self.tab_cob_tree.bind("<Button-1>", self._toggle_check_cob, add="+")

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

        self.master.after(100, self.cob_listar)
    # ----------------------------------------------------------------------
    def cob_executar_robo_automacao(self):
        usuario_sei = self.e_user_sei.get().strip()
        senha_sei = self.e_pass_sei.get().strip()

        if not usuario_sei or not senha_sei:
            messagebox.showwarning("Credenciais Ausentes", "Informe o Usuário e a Senha do SEI para iniciar a automação.")
            return

        if not self._ids_marcados:
            messagebox.showwarning("Nenhuma Diligência Marcada", "Marque o checkbox das diligências que o robô deve cobrar.")
            return
        ids_dil = list(self._ids_marcados)

        texto_prorrogacao = self.cmb_prazo_cob.get()
        dias_prorrogacao = int(re.sub(r"\D", "", texto_prorrogacao)) if texto_prorrogacao else 5

        self._robo_cancelar_solicitado = False
        self._abrir_janela_log_robo()
        self.btn_executar_robo.configure(state="disabled", text="⏳ Executando Automação...")
        self.btn_parar_robo.configure(state="normal", text="🛑 Parar Robô")
        # 'before' garante que o botão sempre apareça logo abaixo do botão
        # "Executar Robô", e não no final do painel rolável (que é onde o
        # Tkinter o colocaria por padrão, já que ele não estava empacotado
        # desde a criação).
        self.btn_parar_robo.pack(pady=(0, 8), before=self.f_divisor_robo)

        def _rodar_thread():
            try:
                usuario_app = getattr(self.app, 'usuario_atual', 'Robô')
                # Tenta repassar um "sinalizador de parada" cooperativo para
                # o robô. Se services/robo_cobranca.py não suportar esse
                # parâmetro (versão atual não suporta), a chamada cai no
                # except e roda normalmente sem suporte a cancelamento.
                try:
                    iniciar_automacao_completa(
                        usuario_sei=usuario_sei,
                        senha_sei=senha_sei,
                        usuario_app=usuario_app,
                        dias_prorrogacao=dias_prorrogacao,
                        callback_log=self.adicionar_log_console,
                        deve_parar=lambda: self._robo_cancelar_solicitado,
                        diligencias_ids=ids_dil
                    )
                except TypeError:
                    iniciar_automacao_completa(
                        usuario_sei=usuario_sei,
                        senha_sei=senha_sei,
                        usuario_app=usuario_app,
                        dias_prorrogacao=dias_prorrogacao,
                        callback_log=self.adicionar_log_console,
                        diligencias_ids=ids_dil
                    )
            except Exception as err:
                logging.error(f"Erro na execução do robô: {err}")
                self.adicionar_log_console(f"❌ [Erro Crítico] Falha geral na execução do robô: {err}")
            finally:
                janela = self.master.winfo_toplevel()
                try:
                    if janela.winfo_exists():
                        janela.after(0, self._finalizar_execucao_robo)
                except RuntimeError:
                    pass

        threading.Thread(target=_rodar_thread, daemon=True).start()

    def cob_cancelar_robo(self):
        """
        Sinaliza pedido de cancelamento para a automação em andamento.

        IMPORTANTE: isso é cooperativo — só interrompe de fato se
        services/robo_cobranca.py checar periodicamente esse sinalizador
        (via o parâmetro opcional 'deve_parar') entre um passo e outro da
        automação. Sem essa checagem no lado do robô, este botão apenas evita
        que uma NOVA execução comece e avisa no log que o cancelamento foi
        pedido — a rotina em andamento só para no próximo ponto em que ela
        mesma decidir checar o sinalizador.
        """
        if not messagebox.askyesno("Cancelar Automação", "Deseja sinalizar o cancelamento da automação em andamento?"):
            return
        self._robo_cancelar_solicitado = True
        self.btn_parar_robo.configure(state="disabled", text="🛑 Cancelamento solicitado...")
        self.adicionar_log_console("🛑 Cancelamento solicitado pelo usuário — a automação para no próximo ponto de checagem.")

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

            janela = self.master.winfo_toplevel()
            try:
                if janela.winfo_exists():
                    janela.after(0, _update)
            except RuntimeError:
                pass

    def _finalizar_execucao_robo(self):
        self.btn_executar_robo.configure(state="normal", text="🚀 Executar Robô em Lote")
        self.btn_parar_robo.pack_forget()
        self._robo_cancelar_solicitado = False
        self._ids_marcados.clear()
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
        busca = self.e_pesq_cob.get().strip()

        self._listar_seq += 1
        seq_atual = self._listar_seq
        self._mostrar_carregando(True)
        janela = self.master.winfo_toplevel()  # captura na thread principal

        def tarefa_banco():
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    query = "SELECT id, saj, sei, diligencia, categoria, secretaria, caminho, `Id SEI` AS id_sei, data_envio, prazo, procurador FROM tb_gestao WHERE prazo < CURDATE()"
                    params = []

                    if busca:
                        query += " AND (saj LIKE %s OR sei LIKE %s OR diligencia LIKE %s OR secretaria LIKE %s OR categoria LIKE %s OR caminho LIKE %s)"
                        lk = f"%{busca}%"
                        params.extend([lk, lk, lk, lk, lk, lk])

                    cur.execute(query, tuple(params))
                    atrasadas = cur.fetchall()

                    if janela.winfo_exists():
                        janela.after(0, lambda: self._preencher_tabela(atrasadas, seq_atual))
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao listar cobranças: {err}")
                    if janela.winfo_exists():
                        janela.after(0, lambda: self._mostrar_carregando(False))
                        janela.after(0, lambda: messagebox.showerror("Erro no Banco", f"Falha ao carregar a lista de cobranças:\n{err}"))
                except RuntimeError:
                    pass  # janela foi destruída/trocada (ex.: logout) durante a consulta
                finally: 
                    cur.close()
                    con.close()
            else:
                if janela.winfo_exists():
                    janela.after(0, lambda: self._mostrar_carregando(False))

        threading.Thread(target=tarefa_banco, daemon=True).start()

    def _preencher_tabela(self, atrasadas, seq=None):
        # Descarta resultados de uma requisição antiga que chegou depois de
        # uma mais nova (proteção contra race condition de rede/consulta).
        if seq is not None and seq != self._listar_seq:
            return

        for item in self.tab_cob_tree.get_children():
            self.tab_cob_tree.delete(item)

        for row in atrasadas:
            pz_str = row['prazo'].strftime('%Y-%m-%d') if hasattr(row['prazo'], 'strftime') else str(row['prazo'])
            env_str = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else (row['data_envio'] or "")
            ex_pz = row['prazo'].strftime('%d/%m/%Y') if hasattr(row['prazo'], 'strftime') else pz_str
            _, dias = calcular_status_diligencia(pz_str)

            sec_valor = row.get('secretaria') or "-"
            cam_valor = row.get('caminho') or "-"
            id_sei_valor = row.get('id_sei') or "-"

            self.tab_cob_tree.insert("", "end", values=(
                "☑" if row['id'] in self._ids_marcados else "☐",
                row['id'], 
                row['saj'] or "-", 
                row['sei'] or "-", 
                row['diligencia'] or "-",
                sec_valor, 
                cam_valor,
                id_sei_valor,
                env_str, 
                ex_pz, 
                f"{abs(dias)}d atrás",
                row.get('procurador') or "-"
            ), tags=("atrasado",))
        self.lbl_total_cob.configure(text=f"Diligências Espelhadas (Em Atraso): {len(atrasadas)}")

        # Reaplica a ordenação de coluna escolhida pelo usuário, já que o
        # SELECT devolve as linhas por ID e, sem isso, qualquer recarga
        # (registro de cobrança, execução do robô, nova busca) perderia a
        # ordenação e voltaria para a ordem padrão.
        self._reaplicar_ordenacao()
        self._mostrar_carregando(False)

    def _toggle_check_cob(self, event):
        if self.tab_cob_tree.identify_region(event.x, event.y) != "cell" or self.tab_cob_tree.identify_column(event.x) != "#1":
            return
        row = self.tab_cob_tree.identify_row(event.y)
        if not row:
            return
        vals = list(self.tab_cob_tree.item(row, "values"))
        id_dil = int(vals[1])
        if id_dil in self._ids_marcados:
            self._ids_marcados.discard(id_dil)
            vals[0] = "☐"
        else:
            self._ids_marcados.add(id_dil)
            vals[0] = "☑"
        self.tab_cob_tree.item(row, values=vals)

    def cob_carregar_campos(self, event=None):
        sel = self.tab_cob_tree.selection()
        if sel:
            v = self.tab_cob_tree.item(sel[0], "values")
            self.id_diligencia_cob_sel = v[1]
            self.e_saj_cob.delete(0, 'end'); self.e_saj_cob.insert(0, v[2] if v[2] != "-" else "")
            self.e_sei_cob.delete(0, 'end'); self.e_sei_cob.insert(0, v[3] if v[3] != "-" else "")
            self.e_orgao_cob.delete(0, 'end'); self.e_orgao_cob.insert(0, v[5] if v[5] != "-" else "")
            self.e_caminho_cob.delete(0, 'end'); self.e_caminho_cob.insert(0, v[6] if v[6] != "-" else "")

    def cob_registrar_cobranca(self):
        if not self.id_diligencia_cob_sel:
            sel = self.tab_cob_tree.selection()
            if sel:
                v = self.tab_cob_tree.item(sel[0], "values")
                self.id_diligencia_cob_sel = v[1]

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

                self._mostrar_toast(f"✔ Cobrança registrada! Prazo +{dias_adicionais} dias.", "#2e7d32")

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

        # Campo de busca local — como a lista pode crescer bastante com o
        # tempo, rolar sem filtro fica inviável. Filtra por SAJ, SEI,
        # Órgão/Secretaria ou Observação sobre os dados já carregados.
        f_busca_hist = ctk.CTkFrame(janela, fg_color="transparent")
        f_busca_hist.pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(f_busca_hist, text="Buscar:", font=("Arial", 12, "bold")).pack(side="left", padx=(0, 5))
        e_busca_hist = ctk.CTkEntry(f_busca_hist, placeholder_text="SAJ, SEI, Órgão/Secretaria ou Observação...", width=320)
        e_busca_hist.pack(side="left", fill="x", expand=True, padx=5)

        cols = ("id", "saj", "sei", "orgao", "caminho", "id_sei", "data_cob", "meio", "prorrogacao", "obs", "por")
        tb_hist = ttk.Treeview(janela, columns=cols, show="headings")

        headers = ("ID", "SAJ", "SEI", "Órgão/Secretaria", "Caminho", "ID SEI", "Data Cobrança", "Meio", "Prorrogação", "Observação", "Registrado Por")
        for c, h in zip(cols, headers):
            tb_hist.heading(c, text=h, command=lambda _col=c: self._ordenar_coluna(_col, False, tb_hist))

        col_vars_hist = {c: BooleanVar(value=True) for c in cols}
        menu_cols_hist = Menu(janela, tearoff=0)
        for c, h in zip(cols, headers):
            menu_cols_hist.add_checkbutton(
                label=h, variable=col_vars_hist[c],
                command=lambda: tb_hist.configure(displaycolumns=[c for c in cols if col_vars_hist[c].get()] or cols)
            )
        ctk.CTkButton(
            f_busca_hist, text="☰ Colunas", width=90,
            command=lambda: menu_cols_hist.tk_popup(janela.winfo_pointerx(), janela.winfo_pointery())
        ).pack(side="left", padx=5)

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

        # Guarda todas as linhas carregadas do banco (lista de tuplas de
        # valores) para permitir filtrar localmente sem nova consulta a
        # cada tecla digitada.
        todas_as_linhas = []

        def _repopular(filtro=""):
            tb_hist.delete(*tb_hist.get_children())
            filtro = filtro.strip().lower()
            for valores in todas_as_linhas:
                if not filtro or any(filtro in str(v).lower() for v in valores):
                    tb_hist.insert("", "end", values=valores)

        def _on_busca_hist(event=None):
            _repopular(e_busca_hist.get())

        e_busca_hist.bind("<KeyRelease>", _on_busca_hist)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_cobranca_respostas ORDER BY data_criacao DESC")
                for row in cur.fetchall():
                    dt_str = row['data_cobranca'].strftime('%d/%m/%Y') if hasattr(row['data_cobranca'], 'strftime') else str(row['data_cobranca'])
                    valores = (
                        row['id'], row['saj'] or "-", row['sei'] or "-", row['orgao_secretaria'] or "-",
                        row.get('caminho') or "-", row.get('Id SEI') or "-", dt_str, row['meio_cobranca'], f"+{row['dias_prorrogados']} dias",
                        row['observacao'] or "-", row['usuario_criacao'] or "N/A"
                    )
                    todas_as_linhas.append(valores)
                _repopular()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao buscar histórico: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao carregar o histórico:\n{err}", parent=janela)
            finally: 
                cur.close()
                con.close()

        if hasattr(self.app, 'ajustar_colunas_treeview'):
            self.app.ajustar_colunas_treeview(tb_hist)