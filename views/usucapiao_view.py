import re
import logging
import threading
from datetime import datetime, timedelta
from tkinter import ttk, messagebox
import customtkinter as ctk
import mysql.connector
from database.connection import obter_conexao
from utils.debouncer import Debouncer
from utils.pdf_exporter import exportar_treeview_pdf
from utils.formatters import aplicar_mascara_data_auto, formatar_e_validar_data, formatar_saj, formatar_sei
from utils.oficio_usucapiao import gerar_oficios_usucapiao

class AbaUsucapiao:
    def __init__(self, master, app_instance):
        self.master = master
        self.app = app_instance
        self.id_usucapiao_sel = None

        # Guarda a ordenação (coluna + direção) escolhida pelo usuário, para que
        # ela seja reaplicada automaticamente após qualquer recarga da lista
        # (cadastro, atualização, exclusão, conclusão), em vez de a lista voltar
        # sempre para a ordem padrão por ID.
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        # Texto "puro" (sem seta ▲/▼) de cada cabeçalho, para remontar com/sem
        # o indicador de ordenação.
        self._col_headers = {}

        # ID a re-selecionar na Treeview após a lista recarregar (usado após
        # cadastrar/atualizar, para o usuário não perder a linha de vista).
        self._id_para_selecionar = None

        # Contador de requisições ao banco: só o resultado da requisição MAIS
        # RECENTE preenche a tabela, evitando que uma busca antiga e lenta
        # sobrescreva por engano o resultado de uma busca mais nova.
        self._listar_seq = 0

        self._toast_label = None
        self._toast_after_id = None

        self.montar_aba()

    # ------------------------------------------------------------------
    # ORDENAÇÃO DE COLUNAS (persistente entre recargas da lista)
    # ------------------------------------------------------------------
    def _ordenar_coluna(self, col, reverse):
        """
        Ordena o Treeview pela coluna clicada, tratando datas, números e
        texto, alternando entre ordem crescente e decrescente a cada clique.
        """
        itens = [(self.tab_usu_tree.set(k, col), k) for k in self.tab_usu_tree.get_children('')]

        def chave_ordenacao(item):
            val = str(item[0]).strip()
            if re.match(r'^\d{2}/\d{2}/\d{4}', val):
                try:
                    return datetime.strptime(val[:10], '%d/%m/%Y')
                except ValueError:
                    pass
            if val.isdigit():
                return int(val)
            return val.lower()

        try:
            itens.sort(key=chave_ordenacao, reverse=reverse)
        except Exception:
            itens.sort(key=lambda x: str(x[0]).lower(), reverse=reverse)

        for index, (val, k) in enumerate(itens):
            self.tab_usu_tree.move(k, '', index)

        # Alterna o comando para a próxima ordenação ser inversa
        self.tab_usu_tree.heading(
            col,
            command=lambda _col=col: self._ordenar_coluna(_col, not reverse)
        )

        self._sort_coluna_atual = col
        self._sort_reverse_atual = reverse
        self._atualizar_estado_btn_ordenacao()
        self._atualizar_headers_ordenacao()

    def _atualizar_headers_ordenacao(self):
        """
        Reescreve o texto de todos os cabeçalhos, acrescentando uma seta
        (▲ crescente / ▼ decrescente) apenas na coluna atualmente ordenada.
        """
        for col, texto_base in self._col_headers.items():
            if col == self._sort_coluna_atual:
                seta = " ▼" if self._sort_reverse_atual else " ▲"
                self.tab_usu_tree.heading(col, text=texto_base + seta)
            else:
                self.tab_usu_tree.heading(col, text=texto_base)

    def _reaplicar_ordenacao(self):
        """
        Reaplica a última ordenação de coluna escolhida pelo usuário. Deve
        ser chamada sempre que a Treeview for repopulada a partir do banco,
        pois o SELECT não garante a ordem que o usuário escolheu.
        """
        if self._sort_coluna_atual is not None:
            self._ordenar_coluna(self._sort_coluna_atual, self._sort_reverse_atual)

    def _atualizar_estado_btn_ordenacao(self):
        """
        Reflete no botão de ordenação se há alguma ordenação de coluna ativa
        (destacado) ou não (neutro).
        """
        if not hasattr(self, 'btn_ordenacao_ativa'):
            return
        if self._sort_coluna_atual is not None:
            self.btn_ordenacao_ativa.configure(fg_color="#1f538d", hover_color="#14375e")
        else:
            self.btn_ordenacao_ativa.configure(fg_color="#454545", hover_color="#2b2b2b")

    def atualizar_cores_tema(self, modo=None):
        """Reconfigura as cores das tags de status conforme o tema atual (chamado também pelo main_view ao trocar o tema)."""
        modo = modo or ctk.get_appearance_mode()
        if modo == "Light":
            self.tab_usu_tree.tag_configure("concluido_usu", background="#d4edda", foreground="#155724")
            self.tab_usu_tree.tag_configure("parcial_usu", background="#fff3cd", foreground="#856404")
        else:
            self.tab_usu_tree.tag_configure("concluido_usu", background="#1b4d2b", foreground="#ffffff")
            self.tab_usu_tree.tag_configure("parcial_usu", background="#7a5a12", foreground="#ffffff")

    def usu_limpar_ordenacao(self):
        """Limpa a ordenação ativa, voltando a lista para a ordem por ID."""
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        self._atualizar_estado_btn_ordenacao()
        self._atualizar_headers_ordenacao()
        self.usu_listar()

    def _selecionar_por_id(self, item_id):
        """
        Procura na Treeview a linha cujo ID corresponde a item_id, seleciona
        e rola a visão até ela. Usado para manter o usuário "no lugar" após
        cadastrar ou atualizar um processo.
        """
        if item_id is None:
            return
        item_id = str(item_id)
        for k in self.tab_usu_tree.get_children(''):
            if str(self.tab_usu_tree.set(k, "id")) == item_id:
                self.tab_usu_tree.selection_set(k)
                self.tab_usu_tree.see(k)
                break

    # ------------------------------------------------------------------
    # FEEDBACK LEVE (TOAST) E INDICADOR DE CARREGAMENTO
    # ------------------------------------------------------------------
    def _mostrar_toast(self, texto, cor="#2e7d32"):
        """
        Mostra uma notificação leve e temporária no canto da lista, em vez
        de um messagebox modal que trava a tela a cada cadastro/atualização.
        """
        if self._toast_label is not None and self._toast_label.winfo_exists():
            if self._toast_after_id:
                self.master.after_cancel(self._toast_after_id)
            self._toast_label.destroy()

        self._toast_label = ctk.CTkLabel(
            self.f_lista_usu, text=texto, fg_color=cor, text_color="white",
            corner_radius=6, font=("Arial", 12, "bold")
        )
        self._toast_label.place(relx=0.5, rely=0.03, anchor="n")
        self._toast_after_id = self.master.after(2200, self._esconder_toast)

    def _esconder_toast(self):
        if self._toast_label is not None and self._toast_label.winfo_exists():
            self._toast_label.destroy()
        self._toast_label = None
        self._toast_after_id = None

    def _mostrar_carregando(self, mostrar):
        if not hasattr(self, 'lbl_carregando_usu'):
            return
        if mostrar:
            self.lbl_carregando_usu.pack(side="left", padx=(10, 0))
        else:
            self.lbl_carregando_usu.pack_forget()

    def montar_aba(self):
        self.f_form_usu = ctk.CTkScrollableFrame(self.master, width=300)
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

        self.e_oficio_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Nº Ofício (vazio = automático)", width=260)
        self.e_oficio_usu.pack(pady=6)

        self.e_endereco_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Endereço do Imóvel (objeto do usucapião)", width=260)
        self.e_endereco_usu.pack(pady=6)

        self.e_prazo_usu = ctk.CTkEntry(self.f_form_usu, placeholder_text="Prazo p/ Resposta (dias, ex: 5)", width=260)
        self.e_prazo_usu.pack(pady=6)

        ctk.CTkLabel(self.f_form_usu, text="Tipo de Solicitação:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=(4, 0))
        self.cmb_tipo_usu = ctk.CTkComboBox(self.f_form_usu, values=["Judicial", "Extrajudicial"], width=260)
        self.cmb_tipo_usu.pack(pady=4)

        ctk.CTkLabel(self.f_form_usu, text="Secretarias Respondidas:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=(6, 0))

        # As variáveis (0/1) continuam sendo 3 porque a tabela no banco
        # (tb_usucapiao) só tem 3 colunas fixas: sec1_ok, sec2_ok, sec3_ok.
        # O que muda é a EXIBIÇÃO: em vez de sempre mostrar "1ª/2ª/3ª
        # Secretaria" mesmo quando o usuário digitou só uma, os checkboxes
        # agora aparecem dinamicamente com o NOME real de cada secretaria
        # digitada no campo acima, e ficam ocultos quando não há
        # correspondente — evitando confusão sobre "o que é a 3ª secretaria".
        self.chk_sec1_var = ctk.IntVar(value=0)
        self.chk_sec2_var = ctk.IntVar(value=0)
        self.chk_sec3_var = ctk.IntVar(value=0)
        self._chk_secs_vars = [self.chk_sec1_var, self.chk_sec2_var, self.chk_sec3_var]

        self.f_checks_usu = ctk.CTkFrame(self.f_form_usu, fg_color="transparent")
        self.f_checks_usu.pack(fill="x", padx=15, pady=2)

        self.chk_sec1 = ctk.CTkCheckBox(self.f_checks_usu, text="1ª Secretaria", variable=self.chk_sec1_var)
        self.chk_sec2 = ctk.CTkCheckBox(self.f_checks_usu, text="2ª Secretaria", variable=self.chk_sec2_var)
        self.chk_sec3 = ctk.CTkCheckBox(self.f_checks_usu, text="3ª Secretaria", variable=self.chk_sec3_var)
        self._chk_secs_widgets = [self.chk_sec1, self.chk_sec2, self.chk_sec3]

        # Aviso exibido apenas quando o usuário digita mais de 3 secretarias
        # (a tabela do banco só rastreia resposta para as 3 primeiras).
        self.lbl_aviso_secretarias = ctk.CTkLabel(
            self.f_checks_usu, text="⚠ Apenas as 3 primeiras secretarias têm resposta rastreada.",
            font=("Arial", 10), text_color="#ffb74d", wraplength=250, justify="left"
        )

        self.atualizar_rotulos_secretarias()

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

        # Botão com símbolo de ordenação: fica destacado (azul) quando existe
        # uma ordenação de coluna ativa e, ao ser clicado, limpa essa
        # ordenação, voltando a lista para a ordem padrão por ID.
        self.btn_ordenacao_ativa = ctk.CTkButton(
            f_pesq, text="🔽 Ordenação", width=100,
            fg_color="#454545", hover_color="#2b2b2b",
            command=self.usu_limpar_ordenacao
        )
        self.btn_ordenacao_ativa.pack(side="right", padx=(10, 0))

        # Indicador de carregamento — visível apenas enquanto a consulta ao
        # banco está em andamento, para o usuário não achar que o clique
        # "não funcionou" numa base grande ou conexão lenta.
        self.lbl_carregando_usu = ctk.CTkLabel(f_pesq, text="⏳ Carregando...", text_color="gray")

        cols = ("id", "saj", "sei", "id_sei", "interessado", "secretarias", "envio", "tipo", "status", "enviado_por")
        self.tab_usu_tree = ttk.Treeview(self.f_lista_usu, columns=cols, show="headings", selectmode="browse")

        headers = ("ID", "SAJ", "SEI", "ID SEI", "INTERESSADO", "SECRETARIAS", "ENVIO", "TIPO", "STATUS RESPOSTAS", "ENVIADO POR")
        self._col_headers = dict(zip(cols, headers))
        for c, h in zip(cols, headers):
            self.tab_usu_tree.heading(c, text=h, command=lambda _col=c: self._ordenar_coluna(_col, False))

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

        # Cores de status: verde quando todas as secretarias já responderam,
        # laranja quando há resposta parcial. Ajustadas conforme o tema
        # (dark/light) para não ficarem escuras/estranhas no Modo Claro.
        self.atualizar_cores_tema()

        self.tab_usu_tree.pack(pady=10, padx=10, fill="both", expand=True)
        self.tab_usu_tree.bind("<Double-1>", self.usu_carregar_campos)
        # Também carrega os campos com um único clique (seleção), igual ao
        # comportamento da aba de Diligências — antes só o duplo-clique
        # funcionava, o que confundia quem tentava editar com um clique.
        self.tab_usu_tree.bind("<<TreeviewSelect>>", self.usu_carregar_campos)

        f_act = ctk.CTkFrame(self.f_lista_usu, fg_color="transparent")
        f_act.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_act, text="✔ Concluir Usucapião", command=self.usu_concluir, fg_color="#1f538d", hover_color="#14375e", width=140).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="Ver Concluídas", command=self.usu_abrir_concluidas, fg_color="#454545", hover_color="#2b2b2b", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="📄 Gerar PDF", command=lambda: exportar_treeview_pdf(self.tab_usu_tree, "Relatório de Usucapião"), fg_color="#2e7d32", hover_color="#1b5e20", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="📨 Gerar Ofícios", command=self.usu_gerar_oficios, fg_color="#6a1b9a", hover_color="#4a148c", width=120).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="⏰ Cobrança Usucapião", command=self.usu_abrir_cobranca, fg_color="#b37400", hover_color="#8a5a00", width=150).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="✖ Excluir", command=self.usu_excluir, fg_color="red", hover_color="darkred", width=110).pack(side="right", padx=5)

        self.usu_listar()

    def aplicar_mascara_campo(self, widget, funcao_mascara):
        val = widget.get()
        if val.strip():
            widget.delete(0, 'end'); widget.insert(0, funcao_mascara(val))

    def atualizar_rotulos_secretarias(self, event=None):
        """
        Reconstrói dinamicamente os checkboxes de "Secretaria Respondida" a
        partir do que foi digitado no campo de secretarias — mostrando o
        NOME real de cada uma, e ocultando os checkboxes sem correspondente
        (ex.: se só 1 secretaria foi digitada, só aparece 1 checkbox, em vez
        de mostrar 3 rótulos genéricos "1ª/2ª/3ª Secretaria").
        """
        texto = self.e_sec_usu.get().strip()
        secs = [s.strip() for s in texto.split(',') if s.strip()]

        for i, chk in enumerate(self._chk_secs_widgets):
            if i < len(secs):
                chk.configure(text=f"Respondido: {secs[i]}")
                if not chk.winfo_ismapped():
                    chk.pack(anchor="w", pady=2)
            else:
                if chk.winfo_ismapped():
                    chk.pack_forget()
                self._chk_secs_vars[i].set(0)

        if len(secs) > 3:
            if not self.lbl_aviso_secretarias.winfo_ismapped():
                self.lbl_aviso_secretarias.pack(anchor="w", pady=(4, 0))
        else:
            if self.lbl_aviso_secretarias.winfo_ismapped():
                self.lbl_aviso_secretarias.pack_forget()

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

    def _proximo_numero_oficio(self, cur):
        """Gera 'NNN/ANO' pegando o maior número já usado no ano corrente."""
        ano = datetime.now().year
        cur.execute(
            "SELECT numero_oficio FROM tb_usucapiao WHERE numero_oficio LIKE %s "
            "ORDER BY CAST(SUBSTRING_INDEX(numero_oficio,'/',1) AS UNSIGNED) DESC LIMIT 1",
            (f"%/{ano}",)
        )
        row = cur.fetchone()
        maior = int(row[0].split('/')[0]) if row and row[0] else 0
        return f"{maior + 1}/{ano}"

    def usu_limpar_campos(self):
        self.id_usucapiao_sel = None
        for e in (self.e_saj_usu, self.e_sei_usu, self.e_idsei_usu, self.e_interessado_usu, self.e_sec_usu,
                  self.e_envio_usu, self.e_oficio_usu, self.e_endereco_usu, self.e_prazo_usu):
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
        endereco = self.e_endereco_usu.get().strip()
        dias_prazo = self.e_prazo_usu.get().strip()
        numero_oficio = self.e_oficio_usu.get().strip()

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                if not numero_oficio:
                    numero_oficio = self._proximo_numero_oficio(cur)
                sql = """
                    INSERT INTO tb_usucapiao (saj, sei, `Id SEI`, interessado, secretarias, data_envio, tipo_solicitacao, sec1_ok, sec2_ok, sec3_ok, status, endereco_objeto, numero_oficio, dias_prazo_resposta, usuario_criacao, data_criacao)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cur.execute(sql, (saj, sei, id_sei, interessado, secretarias, envio_fmt, tipo, s1, s2, s3, status_txt,
                                   endereco or None, numero_oficio or None, dias_prazo or None, self.app.usuario_atual))
                con.commit()
                # Guarda o ID recém-criado para que, após a lista recarregar,
                # o novo processo já apareça selecionado e visível.
                self._id_para_selecionar = cur.lastrowid
                self._mostrar_toast("✔ Processo cadastrado!", "#2e7d32")
                self.usu_limpar_campos(); self.usu_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao cadastrar usucapião: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao salvar o processo:\n{err}")
            finally:
                cur.close(); con.close()

    # LISTAR USANDO THREADING
    def usu_listar(self):
        busca = self.e_pesq_usu.get().strip()
        ano = self.cmb_ano_usu.get()

        self._listar_seq += 1
        seq_atual = self._listar_seq
        self._mostrar_carregando(True)
        janela = self.master.winfo_toplevel()  # captura na thread principal

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
                    if janela.winfo_exists():
                        janela.after(0, lambda: self._preencher_tabela(linhas, seq_atual))
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao listar usucapião: {err}")
                    if janela.winfo_exists():
                        janela.after(0, lambda: self._mostrar_carregando(False))
                        janela.after(0, lambda: messagebox.showerror("Erro no Banco", f"Falha ao carregar a lista:\n{err}"))
                except RuntimeError:
                    pass  # janela foi destruída/trocada durante a consulta
                finally:
                    cur.close(); con.close()
            else:
                if janela.winfo_exists():
                    janela.after(0, lambda: self._mostrar_carregando(False))

        threading.Thread(target=tarefa_banco, daemon=True).start()

    def _preencher_tabela(self, linhas, seq=None):
        # Descarta resultados de uma requisição antiga que chegou depois de
        # uma mais nova (proteção contra race condition de rede/consulta).
        if seq is not None and seq != self._listar_seq:
            return

        for item in self.tab_usu_tree.get_children():
            self.tab_usu_tree.delete(item)

        for row in linhas:
            env_str = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else (row['data_envio'] or "")
            st_dinamico = self.calcular_status_usucapiao(row['sec1_ok'], row['sec2_ok'], row['sec3_ok'], row['secretarias'] or "")

            # Destaque visual: verde quando concluído, laranja quando há
            # resposta parcial, para escanear a lista sem ler cada status.
            if st_dinamico.startswith("Concluído"):
                tags = ("concluido_usu",)
            elif st_dinamico.startswith("Resp:"):
                tags = ("parcial_usu",)
            else:
                tags = ()

            self.tab_usu_tree.insert("", "end", values=(
                row['id'], row['saj'], row['sei'], row.get('Id SEI') or "-", row['interessado'],
                row['secretarias'], env_str, row['tipo_solicitacao'], st_dinamico, row['usuario_criacao'] or "N/A"
            ), tags=tags)

        # Reaplica a ordenação de coluna escolhida pelo usuário, já que o
        # SELECT devolve as linhas por ID e, sem isso, qualquer recarga
        # perderia a ordenação e voltaria para a ordem padrão.
        self._reaplicar_ordenacao()

        # Se uma ação (cadastro/atualização) pediu para manter o foco em um
        # ID específico, seleciona e rola a tabela até essa linha.
        if self._id_para_selecionar is not None:
            self._selecionar_por_id(self._id_para_selecionar)
            self._id_para_selecionar = None

        self._mostrar_carregando(False)

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

                        self.e_oficio_usu.delete(0, 'end'); self.e_oficio_usu.insert(0, reg.get('numero_oficio') or "")
                        self.e_endereco_usu.delete(0, 'end'); self.e_endereco_usu.insert(0, reg.get('endereco_objeto') or "")
                        self.e_prazo_usu.delete(0, 'end'); self.e_prazo_usu.insert(0, str(reg.get('dias_prazo_resposta') or ""))

                        self.cmb_tipo_usu.set(reg['tipo_solicitacao'])
                        self.chk_sec1_var.set(reg['sec1_ok'])
                        self.chk_sec2_var.set(reg['sec2_ok'])
                        self.chk_sec3_var.set(reg['sec3_ok'])
                        self.atualizar_rotulos_secretarias()
                        self.btn_salvar_usu.configure(state="disabled")
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao carregar processo: {err}")
                    messagebox.showerror("Erro no Banco", f"Falha ao carregar os dados do processo:\n{err}")
                finally:
                    cur.close(); con.close()

    def usu_atualizar(self):
        if not self.id_usucapiao_sel: messagebox.showwarning("Aviso", "Selecione um processo."); return
        self.aplicar_mascara_campo(self.e_saj_usu, formatar_saj)
        self.aplicar_mascara_campo(self.e_sei_usu, formatar_sei)

        if not self.e_saj_usu.get().strip() or not self.e_interessado_usu.get().strip():
            messagebox.showwarning("Aviso", "SAJ e Interessado são obrigatórios!"); return

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
                    SET saj=%s, sei=%s, `Id SEI`=%s, interessado=%s, secretarias=%s, data_envio=%s, tipo_solicitacao=%s, sec1_ok=%s, sec2_ok=%s, sec3_ok=%s, status=%s,
                        endereco_objeto=%s, numero_oficio=%s, dias_prazo_resposta=%s
                    WHERE id=%s
                """
                cur.execute(sql, (
                    self.e_saj_usu.get(), self.e_sei_usu.get(), self.e_idsei_usu.get().strip(), self.e_interessado_usu.get(),
                    secretarias, envio_fmt, self.cmb_tipo_usu.get(), s1, s2, s3, status_txt,
                    self.e_endereco_usu.get().strip() or None, self.e_oficio_usu.get().strip() or None,
                    self.e_prazo_usu.get().strip() or None, self.id_usucapiao_sel
                ))
                con.commit()
                # Mantém a mesma linha selecionada/visível após a lista recarregar.
                self._id_para_selecionar = self.id_usucapiao_sel
                self._mostrar_toast("✔ Processo atualizado!", "#1f538d")
                self.usu_limpar_campos(); self.usu_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao atualizar usucapião: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao atualizar o processo:\n{err}")
            finally:
                cur.close(); con.close()

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
                    self._mostrar_toast("🗑 Processo excluído", "#a83232")
                    self.usu_limpar_campos(); self.usu_listar()
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao excluir usucapião: {err}")
                    messagebox.showerror("Erro no Banco", f"Falha ao excluir o processo:\n{err}")
                finally:
                    cur.close(); con.close()

    def usu_gerar_oficios(self):
        sel = self.tab_usu_tree.selection()
        if not sel: messagebox.showwarning("Aviso", "Selecione um processo."); return
        item_id = self.tab_usu_tree.item(sel[0], "values")[0]

        con = obter_conexao()
        if not con: return
        try:
            cur = con.cursor(dictionary=True)
            cur.execute("SELECT * FROM tb_usucapiao WHERE id=%s", (item_id,))
            reg = cur.fetchone()
            if not reg: return

            if not reg.get('endereco_objeto'):
                messagebox.showwarning("Aviso", "Informe e salve o endereço do imóvel antes de gerar os ofícios."); return

            numero_oficio = reg.get('numero_oficio')
            if not numero_oficio:
                numero_oficio = self._proximo_numero_oficio(cur)

            dias = int(reg.get('dias_prazo_resposta') or 5)
            hoje = datetime.now().date()
            prazo_data = hoje + timedelta(days=dias)

            caminhos = gerar_oficios_usucapiao(
                numero_oficio_base=numero_oficio,
                processo=reg['saj'] or reg['sei'] or "",
                interessado=reg['interessado'],
                endereco=reg['endereco_objeto'],
                prazo_dias=dias,
            )

            cur.execute(
                "UPDATE tb_usucapiao SET numero_oficio=%s, prazo_resposta_data=%s WHERE id=%s",
                (numero_oficio, prazo_data, item_id)
            )
            con.commit()
            self._mostrar_toast("📨 Ofícios gerados!", "#6a1b9a")
            messagebox.showinfo("Ofícios Gerados", "Arquivos:\n" + "\n".join(caminhos))
            self.usu_listar()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao gerar ofícios: {err}")
            messagebox.showerror("Erro no Banco", f"Falha ao gerar ofícios:\n{err}")
        finally:
            cur.close(); con.close()

    def usu_abrir_cobranca(self):
        """Lista processos com prazo de resposta vencido e ainda não concluídos,
        permitindo registrar o número do SEI de cobrança."""
        janela = ctk.CTkToplevel(self.master)
        janela.title("Cobrança Usucapião")
        janela.after(0, lambda: janela.wm_state('zoomed'))
        janela.transient(self.master)

        ctk.CTkLabel(janela, text="Processos com Prazo de Resposta Vencido", font=("Arial", 16, "bold")).pack(pady=10)
        cols = ("id", "saj", "interessado", "endereco", "prazo", "status", "sei_cobranca")
        tb = ttk.Treeview(janela, columns=cols, show="headings")
        for c, t in zip(cols, ("ID", "SAJ", "Interessado", "Endereço", "Prazo", "Status", "SEI Cobrança")):
            tb.heading(c, text=t)
        tb.pack(fill="both", expand=True, padx=10, pady=5)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute(
                    "SELECT * FROM tb_usucapiao WHERE prazo_resposta_data IS NOT NULL AND prazo_resposta_data < CURDATE() "
                    "AND (sec1_ok + sec2_ok + sec3_ok) < 3"
                )
                for row in cur.fetchall():
                    prazo_str = row['prazo_resposta_data'].strftime('%d/%m/%Y') if row['prazo_resposta_data'] else ""
                    st = self.calcular_status_usucapiao(row['sec1_ok'], row['sec2_ok'], row['sec3_ok'], row['secretarias'] or "")
                    tb.insert("", "end", values=(row['id'], row['saj'], row['interessado'], row.get('endereco_objeto') or "",
                                                  prazo_str, st, row.get('sei_cobranca') or ""))
            except mysql.connector.Error as err:
                logging.error(f"Erro ao listar cobrança usucapião: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao carregar cobrança:\n{err}", parent=janela)
            finally:
                cur.close(); con.close()

        f_reg = ctk.CTkFrame(janela, fg_color="transparent")
        f_reg.pack(fill="x", padx=10, pady=10)
        e_sei = ctk.CTkEntry(f_reg, placeholder_text="Nº do processo SEI de cobrança", width=260)
        e_sei.pack(side="left", padx=5)

        def registrar_cobranca():
            sel = tb.selection()
            if not sel: return
            item_id = tb.item(sel[0], "values")[0]
            sei_val = e_sei.get().strip()
            if not sei_val: return
            c = obter_conexao()
            if c:
                try:
                    cr = c.cursor()
                    cr.execute("UPDATE tb_usucapiao SET sei_cobranca=%s, data_cobranca=NOW() WHERE id=%s", (sei_val, item_id))
                    c.commit()
                    tb.set(sel[0], "sei_cobranca", sei_val)
                    e_sei.delete(0, 'end')
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao registrar cobrança: {err}")
                finally:
                    cr.close(); c.close()

        ctk.CTkButton(f_reg, text="Registrar Cobrança SEI", command=registrar_cobranca, fg_color="#b37400").pack(side="left", padx=5)

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
                    self._mostrar_toast("✔ Processo concluído!", "#2e7d32")
                    self.usu_limpar_campos(); self.usu_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao concluir usucapião: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao concluir o processo:\n{err}")
            finally:
                cur.close(); con.close()

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
            except mysql.connector.Error as err:
                logging.error(f"Erro ao carregar histórico de usucapião: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao carregar o histórico:\n{err}", parent=janela)
            finally:
                cur.close(); con.close()

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
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao restaurar usucapião: {err}")
                        messagebox.showerror("Erro no Banco", f"Falha ao restaurar o processo:\n{err}", parent=janela)
                    finally:
                        cr.close(); c.close()

        if self.app.perfil_atual == 'admin':
            ctk.CTkButton(janela, text="↩ Restaurar Selecionado (Admin)", command=restaurar, fg_color="#b37400", hover_color="orange").pack(pady=10)
        else:
            ctk.CTkLabel(janela, text="* Apenas usuários administradores podem restaurar processos concluídos.", text_color="gray").pack(pady=10)