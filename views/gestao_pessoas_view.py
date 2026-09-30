import calendar
import logging
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, filedialog
import customtkinter as ctk
import mysql.connector
from database.connection import obter_conexao

# Motivos de afastamento pré-definidos. Sinta-se livre para editar esta
# lista — é só um combobox, não afeta nada mais no código.
MOTIVOS_AFASTAMENTO = [
    "Férias", "Carência", "Licença Médica", "Licença Prêmio", "Licença Maternidade",
    "Licença Paternidade", "Licença Casamento", "Licença Luto",
    "Capacitação/Curso", "Outro",
]

# Carência: pré-férias do procurador (para de receber processos). Máx. de dias por período concessivo.
LIMITE_CARENCIA_DIAS = 10
TAG_VENDA = "[Vendeu 10 dias]"  # marcador gravado em observacao
DIAS_FERIAS_PERIODO = 30  # direito de férias por período aquisitivo (dias corridos)

_DDL_PERIODOS = """CREATE TABLE IF NOT EXISTS tb_periodos_ferias (
    id INT AUTO_INCREMENT PRIMARY KEY, colaborador_id INT NOT NULL,
    aquisitivo_inicio DATE NOT NULL, aquisitivo_fim DATE NOT NULL,
    concessivo_inicio DATE NOT NULL, concessivo_fim DATE NOT NULL,
    usuario_criacao VARCHAR(100), data_criacao DATETIME, INDEX (colaborador_id))"""


def _fim_periodo(d):
    """Último dia de um período de 12 meses iniciado em d."""
    try:
        return d.replace(year=d.year + 1) - timedelta(days=1)
    except ValueError:  # 29/02
        return d.replace(year=d.year + 1, day=28)

# Paleta de cores selecionáveis para a tarja no calendário.
PALETA_CORES = [
    "#3a7ebf", "#2e7d32", "#e0a030", "#a83232",
    "#7b4fa0", "#1f7a72", "#c2477a", "#6c6c6c",
]

MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
DIAS_SEMANA_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _blend(hex_cor, alpha, hex_fundo=None):
    """
    Mistura hex_cor com a cor de fundo do tema ATUAL (dark/light) para
    simular translucidez — Canvas/Tkinter não suporta alpha real em
    preenchimentos. alpha 0..1 (quanto menor, mais "apagada" fica a cor).
    Antes o fundo de referência era fixo (só dark), por isso a tarja ficava
    escura/estranha no Modo Claro.
    """
    if hex_fundo is None:
        hex_fundo = "#e5e5e5" if ctk.get_appearance_mode() == "Light" else "#2b2b2b"
    c = hex_cor.lstrip('#')
    f = hex_fundo.lstrip('#')
    r1, g1, b1 = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    r2, g2, b2 = int(f[0:2], 16), int(f[2:4], 16), int(f[4:6], 16)
    r = round(r1 * alpha + r2 * (1 - alpha))
    g = round(g1 * alpha + g2 * (1 - alpha))
    b = round(b1 * alpha + b2 * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


def _texto_legivel(hex_cor):
    """Escolhe preto ou branco para o texto conforme a luminância do fundo, garantindo contraste em qualquer tema/cor."""
    c = hex_cor.lstrip('#')
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


def _as_date(v):
    """Normaliza um valor de data vindo do banco (date, datetime ou string) para datetime.date."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.strptime(v, '%Y-%m-%d').date()
        except ValueError:
            return None
    if hasattr(v, 'date') and not isinstance(v, date):
        return v.date()
    return v


def _proximo_dia_util(d):
    """Se d cair em sábado/domingo, avança para a próxima segunda-feira."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _dias_uteis(di, df):
    """Dias úteis (seg-sex) entre di e df, inclusive. Não considera feriados."""
    return sum(1 for i in range((df - di).days + 1) if (di + timedelta(days=i)).weekday() < 5)


class AbaGestaoPessoas:
    def __init__(self, master, app_instance):
        self.master = master
        self.app = app_instance

        self.data_ref = date.today()
        self.modo = "mes"          # dia | semana | mes | ano
        self.filtro_tipo = "todos"  # todos | procurador | apoio
        self.colaborador_sel = None
        self.afastamento_sel = None  # afastamento em edição (None = criando um novo)
        self.cor_escolhida = PALETA_CORES[0]
        self.colaboradores = []
        self.afastamentos = []
        self.periodos = []
        self._afast_por_dia, self._aniv_por_diames = {}, {}
        self._colab_por_id, self._colab_por_nome = {}, {}
        self._busca_colab = ""
        self._toast_label = self._toast_after_id = None

        self.montar_aba()
        self.master.after(100, self.recarregar_dados)

    def _cores_unicas_dia(self, afast_dia):
        """Cores (já misturadas com o fundo do tema) de cada afastamento do
        dia, sem repetir cores idênticas — na ordem em que aparecem."""
        cores = []
        for a in afast_dia:
            cor_bg = _blend(a['cor'], 0.35)
            if cor_bg not in cores:
                cores.append(cor_bg)
        return cores

    def _criar_celula_vazia(self, parent, r, c):
        """Célula sem data (dia fora do mês) — mantém a MESMA caixa/borda das
        células com data, só sem número, para o grid ficar visualmente
        uniforme em vez de ter 'buracos' sem moldura."""
        cel = ctk.CTkFrame(parent, fg_color="transparent", border_width=1, border_color="#333333", corner_radius=4)
        cel.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

    def _mascarar_data(self, entry):
        """Formata automaticamente dd/mm/aaaa (ou dd/mm) enquanto o usuário
        digita, aceitando apenas dígitos e inserindo as barras sozinho."""
        texto = entry.get()
        digitos = ''.join(ch for ch in texto if ch.isdigit())[:8]
        if len(digitos) > 4:
            novo = f"{digitos[:2]}/{digitos[2:4]}/{digitos[4:]}"
        elif len(digitos) > 2:
            novo = f"{digitos[:2]}/{digitos[2:]}"
        else:
            novo = digitos
        if novo != texto:
            entry.delete(0, 'end')
            entry.insert(0, novo)

    def _aplicar_mascara_data(self, entry):
        entry.bind("<KeyRelease>", lambda e, en=entry: self._mascarar_data(en))

    # ------------------------------------------------------------------
    # MONTAGEM DA INTERFACE
    # ------------------------------------------------------------------
    def montar_aba(self):
        self.f_form = ctk.CTkScrollableFrame(self.master, width=300)
        self.f_form.pack(side="left", fill="y", padx=10, pady=10)
        self.f_cal = ctk.CTkFrame(self.master)
        self.f_cal.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        # --- Colaborador / Afastamento: agora em pop-ups ---
        ctk.CTkLabel(self.f_form, text="Colaborador", font=("Arial", 14, "bold")).pack(pady=(5, 8))
        ctk.CTkButton(self.f_form, text="➕ Cadastrar Colaborador", width=250,
                      command=lambda: (self.limpar_form_colaborador(), self.abrir_popup_colaborador_form())).pack(pady=6)
        ctk.CTkButton(self.f_form, text="👥 Ver Colaboradores", width=250,
                      command=self.abrir_popup_ver_colaboradores).pack(pady=6)
        ctk.CTkFrame(self.f_form, height=2, fg_color="#333333").pack(fill="x", pady=8)
        ctk.CTkButton(self.f_form, text="🗓️ Registrar/Alterar/Excluir Afastamento", width=250,
                      command=self.abrir_popup_afastamento).pack(pady=6)
        ctk.CTkButton(self.f_form, text="📄 Gerar Relatório", width=250,
                      command=self.abrir_popup_relatorio).pack(pady=6)

        # --- Alertas de férias (entrada/retorno) ---
        self._alertas = []  # exibidos na barra de notificações da tela principal

        # --- Barra de navegação do calendário ---
        f_nav = ctk.CTkFrame(self.f_cal, fg_color="transparent")
        f_nav.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(f_nav, text="◀", width=36, command=lambda: self.navegar(-1)).pack(side="left", padx=3)
        ctk.CTkButton(f_nav, text="Hoje", width=55, command=self.ir_para_hoje).pack(side="left", padx=3)
        ctk.CTkButton(f_nav, text="▶", width=36, command=lambda: self.navegar(1)).pack(side="left", padx=3)
        ctk.CTkButton(f_nav, text="🔄", width=36, command=self.recarregar_dados).pack(side="left", padx=(10, 3))
        self.lbl_periodo = ctk.CTkLabel(f_nav, text="", font=("Arial", 14, "bold"))
        self.lbl_periodo.pack(side="left", padx=15)
        self.lbl_carregando_gp = ctk.CTkLabel(f_nav, text="", text_color="gray", font=("Arial", 11))
        self.lbl_carregando_gp.pack(side="left", padx=5)

        self.seg_filtro = ctk.CTkSegmentedButton(f_nav, values=["Todos", "Procurador", "Apoio"], command=self.mudar_filtro)
        self.seg_filtro.set("Todos")
        self.seg_filtro.pack(side="right", padx=5)
        self.seg_modo = ctk.CTkSegmentedButton(f_nav, values=["Dia", "Semana", "Mês", "Ano"], command=self.mudar_modo)
        self.seg_modo.set("Mês")
        self.seg_modo.pack(side="right", padx=5)

        self.f_area_cal = ctk.CTkFrame(self.f_cal, fg_color="transparent")
        self.f_area_cal.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # DADOS (BANCO)
    # ------------------------------------------------------------------
    def recarregar_dados(self):
        if self._w_ok('lbl_carregando_gp'):
            self.lbl_carregando_gp.configure(text="🔄 Carregando...")

        def _tarefa():
            con = obter_conexao()
            if not con:
                return
            colaboradores, afastamentos, periodos, erro = [], [], [], None
            try:
                cur = con.cursor(dictionary=True)
                try:
                    cur.execute(_DDL_PERIODOS)
                except mysql.connector.Error:
                    pass
                cur.execute("SELECT id, nome, tipo, data_nascimento FROM tb_colaboradores WHERE ativo=1 ORDER BY nome")
                colaboradores = cur.fetchall()
                cur.execute("""
                    SELECT a.id, a.colaborador_id, a.tipo_afastamento, a.data_inicio, a.data_fim, a.cor, a.observacao,
                           c.nome AS nome_colaborador, c.tipo AS tipo_colaborador
                    FROM tb_afastamentos a JOIN tb_colaboradores c ON c.id = a.colaborador_id
                """)
                afastamentos = cur.fetchall()
                cur.execute("SELECT id, colaborador_id, aquisitivo_inicio, aquisitivo_fim, concessivo_inicio, concessivo_fim FROM tb_periodos_ferias ORDER BY concessivo_inicio")
                periodos = cur.fetchall()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao carregar dados de Gestão de Pessoas: {err}")
                erro = err
            finally:
                cur.close()
                con.close()
            try:
                if self.master.winfo_exists():
                    self.master.after(0, lambda: self._aplicar_dados(colaboradores, afastamentos, erro, periodos))
            except (RuntimeError, tk.TclError):
                pass  # janela fechada / mainloop encerrado: ignora

        threading.Thread(target=_tarefa, daemon=True).start()

    def _aplicar_dados(self, colaboradores, afastamentos, erro, periodos=()):
        self.colaboradores, self.afastamentos, self.periodos = colaboradores, afastamentos, list(periodos)
        if erro:
            messagebox.showerror(
                "Erro no Banco",
                "Falha ao carregar dados de Gestão de Pessoas.\n"
                "Verifique se as tabelas tb_colaboradores e tb_afastamentos existem "
                "(ver instruções de migração).\n\n" + str(erro)
            )
        self._reindexar()
        if self._w_ok('lbl_carregando_gp'):
            self.lbl_carregando_gp.configure(text="")
        self._popular_lista_colaboradores()
        self._popular_periodos()
        self._popular_alertas_ferias()
        self.renderizar_calendario()

    def _reindexar(self):
        """Monta caches por id/nome/dia — evita varrer as listas inteiras a cada célula do calendário."""
        self._colab_por_id = {c['id']: c for c in self.colaboradores}
        self._colab_por_nome = {c['nome']: c for c in self.colaboradores}
        self._aniv_por_diames = {}
        for c in self.colaboradores:
            nd = _as_date(c.get('data_nascimento'))
            if nd:
                self._aniv_por_diames.setdefault((nd.month, nd.day), []).append(c)
        self._afast_por_dia = {}
        for a in self.afastamentos:
            ai, af = _as_date(a['data_inicio']), _as_date(a['data_fim'])
            if not ai or not af:
                continue
            d = ai
            while d <= af:
                self._afast_por_dia.setdefault(d, []).append(a)
                d += timedelta(days=1)

    def _popular_alertas_ferias(self):
        """Alerta 5 dias antes de entrar de férias e 1 dia antes de retornar;
        se a data calculada cair em fim de semana, o alerta passa a valer
        para o primeiro dia útil seguinte."""
        self._alertas = []
        hoje = date.today()
        for a in self.afastamentos:
            if a['tipo_afastamento'] != 'Férias':
                continue
            di, df = _as_date(a['data_inicio']), _as_date(a['data_fim'])
            if not di or not df:
                continue
            if _proximo_dia_util(di - timedelta(days=5)) == hoje:
                self._add_alerta(f"🏖️ {a['nome_colaborador']} entra de férias em {di.strftime('%d/%m')}.")
            if _proximo_dia_util(df) == hoje:
                self._add_alerta(f"↩️ {a['nome_colaborador']} retorna de férias amanhã ({(df + timedelta(days=1)).strftime('%d/%m')}).")

        ini_v, fim_v = self._intervalo_visivel()
        for a in self.afastamentos:
            di, df = _as_date(a['data_inicio']), _as_date(a['data_fim'])
            if di and df and di <= fim_v and df >= ini_v:
                self._add_alerta(f"🚫 {a['nome_colaborador']} afastado(a) por {a['tipo_afastamento']} de {di:%d/%m} a {df:%d/%m} ({(df - di).days + 1}d).")
        self.app.definir_notificacoes("gestao_pessoas", self._alertas)

    def _add_alerta(self, msg):
        self._alertas.append(msg)

    def _mostrar_toast(self, msg, cor="#2e7d32"):
        if self._toast_label is None:
            self._toast_label = ctk.CTkLabel(self.f_cal, text="", corner_radius=8, fg_color=cor,
                                              text_color="white", font=("Arial", 12, "bold"))
        if self._toast_after_id is not None:
            self.master.after_cancel(self._toast_after_id)
        self._toast_label.configure(text=msg, fg_color=cor)
        self._toast_label.place(relx=0.5, rely=0.06, anchor="center")
        self._toast_label.lift()
        self._toast_after_id = self.master.after(2200, self._toast_label.place_forget)

    # ------------------------------------------------------------------
    # COLABORADORES
    # ------------------------------------------------------------------
    def _colaboradores_filtrados(self):
        if self.filtro_tipo == "todos":
            return self.colaboradores
        return [c for c in self.colaboradores if c['tipo'] == self.filtro_tipo]

    def _w_ok(self, nome):
        w = getattr(self, nome, None)
        return w is not None and w.winfo_exists()

    def abrir_popup_colaborador_form(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Cadastrar Colaborador")
        janela.attributes("-topmost", True)
        janela.after(300, lambda: janela.attributes("-topmost", False))
        janela.after(100, lambda: (janela.lift(), janela.focus_force()))
        janela.geometry("340x620")
        janela.transient(self.master)
        self.e_nome_col = ctk.CTkEntry(janela, placeholder_text="Nome completo", width=250)
        self.e_nome_col.pack(pady=(15, 4))
        self.cmb_tipo_col = ctk.CTkComboBox(janela, values=["Procurador", "Apoio"], width=250)
        self.cmb_tipo_col.set("Procurador")
        self.cmb_tipo_col.pack(pady=4)
        self.e_nasc_col = ctk.CTkEntry(janela, placeholder_text="Aniversário (dd/mm ou dd/mm/aaaa)", width=250)
        self.e_nasc_col.pack(pady=4)
        self._aplicar_mascara_data(self.e_nasc_col)
        f_btn_col = ctk.CTkFrame(janela, fg_color="transparent")
        f_btn_col.pack(pady=4)
        ctk.CTkButton(f_btn_col, text="Salvar", width=120, command=self.salvar_colaborador).pack(side="left", padx=3)
        ctk.CTkButton(f_btn_col, text="Excluir", width=120, fg_color="#a83232", hover_color="#7a2323", command=self.excluir_colaborador).pack(side="left", padx=3)
        ctk.CTkButton(janela, text="Limpar Seleção", width=250, fg_color="gray", command=self.limpar_form_colaborador).pack(pady=(4, 8))
        ctk.CTkLabel(janela, text="Períodos de férias (aquisitivo / concessivo)", font=("Arial", 11, "bold")).pack(pady=(4, 0))
        ctk.CTkLabel(janela, text="Só o início do aquisitivo é obrigatório; o resto é calculado (12 meses).", font=("Arial", 10), text_color="gray").pack()
        f_per = ctk.CTkFrame(janela, fg_color="transparent")
        f_per.pack(pady=2)
        self.e_aq_ini, self.e_aq_fim, self.e_co_ini, self.e_co_fim = (
            ctk.CTkEntry(f_per, placeholder_text=ph, width=125)
            for ph in ("Aquis. início", "Aquis. fim", "Conces. início", "Conces. fim"))
        for i, e in enumerate((self.e_aq_ini, self.e_aq_fim, self.e_co_ini, self.e_co_fim)):
            e.grid(row=i // 2, column=i % 2, padx=3, pady=2)
            self._aplicar_mascara_data(e)
        ctk.CTkButton(janela, text="+ Adicionar período", width=250, command=self.adicionar_periodo).pack(pady=4)
        self.lst_periodos = ctk.CTkScrollableFrame(janela, width=280, height=130)
        self.lst_periodos.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        if self.colaborador_sel:
            self.selecionar_colaborador(self.colaborador_sel)

    def abrir_popup_relatorio(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Gerar Relatório")
        janela.geometry("320x460")
        janela.transient(self.master)
        janela.after(100, lambda: (janela.lift(), janela.focus_force()))
        ctk.CTkLabel(janela, text="Colaboradores no relatório:", font=("Arial", 11, "bold")).pack(pady=(15, 4))
        lista = ctk.CTkScrollableFrame(janela, width=270, height=290)
        lista.pack(fill="both", expand=True, padx=15, pady=4)
        vars_ = {}

        def _sync_todos():
            var_todos.set(all(v.get() for v in vars_.values()))

        def _marcar_todos():
            for v in vars_.values():
                v.set(var_todos.get())

        var_todos = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(lista, text="Selecionar todos", variable=var_todos, command=_marcar_todos,
                        font=("Arial", 12, "bold")).pack(anchor="w", pady=(2, 6))
        for c in self._colaboradores_filtrados():
            vars_[c['id']] = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(lista, text=f"{c['nome']} ({'Proc.' if c['tipo'] == 'procurador' else 'Apoio'})",
                            variable=vars_[c['id']], command=_sync_todos).pack(anchor="w", pady=1)

        def _gerar():
            ids = [i for i, v in vars_.items() if v.get()]
            if not ids:
                messagebox.showwarning("Aviso", "Selecione ao menos um colaborador.", parent=janela)
                return
            if self.gerar_relatorio(ids, janela):
                janela.destroy()

        ctk.CTkButton(janela, text="Gerar", width=250, command=_gerar).pack(pady=(6, 12))

    def _carencia_usada(self, p):
        """Dias úteis de carência usados no período concessivo p (só faz sentido p/ procurador)."""
        ci, cf = _as_date(p['concessivo_inicio']), _as_date(p['concessivo_fim'])
        return sum(_dias_uteis(_as_date(a['data_inicio']), _as_date(a['data_fim']))
                   for a in self.afastamentos
                   if a['colaborador_id'] == p['colaborador_id'] and a['tipo_afastamento'] == "Carência"
                   and ci <= _as_date(a['data_fim']) + timedelta(days=1) <= cf)

    def gerar_relatorio(self, ids, janela=None):
        """Gera PDF colorido com o controle de férias/carência dos colaboradores selecionados."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        caminho = filedialog.asksaveasfilename(
            parent=janela, defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile=f"relatorio_ferias_{date.today():%Y%m%d}.pdf")
        if not caminho:
            return False
        f = lambda v: _as_date(v).strftime('%d/%m/%Y')
        cab = ["Colaborador", "Tipo", "Aquisitivo", "Concessivo", "Direito", "Gozados", "Períodos férias",
               "Disponíveis", "Carência\nusada", "Períodos carência", "Carência\ndisp."]
        P = lambda t: Paragraph(str(t), ParagraphStyle('c', fontSize=8, leading=10))
        linhas, estilos = [cab], [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('ALIGN', (4, 1), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3)]

        def cor(i, col, bg):
            estilos.append(('BACKGROUND', (col, i), (col, i), colors.HexColor(bg)))
        try:
            for c in self.colaboradores:
                if c['id'] not in ids:
                    continue
                proc = c['tipo'] == 'procurador'
                base = [c['nome'], 'Procurador' if proc else 'Apoio']
                pers = [p for p in self.periodos if p['colaborador_id'] == c['id']]
                if not pers:
                    linhas.append(base + ["Sem períodos cadastrados"] + [""] * 8)
                    estilos.append(('BACKGROUND', (2, len(linhas) - 1), (-1, len(linhas) - 1), colors.HexColor("#eeeeee")))
                for p in pers:
                    saldo, gozados = self._saldo_ferias(p)
                    car = self._carencia_usada(p) if proc else None
                    cdisp = max(LIMITE_CARENCIA_DIAS - car, 0) if proc else "-"
                    ci_, cf_ = _as_date(p['concessivo_inicio']), _as_date(p['concessivo_fim'])
                    iv = lambda a, n: f"{f(a['data_inicio'])}&nbsp;a&nbsp;{f(a['data_fim'])} ({n} dias)"
                    pf = "<br/>".join(f"{f(i_)}&nbsp;a&nbsp;{f(f_)} ({n} dias)"
                                      for i_, f_, n in self._ferias_por_periodo(c['id']).get(p['id'], [])) or "-"
                    pc = "<br/>".join(iv(a, _dias_uteis(_as_date(a['data_inicio']), _as_date(a['data_fim'])))
                                      for a in self.afastamentos
                                      if a['colaborador_id'] == c['id'] and a['tipo_afastamento'] == "Carência"
                                      and ci_ <= _as_date(a['data_fim']) + timedelta(days=1) <= cf_) or "-" if proc else "-"
                    linhas.append([P(c['nome'])] + base[1:] + [P(f"{f(p['aquisitivo_inicio'])}&nbsp;a&nbsp;{f(p['aquisitivo_fim'])}"),
                                          P(f"{f(p['concessivo_inicio'])}&nbsp;a&nbsp;{f(p['concessivo_fim'])}"),
                                          DIAS_FERIAS_PERIODO, gozados, P(pf), saldo, "-" if car is None else car, P(pc), cdisp])
                    i = len(linhas) - 1
                    cor(i, 1, "#dbe9f7" if proc else "#e8e8e8")
                    cor(i, 7, "#c6efce" if saldo > 10 else "#ffeb9c" if saldo > 0 else "#ffc7ce")
                    if proc:
                        cor(i, 10, "#c6efce" if cdisp > 3 else "#ffeb9c" if cdisp > 0 else "#ffc7ce")
            st = getSampleStyleSheet()
            t = Table(linhas, repeatRows=1, colWidths=[100, 52, 88, 88, 36, 42, 122, 54, 46, 122, 42])
            t.setStyle(TableStyle(estilos))
            SimpleDocTemplate(caminho, pagesize=landscape(A4), leftMargin=25, rightMargin=25, topMargin=25, bottomMargin=25).build([
                Paragraph("Relatório de Férias e Carência", st['Title']),
                Paragraph(f"Gerado em {date.today():%d/%m/%Y} | Verde: folgado | Amarelo: atenção | Vermelho: esgotado", st['Normal']),
                Spacer(1, 10), t])
        except OSError as err:
            messagebox.showerror("Erro", f"Não foi possível salvar o relatório:\n{err}", parent=janela)
            return False
        self._mostrar_toast("📄 Relatório gerado!")
        return True

    def abrir_popup_ver_colaboradores(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Colaboradores Cadastrados")
        janela.geometry("320x420")
        janela.transient(self.master)
        self.e_busca_col = ctk.CTkEntry(janela, placeholder_text="🔎 Buscar por nome...", width=250)
        self.e_busca_col.pack(pady=(15, 4))
        self.e_busca_col.bind("<KeyRelease>", lambda e: self._filtrar_lista_colaboradores(self.e_busca_col.get()))
        self.lst_colaboradores = ctk.CTkScrollableFrame(janela, width=280, height=320)
        self.lst_colaboradores.pack(fill="both", expand=True, padx=15, pady=(4, 10))
        self._popular_lista_colaboradores()

    def abrir_popup_afastamento(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Afastamento Temporário")
        janela.geometry("320x660")
        janela.transient(self.master)
        self.lbl_col_sel = ctk.CTkLabel(janela, text=f"Colaborador: {self.colaborador_sel['nome'] if self.colaborador_sel else '(selecione abaixo)'}", wraplength=250, font=("Arial", 11, "bold"))
        self.lbl_col_sel.pack(pady=(15, 2))
        ctk.CTkComboBox(janela, values=[c['nome'] for c in self._colaboradores_filtrados()], width=250,
                         command=lambda nome: self.selecionar_colaborador(self._colab_por_nome[nome])).pack(pady=4)
        self.cmb_motivo = ctk.CTkComboBox(janela, values=MOTIVOS_AFASTAMENTO, width=250)
        self.cmb_motivo.set(MOTIVOS_AFASTAMENTO[0])
        self.cmb_motivo.pack(pady=4)
        self.e_data_ini = ctk.CTkEntry(janela, placeholder_text="Início (dd/mm/aaaa)", width=250)
        self.e_data_ini.pack(pady=4)
        self._aplicar_mascara_data(self.e_data_ini)
        self.e_data_fim = ctk.CTkEntry(janela, placeholder_text="Fim (dd/mm/aaaa)", width=250)
        self.e_data_fim.pack(pady=4)
        self._aplicar_mascara_data(self.e_data_fim)
        ctk.CTkLabel(janela, text="Cor da tarja no calendário:").pack(pady=(8, 2))
        f_cores = ctk.CTkFrame(janela, fg_color="transparent")
        f_cores.pack(pady=2)
        self._botoes_cor = {}
        for i, cor in enumerate(PALETA_CORES):
            b = ctk.CTkButton(f_cores, text="", width=26, height=26, fg_color=cor, hover_color=cor,
                               border_width=1, border_color="white",
                               command=lambda c=cor: self.escolher_cor(c))
            b.grid(row=i // 4, column=i % 4, padx=3, pady=3)
            self._botoes_cor[cor] = b
        self.e_obs_afast = ctk.CTkEntry(janela, placeholder_text="Observação (opcional)", width=250)
        self.e_obs_afast.pack(pady=(8, 4))
        self.chk_vendeu = ctk.CTkCheckBox(janela, text="Vendeu 10 dias de férias (abono)")
        self.chk_vendeu.pack(pady=(0, 4))
        self.btn_salvar_afast = ctk.CTkButton(janela, text="Registrar Afastamento", command=self.salvar_afastamento, fg_color="#1f538d", hover_color="#14375e", width=250)
        self.btn_salvar_afast.pack(pady=(8, 2))
        self.btn_cancelar_edicao_afast = ctk.CTkButton(janela, text="✕ Cancelar edição", command=self.cancelar_edicao_afastamento, fg_color="transparent", text_color=("gray30", "gray70"), width=250, height=24)
        ctk.CTkLabel(janela, text="Afastamentos no período visível:", font=("Arial", 11, "bold")).pack(pady=(10, 2))
        self.lst_afastamentos = ctk.CTkScrollableFrame(janela, width=280, height=200)
        self.lst_afastamentos.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self.escolher_cor(self.cor_escolhida)
        self._popular_lista_afastamentos_periodo()

    def _filtrar_lista_colaboradores(self, texto):
        self._busca_colab = texto.strip().lower()
        self._popular_lista_colaboradores()

    def _popular_lista_colaboradores(self):
        if not self._w_ok('lst_colaboradores'):
            return
        for w in self.lst_colaboradores.winfo_children():
            w.destroy()
        for c in self._colaboradores_filtrados():
            if self._busca_colab and self._busca_colab not in c['nome'].lower():
                continue
            txt = f"{c['nome']} ({'Proc.' if c['tipo'] == 'procurador' else 'Apoio'})"
            linha = ctk.CTkFrame(self.lst_colaboradores, fg_color="transparent")
            linha.pack(fill="x", pady=1)
            ctk.CTkLabel(linha, text=txt, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(linha, text="✏️", width=28,
                          command=lambda cc=c: (self.selecionar_colaborador(cc), self.abrir_popup_colaborador_form())).pack(side="right")

    def selecionar_colaborador(self, c):
        self.colaborador_sel = c
        if self._w_ok('lbl_col_sel'):
            self.lbl_col_sel.configure(text=f"Colaborador: {c['nome']}")
        if self._w_ok('e_nome_col'):
            self.e_nome_col.delete(0, 'end'); self.e_nome_col.insert(0, c['nome'])
            self.cmb_tipo_col.set('Procurador' if c['tipo'] == 'procurador' else 'Apoio')
            self.e_nasc_col.delete(0, 'end')
            nasc = _as_date(c.get('data_nascimento'))
            if nasc:
                self.e_nasc_col.insert(0, nasc.strftime('%d/%m/%Y'))
        self._popular_periodos()

    def limpar_form_colaborador(self):
        self.colaborador_sel = None
        if self._w_ok('e_nome_col'):
            self.e_nome_col.delete(0, 'end')
            self.e_nasc_col.delete(0, 'end')
            self.cmb_tipo_col.set('Procurador')
        if self._w_ok('lbl_col_sel'):
            self.lbl_col_sel.configure(text="Colaborador: (selecione na lista acima)")
        self._popular_periodos()

    def escolher_cor(self, cor):
        self.cor_escolhida = cor
        for c, btn in self._botoes_cor.items():
            btn.configure(border_width=3 if c == cor else 1, border_color="#ffd54f" if c == cor else "white")

    # --- Períodos aquisitivo/concessivo ---
    def _ferias_por_periodo(self, cid):
        """{id_periodo: [(inicio, fim, dias)]}: férias do colaborador abatidas do período mais antigo
        para o mais novo (até o direito de cada um); férias que cruzam períodos são divididas."""
        pers = sorted((q for q in self.periodos if q['colaborador_id'] == cid),
                      key=lambda q: (_as_date(q['aquisitivo_inicio']), q['id']))
        res = {q['id']: [] for q in pers}
        if not pers:
            return res
        ini = min(_as_date(q['concessivo_inicio']) for q in pers)
        fer = sorted((_as_date(a['data_inicio']), _as_date(a['data_fim'])) for a in self.afastamentos
                     if a['colaborador_id'] == cid and a['tipo_afastamento'] == "Férias"
                     and _as_date(a['data_inicio']) >= ini)
        usado = {q['id']: 0 for q in pers}
        for di, df in fer:
            for q in pers:
                if di > df:
                    break
                n = min((df - di).days + 1, DIAS_FERIAS_PERIODO - usado[q['id']])
                if n <= 0:
                    continue
                usado[q['id']] += n
                res[q['id']].append((di, di + timedelta(days=n - 1), n))
                di += timedelta(days=n)
        return res

    def _saldo_ferias(self, p):
        """Dias disponíveis e gozados do período p (abatimento do mais antigo para o mais novo)."""
        g = sum(n for _, _, n in self._ferias_por_periodo(p['colaborador_id']).get(p['id'], []))
        v = 10 * sum(1 for x in self.afastamentos if x['colaborador_id'] == p['colaborador_id']
                     and x['tipo_afastamento'] == "Férias" and TAG_VENDA in (x.get('observacao') or '')
                     and _as_date(p['concessivo_inicio']) <= _as_date(x['data_inicio']) <= _as_date(p['concessivo_fim']))
        return DIAS_FERIAS_PERIODO - g - v, g

    def _popular_periodos(self):
        if not self._w_ok('lst_periodos'):
            return
        for w in self.lst_periodos.winfo_children():
            w.destroy()
        if not self.colaborador_sel:
            return
        f = lambda v: _as_date(v).strftime('%d/%m/%Y')
        total = 0
        for p in self.periodos:
            if p['colaborador_id'] != self.colaborador_sel['id']:
                continue
            saldo, gozados = self._saldo_ferias(p)
            total += max(saldo, 0)
            linha = ctk.CTkFrame(self.lst_periodos, fg_color="transparent")
            linha.pack(fill="x", pady=1)
            ctk.CTkLabel(linha, anchor="w", justify="left", font=("Arial", 10),
                         text=f"Aq.: {f(p['aquisitivo_inicio'])} a {f(p['aquisitivo_fim'])}\nConc.: {f(p['concessivo_inicio'])} a {f(p['concessivo_fim'])}\n"
                              f"Disponíveis: {saldo} de {DIAS_FERIAS_PERIODO} dias (gozados: {gozados})"
                              + (f"\nCarência disponível: {max(LIMITE_CARENCIA_DIAS - self._carencia_usada(p), 0)} de {LIMITE_CARENCIA_DIAS} dias úteis"
                                 if self.colaborador_sel['tipo'] == 'procurador' else "")
                         ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(linha, text="🗑", width=28, fg_color="#a83232", hover_color="#7a2323",
                          command=lambda pid=p['id']: self.excluir_periodo(pid)).pack(side="right")
        ctk.CTkLabel(self.lst_periodos, text=f"Total disponível: {total} dias", font=("Arial", 11, "bold")).pack(pady=(4, 0))

    def adicionar_periodo(self):
        if not self.colaborador_sel:
            messagebox.showwarning("Aviso", "Selecione o colaborador (✏️ na lista) antes de adicionar períodos.")
            return
        try:
            aqi, aqf, coi, cof = (datetime.strptime(e.get().strip(), '%d/%m/%Y').date() if e.get().strip() else None
                                  for e in (self.e_aq_ini, self.e_aq_fim, self.e_co_ini, self.e_co_fim))
        except ValueError:
            messagebox.showwarning("Aviso", "Datas inválidas. Use dd/mm/aaaa.")
            return
        if not aqi:
            messagebox.showwarning("Aviso", "Informe ao menos o início do período aquisitivo.")
            return
        aqf = aqf or _fim_periodo(aqi)
        coi = coi or aqf + timedelta(days=1)
        cof = cof or _fim_periodo(coi)
        if aqf < aqi or cof < coi:
            messagebox.showwarning("Aviso", "A data final não pode ser anterior à inicial.")
            return
        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            cur.execute("""INSERT INTO tb_periodos_ferias (colaborador_id, aquisitivo_inicio, aquisitivo_fim,
                           concessivo_inicio, concessivo_fim, usuario_criacao, data_criacao)
                           VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                        (self.colaborador_sel['id'], aqi, aqf, coi, cof, getattr(self.app, 'usuario_atual', 'N/A')))
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao salvar período de férias: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()
        for e in (self.e_aq_ini, self.e_aq_fim, self.e_co_ini, self.e_co_fim):
            e.delete(0, 'end')
        self._mostrar_toast("✔ Período adicionado!")
        self.recarregar_dados()

    def excluir_periodo(self, pid):
        if not messagebox.askyesno("Confirmar", "Excluir este período?"):
            return
        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            cur.execute("DELETE FROM tb_periodos_ferias WHERE id=%s", (pid,))
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao excluir período de férias: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()
        self._mostrar_toast("🗑 Período excluído.", cor="#a83232")
        self.recarregar_dados()

    def salvar_colaborador(self):
        nome = self.e_nome_col.get().strip()
        if not nome:
            messagebox.showwarning("Aviso", "Informe o nome do colaborador.")
            return
        tipo = 'procurador' if self.cmb_tipo_col.get() == 'Procurador' else 'apoio'

        nasc_txt = self.e_nasc_col.get().strip()
        nasc_sql = None
        if nasc_txt:
            partes = nasc_txt.split('/')
            try:
                if len(partes) == 3:
                    d, m, a = partes
                    nasc_sql = date(int(a), int(m), int(d)).isoformat()
                elif len(partes) == 2:
                    d, m = partes
                    # Ano fictício quando só dd/mm é informado — o que importa
                    # para aniversário é o dia/mês, não o ano de nascimento.
                    nasc_sql = date(1900, int(m), int(d)).isoformat()
                else:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Aviso", "Data de aniversário inválida. Use dd/mm ou dd/mm/aaaa.")
                return

        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            if self.colaborador_sel:
                cur.execute(
                    "UPDATE tb_colaboradores SET nome=%s, tipo=%s, data_nascimento=%s WHERE id=%s",
                    (nome, tipo, nasc_sql, self.colaborador_sel['id'])
                )
            else:
                cur.execute(
                    "INSERT INTO tb_colaboradores (nome, tipo, data_nascimento, ativo, usuario_criacao, data_criacao) VALUES (%s, %s, %s, 1, %s, NOW())",
                    (nome, tipo, nasc_sql, getattr(self.app, 'usuario_atual', 'N/A'))
                )
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao salvar colaborador: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()

        self._mostrar_toast("💾 Colaborador atualizado!" if self.colaborador_sel else "✔ Colaborador cadastrado!")
        self.limpar_form_colaborador()
        self.recarregar_dados()

    def excluir_colaborador(self):
        if not self.colaborador_sel:
            messagebox.showwarning("Aviso", "Selecione um colaborador na lista.")
            return
        if not messagebox.askyesno("Confirmar", f"Inativar {self.colaborador_sel['nome']}? Os afastamentos já registrados são mantidos no histórico."):
            return
        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            # Inativa em vez de excluir de fato, para preservar o histórico
            # de afastamentos já vinculados a esse colaborador.
            cur.execute("UPDATE tb_colaboradores SET ativo=0 WHERE id=%s", (self.colaborador_sel['id'],))
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao inativar colaborador: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()

        self._mostrar_toast("🗑 Colaborador inativado.", cor="#a83232")
        self.limpar_form_colaborador()
        self.recarregar_dados()

    # ------------------------------------------------------------------
    # AFASTAMENTOS
    # ------------------------------------------------------------------
    def _afastamentos_filtrados(self):
        if self.filtro_tipo == "todos":
            return self.afastamentos
        return [a for a in self.afastamentos if a['tipo_colaborador'] == self.filtro_tipo]

    def _validar_carencia(self, di, df):
        """Retorna mensagem de erro (ou None). Carência: só procurador, e no máx.
        LIMITE_CARENCIA_DIAS dias ÚTEIS por período concessivo (o do 1º dia de férias, df+1)."""
        c = self.colaborador_sel
        if c['tipo'] != 'procurador':
            return "Carência se aplica apenas a procuradores."
        dia_ferias = df + timedelta(days=1)

        def _periodo(d):
            for p in self.periodos:
                if p['colaborador_id'] == c['id'] and _as_date(p['concessivo_inicio']) <= d <= _as_date(p['concessivo_fim']):
                    return p
        per = _periodo(dia_ferias)
        if not per:
            return "Cadastre o período concessivo do procurador (Colaboradores → ✏️) que contém o início das férias."
        edit_id = self.afastamento_sel['id'] if self.afastamento_sel else None
        usados = 0
        for a in self.afastamentos:
            if a['colaborador_id'] == c['id'] and a['tipo_afastamento'] == "Carência" and a['id'] != edit_id:
                fa = _as_date(a['data_fim'])
                if _periodo(fa + timedelta(days=1)) is per:
                    usados += _dias_uteis(_as_date(a['data_inicio']), fa)
        novo = _dias_uteis(di, df)
        if usados + novo > LIMITE_CARENCIA_DIAS:
            return f"Limite de {LIMITE_CARENCIA_DIAS} dias úteis de carência por período concessivo excedido (já usados: {usados}; este: {novo})."

    def salvar_afastamento(self):
        if not self.colaborador_sel:
            messagebox.showwarning("Aviso", "Selecione um colaborador na lista de colaboradores.")
            return
        try:
            di = datetime.strptime(self.e_data_ini.get().strip(), '%d/%m/%Y').date()
            df = datetime.strptime(self.e_data_fim.get().strip(), '%d/%m/%Y').date()
        except ValueError:
            messagebox.showwarning("Aviso", "Datas inválidas. Use o formato dd/mm/aaaa.")
            return
        if df < di:
            messagebox.showwarning("Aviso", "A data de fim não pode ser anterior à data de início.")
            return
        if self.cmb_motivo.get() == "Carência":
            erro = self._validar_carencia(di, df)
            if erro:
                messagebox.showwarning("Carência", erro)
                return

        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            if self.afastamento_sel:
                cur.execute(
                    """UPDATE tb_afastamentos SET colaborador_id=%s, tipo_afastamento=%s, data_inicio=%s,
                       data_fim=%s, cor=%s, observacao=%s WHERE id=%s""",
                    (self.colaborador_sel['id'], self.cmb_motivo.get(), di, df, self.cor_escolhida,
                     self._obs_final(), self.afastamento_sel['id'])
                )
            else:
                cur.execute(
                    """INSERT INTO tb_afastamentos
                       (colaborador_id, tipo_afastamento, data_inicio, data_fim, cor, observacao, usuario_criacao, data_criacao)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (self.colaborador_sel['id'], self.cmb_motivo.get(), di, df, self.cor_escolhida,
                     self._obs_final(), getattr(self.app, 'usuario_atual', 'N/A'))
                )
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao salvar afastamento: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()

        self._mostrar_toast("💾 Afastamento atualizado!" if self.afastamento_sel else "✔ Afastamento registrado!")
        self.cancelar_edicao_afastamento(recarregar=False)
        self.recarregar_dados()

    def _obs_final(self):
        obs = self.e_obs_afast.get().strip()
        if self.chk_vendeu.get() and self.cmb_motivo.get() == "Férias":
            obs = f"{TAG_VENDA} {obs}".strip()
        return obs or None

    def editar_afastamento(self, a, janela=None):
        """Carrega um afastamento existente no formulário para edição."""
        if not self._w_ok('btn_salvar_afast'):
            self.abrir_popup_afastamento()
        self.afastamento_sel = a
        colab = self._colab_por_id.get(a['colaborador_id'])
        if colab:
            self.selecionar_colaborador(colab)
        else:
            self.lbl_col_sel.configure(text=f"Colaborador: {a['nome_colaborador']}")

        self.cmb_motivo.set(a['tipo_afastamento'])
        ai, af = _as_date(a['data_inicio']), _as_date(a['data_fim'])
        self.e_data_ini.delete(0, 'end'); self.e_data_ini.insert(0, ai.strftime('%d/%m/%Y'))
        self.e_data_fim.delete(0, 'end'); self.e_data_fim.insert(0, af.strftime('%d/%m/%Y'))
        self.e_obs_afast.delete(0, 'end'); self.e_obs_afast.insert(0, (a.get('observacao') or '').replace(TAG_VENDA, '').strip())
        self.chk_vendeu.select() if TAG_VENDA in (a.get('observacao') or '') else self.chk_vendeu.deselect()
        self.escolher_cor(a['cor'])

        self.btn_salvar_afast.configure(text="💾 Atualizar Afastamento")
        self.btn_cancelar_edicao_afast.pack(pady=(0, 8))
        if janela is not None:
            janela.destroy()

    def cancelar_edicao_afastamento(self, recarregar=True):
        self.afastamento_sel = None
        self.e_data_ini.delete(0, 'end')
        self.e_data_fim.delete(0, 'end')
        self.e_obs_afast.delete(0, 'end')
        self.chk_vendeu.deselect()
        self.btn_salvar_afast.configure(text="Registrar Afastamento")
        self.btn_cancelar_edicao_afast.pack_forget()

    def excluir_afastamento(self, afast_id, janela=None):
        if not messagebox.askyesno("Confirmar", "Excluir este afastamento?"):
            return
        con = obter_conexao()
        if not con:
            return
        try:
            cur = con.cursor()
            cur.execute("DELETE FROM tb_afastamentos WHERE id=%s", (afast_id,))
            con.commit()
        except mysql.connector.Error as err:
            logging.error(f"Erro ao excluir afastamento: {err}")
            messagebox.showerror("Erro no Banco", str(err))
            return
        finally:
            cur.close(); con.close()

        if self.afastamento_sel and self.afastamento_sel['id'] == afast_id:
            self.cancelar_edicao_afastamento(recarregar=False)
        if janela is not None:
            janela.destroy()
        self._mostrar_toast("🗑 Afastamento excluído.", cor="#a83232")
        self.recarregar_dados()

    # ------------------------------------------------------------------
    # CONSULTAS AUXILIARES (POR DIA)
    # ------------------------------------------------------------------
    def _afastamentos_no_dia(self, d):
        res = self._afast_por_dia.get(d, [])
        if self.filtro_tipo == "todos":
            return res
        return [a for a in res if a['tipo_colaborador'] == self.filtro_tipo]

    def _aniversariantes_no_dia(self, d):
        res = self._aniv_por_diames.get((d.month, d.day), [])
        if self.filtro_tipo == "todos":
            return res
        return [c for c in res if c['tipo'] == self.filtro_tipo]

    def _intervalo_visivel(self):
        if self.modo == "dia":
            return self.data_ref, self.data_ref
        if self.modo == "semana":
            ini = self.data_ref - timedelta(days=self.data_ref.weekday())
            return ini, ini + timedelta(days=6)
        if self.modo == "mes":
            ini = self.data_ref.replace(day=1)
            fim = ini.replace(day=calendar.monthrange(ini.year, ini.month)[1])
            return ini, fim
        return date(self.data_ref.year, 1, 1), date(self.data_ref.year, 12, 31)

    # ------------------------------------------------------------------
    # NAVEGAÇÃO / MODO / FILTRO
    # ------------------------------------------------------------------
    def navegar(self, delta):
        if self.modo == "dia":
            self.data_ref += timedelta(days=delta)
        elif self.modo == "semana":
            self.data_ref += timedelta(weeks=delta)
        elif self.modo == "mes":
            m = self.data_ref.month - 1 + delta
            a = self.data_ref.year + m // 12
            m = m % 12 + 1
            dia = min(self.data_ref.day, calendar.monthrange(a, m)[1])
            self.data_ref = date(a, m, dia)
        else:
            self.data_ref = self.data_ref.replace(year=self.data_ref.year + delta)
        self.renderizar_calendario()

    def ir_para_hoje(self):
        self.data_ref = date.today()
        self.renderizar_calendario()

    def mudar_modo(self, valor):
        self.modo = {"Dia": "dia", "Semana": "semana", "Mês": "mes", "Ano": "ano"}[valor]
        self.renderizar_calendario()

    def mudar_filtro(self, valor):
        self.filtro_tipo = {"Todos": "todos", "Procurador": "procurador", "Apoio": "apoio"}[valor]
        self._popular_lista_colaboradores()
        self.renderizar_calendario()

    # ------------------------------------------------------------------
    # RENDERIZAÇÃO DO CALENDÁRIO
    # ------------------------------------------------------------------
    def renderizar_calendario(self):
        for w in self.f_area_cal.winfo_children():
            w.destroy()
        if self.modo == "mes":
            self._renderizar_mes()
        elif self.modo == "semana":
            self._renderizar_semana()
        elif self.modo == "dia":
            self._renderizar_dia()
        else:
            self._renderizar_ano()
        self._popular_lista_afastamentos_periodo()
        self._popular_alertas_ferias()

    def _renderizar_mes(self):
        ano, mes = self.data_ref.year, self.data_ref.month
        self.lbl_periodo.configure(text=f"{MESES_PT[mes - 1]} de {ano}")

        grid = ctk.CTkFrame(self.f_area_cal, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        # 'uniform' força as 7 colunas a terem EXATAMENTE a mesma largura,
        # independente do conteúdo de cada célula (sem isso, colunas com
        # texto mais curto ou células vazias podem encolher bem mais que as
        # outras — foi o que causou o efeito de colunas espremidas).
        for i in range(7):
            grid.grid_columnconfigure(i, weight=1, uniform="col_mes", minsize=90)
        for i, dnome in enumerate(DIAS_SEMANA_PT):
            ctk.CTkLabel(grid, text=dnome, font=("Arial", 11, "bold")).grid(row=0, column=i, sticky="nsew", padx=1, pady=1)

        semanas = calendar.Calendar(firstweekday=0).monthdayscalendar(ano, mes)
        for r, semana in enumerate(semanas, start=1):
            for c, dia in enumerate(semana):
                if dia == 0:
                    self._criar_celula_vazia(grid, r, c)
                    continue
                self._criar_celula_dia_mes(grid, date(ano, mes, dia), r, c)

        for r in range(1, len(semanas) + 1):
            grid.grid_rowconfigure(r, weight=1, minsize=70, uniform="row_mes")

    def _criar_celula_dia_mes(self, parent, d, r, c):
        afast_dia = self._afastamentos_no_dia(d)
        aniv_dia = self._aniversariantes_no_dia(d)
        cores_dia = self._cores_unicas_dia(afast_dia)
        destaque = "#ffd54f" if d == date.today() else None

        cel = ctk.CTkFrame(parent, fg_color="transparent", border_width=1, border_color="#444444", corner_radius=4)
        cel.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

        if cores_dia:
            # Uma faixa HORIZONTAL por cor distinta de afastamento no dia —
            # antes a célula inteira só ficava com a cor do primeiro
            # afastamento, então um dia com colaboradores de cores
            # diferentes "escondia" a segunda cor.
            # Uma faixa por cor, com altura proporcional ao nº de linhas de
            # texto; os nomes ficam DENTRO da própria faixa (por cima da cor).
            grupos = {}
            for a in afast_dia:
                grupos.setdefault(_blend(a['cor'], 0.35), []).append(
                    f"{a['nome_colaborador'].split()[0]} ({a['tipo_afastamento']})")
            if aniv_dia:
                grupos[cores_dia[-1]].append("🎂 " + ", ".join(a['nome'].split()[0] for a in aniv_dia))
            total = sum(len(v) for v in grupos.values())
            y = 0
            for i, (cor_bg, linhas) in enumerate(grupos.items()):
                h = len(linhas) / total
                faixa = ctk.CTkFrame(cel, fg_color=cor_bg, corner_radius=0)
                faixa.place(relx=0, rely=y, relwidth=1, relheight=h)
                y += h
                cor_txt = _texto_legivel(cor_bg)
                if i == 0:
                    ctk.CTkLabel(faixa, text=str(d.day), font=("Arial", 12, "bold" if destaque else "normal"),
                                 text_color=destaque or cor_txt).place(relx=1, x=-4, y=1, anchor="ne")
                ctk.CTkLabel(faixa, text="\n".join(linhas), font=("Arial", 9), wraplength=85,
                             justify="left", text_color=cor_txt).pack(anchor="nw", padx=4, pady=1)
        else:
            ctk.CTkLabel(cel, text=str(d.day), font=("Arial", 12, "bold" if destaque else "normal"),
                         text_color=destaque).pack(anchor="ne", padx=4, pady=2)
            if aniv_dia:
                ctk.CTkLabel(cel, text="🎂 " + ", ".join(a['nome'].split()[0] for a in aniv_dia),
                             font=("Arial", 9), wraplength=85).pack(anchor="w", padx=4)

        cel.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))
        for child in cel.winfo_children():
            child.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))
            for neto in child.winfo_children():
                neto.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))

    def _renderizar_semana(self):
        inicio = self.data_ref - timedelta(days=self.data_ref.weekday())
        fim = inicio + timedelta(days=6)
        self.lbl_periodo.configure(text=f"{inicio.strftime('%d/%m')} a {fim.strftime('%d/%m/%Y')}")

        grid = ctk.CTkFrame(self.f_area_cal, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        for i in range(7):
            grid.grid_columnconfigure(i, weight=1, uniform="col_semana", minsize=110)
        for i in range(7):
            d = inicio + timedelta(days=i)
            col = ctk.CTkFrame(grid, fg_color="transparent")
            col.grid(row=0, column=i, sticky="nsew", padx=2)

            ctk.CTkLabel(col, text=f"{DIAS_SEMANA_PT[i]} {d.day:02d}", font=("Arial", 11, "bold"),
                         text_color="#ffd54f" if d == date.today() else None).pack(pady=4)

            for a in self._afastamentos_no_dia(d):
                cor_bg = _blend(a['cor'], 0.55)
                b = ctk.CTkFrame(col, fg_color=cor_bg, corner_radius=4)
                b.pack(fill="x", pady=2, padx=2)
                lbl = ctk.CTkLabel(b, text=f"{a['nome_colaborador'].split()[0]}\n{a['tipo_afastamento']}", font=("Arial", 9), justify="left", text_color=_texto_legivel(cor_bg))
                lbl.pack(padx=4, pady=3)
                b.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))
                lbl.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))

            for c in self._aniversariantes_no_dia(d):
                ctk.CTkLabel(col, text=f"🎂 {c['nome'].split()[0]}", font=("Arial", 9)).pack(pady=1)

    def _renderizar_dia(self):
        d = self.data_ref
        self.lbl_periodo.configure(text=d.strftime('%d/%m/%Y'))

        afast = self._afastamentos_no_dia(d)
        aniv = self._aniversariantes_no_dia(d)

        ctk.CTkLabel(self.f_area_cal, text="Afastados neste dia", font=("Arial", 13, "bold")).pack(anchor="w", pady=(10, 4), padx=10)
        if not afast:
            ctk.CTkLabel(self.f_area_cal, text="Nenhum colaborador afastado.", text_color="gray").pack(anchor="w", padx=20)
        for a in afast:
            f = ctk.CTkFrame(self.f_area_cal, fg_color=a['cor'])
            f.pack(fill="x", padx=15, pady=4)
            tipo_txt = 'Proc.' if a['tipo_colaborador'] == 'procurador' else 'Apoio'
            ctk.CTkLabel(f, text=f"{a['nome_colaborador']} ({tipo_txt}) — {a['tipo_afastamento']}", text_color=_texto_legivel(a['cor'])).pack(side="left", padx=10, pady=8)

        ctk.CTkLabel(self.f_area_cal, text="🎂 Aniversariantes", font=("Arial", 13, "bold")).pack(anchor="w", pady=(15, 4), padx=10)
        if not aniv:
            ctk.CTkLabel(self.f_area_cal, text="Nenhum aniversário neste dia.", text_color="gray").pack(anchor="w", padx=20)
        for c in aniv:
            ctk.CTkLabel(self.f_area_cal, text=c['nome']).pack(anchor="w", padx=20)

    def _renderizar_ano(self):
        ano = self.data_ref.year
        self.lbl_periodo.configure(text=str(ano))

        outer = ctk.CTkScrollableFrame(self.f_area_cal)
        outer.pack(fill="both", expand=True)
        for i in range(12):
            mes = i + 1
            bloco = ctk.CTkFrame(outer)
            bloco.grid(row=i // 3, column=i % 3, padx=8, pady=8, sticky="nsew")
            ctk.CTkLabel(bloco, text=MESES_PT[i], font=("Arial", 12, "bold")).pack(pady=(4, 2))

            gridm = ctk.CTkFrame(bloco, fg_color="transparent")
            gridm.pack()
            for i in range(7):
                gridm.grid_columnconfigure(i, weight=1, uniform="col_ano", minsize=24)
            for r, semana in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(ano, mes)):
                for c, dia in enumerate(semana):
                    if dia == 0:
                        continue
                    d = date(ano, mes, dia)
                    afast = self._afastamentos_no_dia(d)
                    aniv = self._aniversariantes_no_dia(d)
                    cor = _blend(afast[0]['cor'], 0.5) if afast else None
                    txt = "🎂" if aniv else str(dia)
                    lbl = ctk.CTkLabel(gridm, text=txt, width=22, height=18, fg_color=cor, corner_radius=3, font=("Arial", 8),
                                        text_color=_texto_legivel(cor) if cor else None)
                    lbl.grid(row=r, column=c, padx=1, pady=1)
                    lbl.bind("<Button-1>", lambda e, dd=d: self._abrir_detalhe_dia(dd))
        for i in range(3):
            outer.grid_columnconfigure(i, weight=1)

    def _abrir_detalhe_dia(self, d):
        janela = ctk.CTkToplevel(self.master)
        janela.title(d.strftime('%d/%m/%Y'))
        janela.geometry("380x420")
        janela.transient(self.master)

        ctk.CTkLabel(janela, text=d.strftime('%d/%m/%Y'), font=("Arial", 15, "bold")).pack(pady=10)
        afast = self._afastamentos_no_dia(d)
        aniv = self._aniversariantes_no_dia(d)

        if not afast and not aniv:
            ctk.CTkLabel(janela, text="Nenhum evento neste dia.", text_color="gray").pack(pady=30)

        for a in afast:
            linha = ctk.CTkFrame(janela, fg_color="transparent")
            linha.pack(fill="x", padx=15, pady=4)
            ctk.CTkButton(linha, text=f"{a['nome_colaborador']} — {a['tipo_afastamento']}", anchor="w",
                          fg_color=a['cor'], hover_color=_blend(a['cor'], 0.75), text_color=_texto_legivel(a['cor']),
                          command=lambda aa=a: self.editar_afastamento(aa, janela)).pack(side="left", fill="x", expand=True, padx=(0, 6))
            ctk.CTkButton(linha, text="✕", width=28, fg_color="#a83232", hover_color="#7a2323",
                          command=lambda aid=a['id']: self.excluir_afastamento(aid, janela)).pack(side="right")

        if aniv:
            ctk.CTkLabel(janela, text="🎂 Aniversariantes", font=("Arial", 12, "bold")).pack(pady=(15, 4))
            for c in aniv:
                ctk.CTkLabel(janela, text=c['nome']).pack()

        ctk.CTkButton(janela, text="Fechar", command=janela.destroy, width=100).pack(pady=15)

    def _popular_lista_afastamentos_periodo(self):
        if not self._w_ok('lst_afastamentos'):
            return
        for w in self.lst_afastamentos.winfo_children():
            w.destroy()
        ini, fim = self._intervalo_visivel()
        for a in self._afastamentos_filtrados():
            ai, af = _as_date(a['data_inicio']), _as_date(a['data_fim'])
            if af < ini or ai > fim:
                continue
            linha = ctk.CTkFrame(self.lst_afastamentos, fg_color="transparent")
            linha.pack(fill="x", pady=2)
            txt = f"{a['nome_colaborador']} — {a['tipo_afastamento']} ({ai.strftime('%d/%m')}–{af.strftime('%d/%m')})"
            ctk.CTkButton(linha, text=txt, anchor="w", fg_color=a['cor'], hover_color=_blend(a['cor'], 0.75),
                          text_color=_texto_legivel(a['cor']), font=("Arial", 9),
                          command=lambda aa=a: self.editar_afastamento(aa)).pack(side="left", fill="x", expand=True, padx=(0, 4))
            ctk.CTkButton(linha, text="✕", width=24, fg_color="#a83232", hover_color="#7a2323",
                          command=lambda aid=a['id']: self.excluir_afastamento(aid)).pack(side="right")