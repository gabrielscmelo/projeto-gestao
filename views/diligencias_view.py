import re
import logging
from datetime import datetime
from collections import Counter
from tkinter import ttk, messagebox
import customtkinter as ctk
import threading
import mysql.connector

from database.connection import obter_conexao
from utils.debouncer import Debouncer
from utils.pdf_exporter import exportar_treeview_pdf
from utils.formatters import (
    aplicar_mascara_data_auto, calcular_status_diligencia,
    formatar_e_validar_data, formatar_saj, formatar_sei, formatar_diligencia
)


class AbaDiligencias:
    def __init__(self, master, app_instance):
        self.master = master
        self.app = app_instance
        self.id_diligencia_sel = None
        self.contagem_secretarias = {}
        self._popup_contador = None
        # Guarda a ordenação (coluna + direção) escolhida pelo usuário, para que
        # ela seja reaplicada automaticamente após qualquer recarga da lista
        # (cadastro, atualização, exclusão, conclusão), em vez de a lista voltar
        # sempre para a ordem padrão por ID.
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        # Guarda o texto "puro" (sem seta ▲/▼) de cada cabeçalho de coluna,
        # para poder remontar o texto com/sem indicador de ordenação.
        self._col_headers = {}
        self._toast_label = None
        self._toast_after_id = None
        self.montar_aba()

    def _validar_input_diligencia(self, texto_novo):
        """
        Permite a digitação de números, barras, espaços e caracteres específicos para a sigla S/Nº.
        """
        if not texto_novo:
            return True
        if texto_novo == "Diligência (ex.: 24 ou S/Nº)":
            return True
        return bool(re.match(r'^[0-9/a-zA-Zº°\s\-]*$', texto_novo))

    def _ordenar_coluna(self, col, reverse):
        """
        Ordena o Treeview pela coluna clicada, tratando datas, números e texto,
        e alternando entre ordem crescente e decrescente a cada clique.
        """
        itens = [(self.tab_dil_tree.set(k, col), k) for k in self.tab_dil_tree.get_children('')]

        def chave_ordenacao(item):
            val = str(item[0]).strip()
            # Tratamento para datas no formato DD/MM/AAAA
            if re.match(r'^\d{2}/\d{2}/\d{4}', val):
                try:
                    return datetime.strptime(val[:10], '%d/%m/%Y')
                except ValueError:
                    pass
            # Tratamento para status com dias entre parênteses, ex.: "Em atraso (-11d)"
            # Sem isso, a ordenação era feita como texto puro, então "-11d" ficava
            # antes de "-1d" (comparação de caracteres, não de valor numérico).
            m = re.search(r'\((-?\d+)d\)\s*$', val)
            if m:
                return int(m.group(1))
            # Tratamento para números inteiros (como ID)
            if val.isdigit():
                return int(val)
            return val.lower()

        try:
            itens.sort(key=chave_ordenacao, reverse=reverse)
        except Exception:
            itens.sort(key=lambda x: str(x[0]).lower(), reverse=reverse)

        for index, (val, k) in enumerate(itens):
            self.tab_dil_tree.move(k, '', index)

        # Alterna o comando para a próxima ordenação ser inversa
        self.tab_dil_tree.heading(
            col,
            command=lambda _col=col: self._ordenar_coluna(_col, not reverse)
        )

        # Guarda a ordenação atual para que possa ser reaplicada depois que a
        # lista for recarregada do banco (ver _reaplicar_ordenacao).
        self._sort_coluna_atual = col
        self._sort_reverse_atual = reverse
        self._atualizar_estado_btn_filtro()
        self._atualizar_headers_ordenacao()

    def _atualizar_headers_ordenacao(self):
        """
        Reescreve o texto de todos os cabeçalhos, acrescentando uma seta
        (▲ crescente / ▼ decrescente) apenas na coluna atualmente ordenada.
        Assim, quem abre a tela depois de outra pessoa mexer na ordenação
        consegue ver de imediato qual coluna e direção estão ativas.
        """
        for col, texto_base in self._col_headers.items():
            if col == self._sort_coluna_atual:
                seta = " ▼" if self._sort_reverse_atual else " ▲"
                self.tab_dil_tree.heading(col, text=texto_base + seta)
            else:
                self.tab_dil_tree.heading(col, text=texto_base)

    def _reaplicar_ordenacao(self):
        """
        Reaplica a última ordenação de coluna escolhida pelo usuário.
        Deve ser chamada sempre que a Treeview for repopulada a partir do
        banco (após cadastrar, atualizar, excluir ou concluir uma
        diligência), pois o SELECT no banco não garante a ordem que o
        usuário escolheu ao clicar em uma coluna.
        """
        if self._sort_coluna_atual is not None:
            self._ordenar_coluna(self._sort_coluna_atual, self._sort_reverse_atual)

    def _atualizar_estado_btn_filtro(self):
        """
        Reflete no botão de filtro/ordenação se há alguma ordenação de
        coluna ativa (destacado) ou não (neutro).
        """
        if not hasattr(self, 'btn_filtro_ativo'):
            return
        if self._sort_coluna_atual is not None:
            self.btn_filtro_ativo.configure(fg_color="#1f538d", hover_color="#14375e")
        else:
            self.btn_filtro_ativo.configure(fg_color="#454545", hover_color="#2b2b2b")

    def dil_limpar_ordenacao(self):
        """
        Limpa a ordenação de coluna ativa, voltando a lista para a ordem
        padrão (por ID) devolvida pelo banco.
        """
        self._sort_coluna_atual = None
        self._sort_reverse_atual = False
        self._atualizar_estado_btn_filtro()
        self._atualizar_headers_ordenacao()
        self.dil_listar()

    def _criar_popup_contador(self):
        """
        Cria (uma única vez) o pop-up flutuante do contador. Ele é reaproveitado
        a cada hover (mostrado/escondido com deiconify/withdraw) em vez de ser
        destruído e recriado — isso evita o bug de pop-ups "fantasma" duplicados
        aparecendo em posições erradas na tela.
        """
        popup = ctk.CTkToplevel(self.master)
        popup.withdraw()
        popup.wm_overrideredirect(True)
        popup.attributes("-topmost", True)

        frame = ctk.CTkFrame(popup, corner_radius=8, border_width=1, border_color="#555555")
        frame.pack()

        self._popup_contador = popup
        self._popup_frame_contador = frame

    def _preencher_popup_contador(self):
        for widget in self._popup_frame_contador.winfo_children():
            widget.destroy()

        container = ctk.CTkFrame(self._popup_frame_contador, fg_color="transparent")
        container.pack(padx=14, pady=10)

        if not self.contagem_secretarias:
            ctk.CTkLabel(container, text="Nenhuma diligência ativa no filtro atual.").pack()
            return

        for secretaria, qtd in sorted(self.contagem_secretarias.items(), key=lambda x: -x[1]):
            linha = ctk.CTkFrame(container, fg_color="transparent")
            linha.pack(fill="x", pady=2)
            ctk.CTkLabel(linha, text=secretaria, anchor="w").pack(side="left")
            ctk.CTkLabel(linha, text=str(qtd), font=("Arial", 12, "bold"), anchor="e").pack(side="right", padx=(20, 0))

    def _mostrar_popup_contador(self, event=None):
        if self._popup_contador is None:
            self._criar_popup_contador()

        self._preencher_popup_contador()

        # Garante que as dimensões estejam atualizadas antes de calcular a posição,
        # evitando que o pop-up apareça deslocado (posição "fantasma").
        self.master.update_idletasks()
        x = self.lbl_contador_dil.winfo_rootx()
        y = self.lbl_contador_dil.winfo_rooty() + self.lbl_contador_dil.winfo_height() + 4
        self._popup_contador.geometry(f"+{x}+{y}")
        self._popup_contador.deiconify()
        self._popup_contador.lift()

    def _esconder_popup_contador(self, event=None):
        if self._popup_contador is not None:
            self._popup_contador.withdraw()

    def _atualizar_contador_dil(self, linhas):
        """
        Recalcula o total de diligências ativas e a contagem por Secretaria
        (uma diligência pode estar lotada em mais de uma Secretaria, separadas
        por vírgula, ex.: "SEDEC,SAS,URB" — cada uma é contabilizada).
        """
        contagem = Counter()
        for row in linhas:
            sec_bruta = (row.get('secretaria') or "-").strip()
            if sec_bruta and sec_bruta != "-":
                for parte in sec_bruta.split(","):
                    parte = parte.strip()
                    if parte:
                        contagem[parte] += 1
            else:
                contagem["Sem Secretaria"] += 1

        self.contagem_secretarias = dict(contagem)
        self.lbl_contador_dil.configure(text=f"📋 Diligências Ativas: {len(linhas)}")

        # Se o pop-up já estiver visível no momento da atualização, atualiza o conteúdo
        if self._popup_contador is not None and self._popup_contador.winfo_ismapped():
            self._preencher_popup_contador()

    def montar_aba(self):
        self.f_form_dil = ctk.CTkFrame(self.master, width=230)
        self.f_form_dil.pack(side="left", fill="y", padx=10, pady=10)
        # Impede que o frame se redimensione para caber o conteúdo interno,
        # garantindo que a largura definida acima (230) seja respeitada.
        self.f_form_dil.pack_propagate(False)

        # Faixa reservada só para o aviso de "Diligência atualizada/cadastrada".
        # Tem altura fixa (pack_propagate(False)) para que o espaço já exista
        # mesmo com o aviso escondido — assim, quando ele aparece, ocupa essa
        # faixa própria em vez de flutuar por cima do título abaixo.
        self.f_toast_dil = ctk.CTkFrame(self.f_form_dil, fg_color="transparent", height=32)
        self.f_toast_dil.pack(fill="x", padx=10, pady=(8, 0))
        self.f_toast_dil.pack_propagate(False)

        ctk.CTkLabel(self.f_form_dil, text="Lotação Diligência", font=("Arial", 18, "bold")).pack(pady=5)

        self.e_saj_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="SAJ (ex: 2025.01.24)", width=195)
        self.e_saj_dil.pack(pady=8)
        self.e_saj_dil.bind("<FocusOut>", lambda e: self.aplicar_mascara_campo(self.e_saj_dil, formatar_saj))

        self.e_sei_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="SEI (ex: 05.123/2024-01)", width=195)
        self.e_sei_dil.pack(pady=8)
        self.e_sei_dil.bind("<FocusOut>", lambda e: self.aplicar_mascara_campo(self.e_sei_dil, formatar_sei))

        cmd_valida_dil = (self.master.register(self._validar_input_diligencia), '%P')
        self.e_num_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Diligência (ex: 24 ou S/Nº)", width=195)
        self.e_num_dil.pack(pady=8)
        self.e_num_dil.bind("<FocusOut>", lambda e: self.aplicar_mascara_campo(self.e_num_dil, formatar_diligencia))

        self.e_sec_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Secretaria", width=195)
        self.e_sec_dil.pack(pady=8)

        self.e_caminho_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Caminho / Pasta", width=195)
        self.e_caminho_dil.pack(pady=8)

        self.e_idsei_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Id SEI (documento da diligência)", width=195)
        self.e_idsei_dil.pack(pady=8)

        # COMBOBOX DA CATEGORIA
        self.cmb_categoria = ctk.CTkComboBox(
            self.f_form_dil, 
            values=["Patrimonial e Urbanismo", "Desapropriação", "Geral"], 
            width=195,
            state="readonly"
        )
        self.cmb_categoria.set("Geral")
        self.cmb_categoria.pack(pady=8)

        self.e_envio_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Data Envio (DD/MM/AAAA)", width=195)
        self.e_envio_dil.pack(pady=8)
        self.e_envio_dil.bind("<KeyRelease>", aplicar_mascara_data_auto)

        self.e_prazo_dil = ctk.CTkEntry(self.f_form_dil, placeholder_text="Prazo (DD/MM/AAAA)", width=195)
        self.e_prazo_dil.pack(pady=8)
        self.e_prazo_dil.bind("<KeyRelease>", aplicar_mascara_data_auto)

        self.btn_salvar_dil = ctk.CTkButton(self.f_form_dil, text="Salvar Novo", command=self.dil_cadastrar, fg_color="green", hover_color="darkgreen", width=195)
        self.btn_salvar_dil.pack(pady=10)

        f_btns_dil = ctk.CTkFrame(self.f_form_dil, fg_color="transparent")
        f_btns_dil.pack(pady=5)
        self.btn_atualizar_dil = ctk.CTkButton(f_btns_dil, text="Atualizar", command=self.dil_atualizar, fg_color="orange", hover_color="#b37400", width=90)
        self.btn_atualizar_dil.pack(side="left", padx=(0, 5))
        ctk.CTkButton(f_btns_dil, text="Limpar Campos", command=self.dil_limpar_campos, fg_color="gray", width=90).pack(side="left")

        self.f_lista_dil = ctk.CTkFrame(self.master)
        self.f_lista_dil.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        f_pesq = ctk.CTkFrame(self.f_lista_dil, fg_color="transparent")
        f_pesq.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkLabel(f_pesq, text="Buscar em:", font=("Arial", 12, "bold")).pack(side="left", padx=(0, 5))

        # Mapa: rótulo exibido -> coluna real no banco de dados.
        # "Todos os Campos" preserva o comportamento antigo (busca em várias colunas
        # ao mesmo tempo); as demais opções restringem a busca a uma única coluna,
        # evitando que um valor pesquisado "bata" por engano em SAJ/SEI/Diligência
        # ao mesmo tempo por serem parecidos entre si.
        self.map_campo_pesq_dil = {
            "Todos os Campos": None,
            "ID": "id",
            "SAJ": "saj",
            "SEI": "sei",
            "DILIGÊNCIA": "diligencia",
            "SECRETARIA": "secretaria",
        }
        self.cmb_campo_pesq_dil = ctk.CTkComboBox(
            f_pesq,
            values=list(self.map_campo_pesq_dil.keys()),
            width=150,
            state="readonly",
            command=lambda e: self.dil_listar()
        )
        self.cmb_campo_pesq_dil.set("Todos os Campos")
        self.cmb_campo_pesq_dil.pack(side="left", padx=(0, 10))

        self.e_pesq_dil = ctk.CTkEntry(f_pesq, placeholder_text="Digite o valor para buscar...", width=200)
        self.e_pesq_dil.pack(side="left", fill="x", expand=True, padx=5)
        
        self.debouncer_dil = Debouncer(self.e_pesq_dil)
        self.debouncer_dil.bind(self.dil_listar)

        # Contador de diligências ativas (reflete a listagem/filtro atual).
        # Ao passar o mouse por cima, mostra um pop-up com a quantidade por Secretaria.
        self.lbl_contador_dil = ctk.CTkLabel(
            f_pesq, text="📋 Diligências Ativas: 0",
            font=("Arial", 12, "bold"), fg_color="#1f538d", text_color="white",
            corner_radius=6, cursor="hand2"
        )
        self.lbl_contador_dil.pack(side="right", padx=(10, 0), ipadx=8, ipady=4)
        self.lbl_contador_dil.bind("<Enter>", self._mostrar_popup_contador)
        self.lbl_contador_dil.bind("<Leave>", self._esconder_popup_contador)

        # Botão com símbolo de ordenação: fica destacado (azul) quando existe uma
        # ordenação de coluna ativa (ex.: por Prazo) e, ao ser clicado, limpa
        # essa ordenação, voltando a lista para a ordem padrão por ID.
        # Renomeado de "Filtro" para "Ordenação" para não ser confundido com o
        # botão "Limpar Busca/Ano" abaixo, que limpa a busca/ano em vez disso.
        self.btn_filtro_ativo = ctk.CTkButton(
            f_pesq, text="🔽", width=50,
            fg_color="#454545", hover_color="#2b2b2b",
            command=self.dil_limpar_ordenacao
        )
        self.btn_filtro_ativo.pack(side="right", padx=(10, 0))

        ctk.CTkButton(
            f_pesq, text="🧹 Limpar", width=100,
            fg_color="#454545", hover_color="#2b2b2b",
            command=self.dil_limpar_busca
        ).pack(side="right", padx=(10, 0))

        # Label discreto de "carregando", mostrado apenas enquanto a consulta
        # ao banco (em thread separada) ainda não retornou.
        self.lbl_carregando_dil = ctk.CTkLabel(f_pesq, text="", text_color="gray", font=("Arial", 11))
        self.lbl_carregando_dil.pack(side="left", padx=(5, 0))

        # COLUNAS DO TREEVIEW
        cols = ("id", "saj", "sei", "diligencia", "categoria", "secretaria", "caminho", "id_sei", "envio", "prazo", "status", "enviado_por")

        # Container que agrupa a tabela e a barra de rolagem vertical. Precisa
        # ser criado ANTES do Treeview e ser o pai real dele (não apenas o
        # destino do pack via "in_"), senão o gerenciador de geometria não
        # calcula corretamente o espaço da tabela e ela some da tela.
        f_tree_dil = ctk.CTkFrame(self.f_lista_dil, fg_color="transparent")
        f_tree_dil.pack(pady=10, padx=10, fill="both", expand=True)

        self.tab_dil_tree = ttk.Treeview(f_tree_dil, columns=cols, show="headings", selectmode="browse")

        headers = ("ID", "SAJ", "SEI", "DILIGÊNCIA", "CATEGORIA", "SECRETARIA", "CAMINHO", "ID SEI", "ENVIO", "PRAZO", "STATUS", "ENVIADO POR")
        self._col_headers = dict(zip(cols, headers))

        # Associação de evento de ordenação ao clicar no título de cada coluna
        for c, h in zip(cols, headers):
            self.tab_dil_tree.heading(
                c, 
                text=h, 
                command=lambda _col=c: self._ordenar_coluna(_col, False)
            )

        self.tab_dil_tree.column("id", width=40, anchor="center")
        self.tab_dil_tree.column("saj", width=110)
        self.tab_dil_tree.column("sei", width=110)
        self.tab_dil_tree.column("diligencia", width=80)
        self.tab_dil_tree.column("categoria", width=110)
        self.tab_dil_tree.column("secretaria", width=110)
        self.tab_dil_tree.column("caminho", width=110)
        self.tab_dil_tree.column("id_sei", width=90, anchor="center")
        self.tab_dil_tree.column("envio", width=75, anchor="center")
        self.tab_dil_tree.column("prazo", width=75, anchor="center")
        self.tab_dil_tree.column("status", width=100)
        self.tab_dil_tree.column("enviado_por", width=90)

        self.tab_dil_tree.tag_configure("vencido", foreground="#ff4d4d")
        # Diligências com 0 a 3 dias restantes até o prazo: destaque intermediário
        # (laranja/amarelo) para chamar atenção antes que virem "Em atraso".
        self.tab_dil_tree.tag_configure("quase_vencendo", foreground="#ffb300")

        scroll_dil = ttk.Scrollbar(f_tree_dil, orient="vertical", command=self.tab_dil_tree.yview)
        self.tab_dil_tree.configure(yscrollcommand=scroll_dil.set)

        # A barra precisa ser empacotada ANTES da tabela: como a tabela usa
        # expand=True, se ela for empacotada primeiro consome todo o espaço
        # do container e não sobra nada (largura zero) para a barra à direita.
        scroll_dil.pack(side="right", fill="y")
        self.tab_dil_tree.pack(side="left", fill="both", expand=True)

        self.tab_dil_tree.bind("<<TreeviewSelect>>", self.dil_carregar_campos)
        self.tab_dil_tree.bind("<Double-1>", self.dil_carregar_campos)

        f_act = ctk.CTkFrame(self.f_lista_dil, fg_color="transparent")
        f_act.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_act, text="✔ Concluir", command=self.dil_concluir, fg_color="#1f538d", hover_color="#14375e", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="Ver Concluídas", command=self.dil_abrir_concluidas, fg_color="#454545", hover_color="#2b2b2b", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="📄 Gerar PDF", command=lambda: exportar_treeview_pdf(self.tab_dil_tree, "Relatório de Diligências"), fg_color="#2e7d32", hover_color="#1b5e20", width=110).pack(side="left", padx=5)
        ctk.CTkButton(f_act, text="✖ Excluir (Del)", command=self.dil_excluir, fg_color="red", hover_color="darkred", width=110).pack(side="right", padx=5)

        # Atalhos de teclado: Delete exclui o item selecionado quando o foco
        # está na tabela (não interfere na edição de texto nos campos).
        self.tab_dil_tree.bind("<Delete>", lambda e: self.dil_excluir())

        # Enter em qualquer campo do formulário salva (se nada selecionado)
        # ou atualiza (se uma linha estiver selecionada); Esc limpa os campos.
        # Reduz a dependência do mouse para quem cadastra várias diligências
        # em sequência.
        for campo in (
            self.e_saj_dil, self.e_sei_dil, self.e_num_dil, self.e_sec_dil,
            self.e_caminho_dil, self.e_idsei_dil, self.e_envio_dil, self.e_prazo_dil,
        ):
            campo.bind("<Return>", self._acionar_atalho_enter)
            campo.bind("<Escape>", lambda e: self.dil_limpar_campos())

        self.dil_listar()

    def _acionar_atalho_enter(self, event=None):
        """
        Atalho de teclado (Enter): salva uma diligência nova se nenhuma
        estiver selecionada, ou atualiza a selecionada, sem precisar clicar
        no botão correspondente.
        """
        if self.id_diligencia_sel:
            self.dil_atualizar()
        else:
            self.dil_cadastrar()

    def dil_limpar_busca(self):
        """
        Limpa apenas os filtros de busca (texto + campo pesquisado),
        sem mexer na ordenação de coluna — que tem seu próprio botão
        ("🔽 Ordenação"), para não confundir os dois tipos de "limpeza".
        """
        self.e_pesq_dil.delete(0, 'end')
        self.cmb_campo_pesq_dil.set("Todos os Campos")
        self.dil_listar()

    def _mostrar_toast(self, mensagem, cor="#2e7d32"):
        """
        Feedback leve e não-modal (some sozinho), usado nas ações mais
        repetitivas (salvar/atualizar) para não travar a tela com um
        messagebox a cada clique. Ações destrutivas (excluir) continuam
        usando confirmação modal, propositalmente.
        """
        if self._toast_label is None:
            self._toast_label = ctk.CTkLabel(
                self.f_toast_dil, text="", corner_radius=8,
                fg_color=cor, text_color="white", font=("Arial", 12, "bold"),
            )

        if self._toast_after_id is not None:
            self.master.after_cancel(self._toast_after_id)

        self._toast_label.configure(text=mensagem, fg_color=cor)
        self._toast_label.place(relx=0.5, rely=0.5, anchor="center")
        self._toast_label.lift()
        self._toast_after_id = self.master.after(2200, self._toast_label.place_forget)

    def aplicar_mascara_campo(self, widget, funcao_mascara):
        val = widget.get()
        if val.strip():
            val_lower = val.strip().lower()
            if any(k in val_lower for k in ['s/n', 'sn', 's/nº', 's/n°', 'sem numero', 'sem número']):
                widget.delete(0, 'end')
                widget.insert(0, 'S/Nº')
                return

            widget.delete(0, 'end')
            widget.insert(0, funcao_mascara(val))

    def dil_limpar_campos(self):
        self.id_diligencia_sel = None
        for e in (self.e_saj_dil, self.e_sei_dil, self.e_num_dil, self.e_sec_dil, self.e_caminho_dil, self.e_idsei_dil, self.e_envio_dil, self.e_prazo_dil):
            e.delete(0, 'end')
        self.cmb_categoria.set("Geral")
        self.btn_salvar_dil.configure(state="normal")

    def dil_cadastrar(self):
        self.aplicar_mascara_campo(self.e_saj_dil, formatar_saj)
        self.aplicar_mascara_campo(self.e_sei_dil, formatar_sei)
        self.aplicar_mascara_campo(self.e_num_dil, formatar_diligencia)

        saj = self.e_saj_dil.get().strip()
        sei = self.e_sei_dil.get().strip()
        dil = self.e_num_dil.get().strip()
        sec = self.e_sec_dil.get().strip()
        caminho = self.e_caminho_dil.get().strip()
        id_sei = self.e_idsei_dil.get().strip()
        categoria = self.cmb_categoria.get()

        if not saj: 
            messagebox.showwarning("Aviso", "O SAJ é obrigatório!")
            return

        if not dil:
            messagebox.showwarning("Aviso", "O campo Diligência é obrigatório!")
            return

        envio_fmt, e_err = formatar_e_validar_data(self.e_envio_dil.get())
        if e_err: 
            messagebox.showwarning("Data Envio", e_err)
            return
            
        prazo_fmt, p_err = formatar_e_validar_data(self.e_prazo_dil.get())
        if p_err: 
            messagebox.showwarning("Prazo", p_err)
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                sql = "INSERT INTO tb_gestao (saj, sei, diligencia, categoria, secretaria, caminho, `Id SEI`, data_envio, prazo, usuario_criacao, data_criacao) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
                cur.execute(sql, (saj, sei, dil, categoria, sec, caminho, id_sei, envio_fmt, prazo_fmt, getattr(self.app, 'usuario_atual', 'N/A')))
                con.commit()
                self._mostrar_toast("✔ Diligência cadastrada!")
                self.dil_limpar_campos()
                self.dil_listar()
                if hasattr(self.app, 'aba_cobranca'): 
                    self.app.aba_cobranca.cob_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao cadastrar: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao salvar a diligência:\n{err}")
            finally:
                cur.close()
                con.close()

    def dil_listar(self):
        for item in self.tab_dil_tree.get_children():
            self.tab_dil_tree.delete(item)

        busca = self.e_pesq_dil.get().strip()
        campo_rotulo = self.cmb_campo_pesq_dil.get()
        coluna_busca = self.map_campo_pesq_dil.get(campo_rotulo)

        # Numa base grande ou conexão lenta, a query pode demorar; sem esse
        # aviso o usuário pode achar que o clique não funcionou e insistir.
        self.lbl_carregando_dil.configure(text="🔄 Carregando...")

        def tarefa_banco():
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor(dictionary=True)
                    query = "SELECT * FROM tb_gestao WHERE 1=1"
                    params = []

                    if busca:
                        if coluna_busca:
                            # Coluna específica selecionada: busca restrita a ela,
                            # evitando ambiguidade entre SAJ/SEI/Diligência parecidos.
                            query += f" AND {coluna_busca} LIKE %s"
                            params.append(f"%{busca}%")
                        else:
                            # "Todos os Campos": mantém a busca ampla de antes.
                            query += " AND (saj LIKE %s OR sei LIKE %s OR diligencia LIKE %s OR secretaria LIKE %s OR categoria LIKE %s OR caminho LIKE %s)"
                            lk = f"%{busca}%"
                            params.extend([lk, lk, lk, lk, lk, lk])

                    cur.execute(query, tuple(params))
                    linhas = cur.fetchall()

                    self.app.after(0, lambda: self._preencher_tabela(linhas))
                except mysql.connector.Error as err:
                    logging.error(f"Erro na busca: {err}")
                finally:
                    cur.close()
                    con.close()

        threading.Thread(target=tarefa_banco, daemon=True).start()

    def _preencher_tabela(self, linhas):
        self._atualizar_contador_dil(linhas)
        for row in linhas:
            pz_str = row['prazo'].strftime('%Y-%m-%d') if hasattr(row['prazo'], 'strftime') else str(row['prazo'])
            env_str = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else (row['data_envio'] or "")
            ex_pz = row['prazo'].strftime('%d/%m/%Y') if hasattr(row['prazo'], 'strftime') else pz_str
            st, dias = calcular_status_diligencia(pz_str)

            if st == "Em atraso":
                tags_item = ("vencido",)
            elif isinstance(dias, int) and 0 <= dias <= 3:
                # Diligências a 0-3 dias do prazo: destaque intermediário para
                # priorizar visualmente antes que virem "Em atraso".
                tags_item = ("quase_vencendo",)
            else:
                tags_item = ()

            cat_str = row.get('categoria') or "Geral"
            sec_str = row.get('secretaria') or "-"
            cam_str = row.get('caminho') or "-"
            id_sei_str = row.get('Id SEI') or "-"

            self.tab_dil_tree.insert("", "end", values=(
                row['id'], 
                row['saj'] or "-", 
                row['sei'] or "-", 
                row['diligencia'] or "-",
                cat_str, 
                sec_str, 
                cam_str,
                id_sei_str,
                env_str, 
                ex_pz, 
                f"{st} ({dias}d)", 
                row.get('usuario_criacao') or "N/A"
            ), tags=tags_item)

        # Reaplica a ordenação de coluna que o usuário havia escolhido (ex.:
        # por Prazo), já que o SELECT do banco devolve as linhas por ID e,
        # sem isso, qualquer recarga (cadastro/atualização/exclusão/conclusão)
        # perderia a ordenação e voltaria para a ordem padrão.
        self._reaplicar_ordenacao()

        self.lbl_carregando_dil.configure(text="")

    def dil_carregar_campos(self, event=None):
        sel = self.tab_dil_tree.selection()
        if not sel:
            return

        v = self.tab_dil_tree.item(sel[0], "values")
        if not v:
            return

        self.id_diligencia_sel = v[0]
            
        self.e_saj_dil.delete(0, 'end')
        self.e_saj_dil.insert(0, v[1] if v[1] != "-" else "")

        self.e_sei_dil.delete(0, 'end')
        self.e_sei_dil.insert(0, v[2] if v[2] != "-" else "")

        self.e_num_dil.delete(0, 'end')
        self.e_num_dil.insert(0, v[3] if v[3] != "-" else "")

        self.cmb_categoria.set(v[4] if (v[4] and v[4] != "-") else "Geral")

        self.e_sec_dil.delete(0, 'end')
        self.e_sec_dil.insert(0, v[5] if v[5] != "-" else "")

        self.e_caminho_dil.delete(0, 'end')
        self.e_caminho_dil.insert(0, v[6] if v[6] != "-" else "")

        self.e_idsei_dil.delete(0, 'end')
        self.e_idsei_dil.insert(0, v[7] if v[7] != "-" else "")

        self.e_envio_dil.delete(0, 'end')
        self.e_envio_dil.insert(0, v[8] if v[8] != "-" else "")

        self.e_prazo_dil.delete(0, 'end')
        self.e_prazo_dil.insert(0, v[9] if v[9] != "-" else "")

    def dil_atualizar(self):
        if not self.id_diligencia_sel: 
            messagebox.showwarning("Aviso", "Selecione uma diligência na tabela para atualizar.")
            return

        self.aplicar_mascara_campo(self.e_saj_dil, formatar_saj)
        self.aplicar_mascara_campo(self.e_sei_dil, formatar_sei)
        self.aplicar_mascara_campo(self.e_num_dil, formatar_diligencia)

        saj = self.e_saj_dil.get().strip()
        sei = self.e_sei_dil.get().strip()
        dil = self.e_num_dil.get().strip()
        sec = self.e_sec_dil.get().strip()
        caminho = self.e_caminho_dil.get().strip()
        id_sei = self.e_idsei_dil.get().strip()
        categoria = self.cmb_categoria.get()

        if not saj:
            messagebox.showwarning("Aviso", "O SAJ é obrigatório!")
            return

        if not dil:
            messagebox.showwarning("Aviso", "O campo Diligência é obrigatório!")
            return

        envio_fmt, e_err = formatar_e_validar_data(self.e_envio_dil.get())
        if e_err: 
            messagebox.showwarning("Data Envio", e_err)
            return

        prazo_fmt, p_err = formatar_e_validar_data(self.e_prazo_dil.get())
        if p_err: 
            messagebox.showwarning("Prazo", p_err)
            return

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor()
                sql = "UPDATE tb_gestao SET saj=%s, sei=%s, diligencia=%s, categoria=%s, secretaria=%s, caminho=%s, `Id SEI`=%s, data_envio=%s, prazo=%s WHERE id=%s"
                cur.execute(sql, (saj, sei, dil, categoria, sec, caminho, id_sei, envio_fmt, prazo_fmt, self.id_diligencia_sel))
                con.commit()
                self._mostrar_toast("✔ Diligência atualizada!")
                self.dil_limpar_campos()
                self.dil_listar()
                if hasattr(self.app, 'aba_cobranca'): 
                    self.app.aba_cobranca.cob_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao atualizar: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao atualizar a diligência:\n{err}")
            finally: 
                cur.close()
                con.close()

    def dil_excluir(self):
        sel = self.tab_dil_tree.selection()
        if not sel: 
            messagebox.showwarning("Aviso", "Selecione um item para excluir.")
            return

        item_id = self.tab_dil_tree.item(sel[0], "values")[0]
        if messagebox.askyesno("Confirmar", "Deseja excluir a diligência selecionada?"):
            con = obter_conexao()
            if con:
                try:
                    cur = con.cursor()
                    cur.execute("DELETE FROM tb_gestao WHERE id=%s", (item_id,))
                    con.commit()
                    self.dil_limpar_campos()
                    self.dil_listar()
                    if hasattr(self.app, 'aba_cobranca'): 
                        self.app.aba_cobranca.cob_listar()
                except mysql.connector.Error as err:
                    logging.error(f"Erro ao excluir: {err}")
                    messagebox.showerror("Erro no Banco", f"Falha ao excluir item:\n{err}")
                finally: 
                    cur.close()
                    con.close()

    def dil_concluir(self):
        sel = self.tab_dil_tree.selection()
        if not sel: 
            messagebox.showwarning("Aviso", "Selecione um item.")
            return

        item_id = self.tab_dil_tree.item(sel[0], "values")[0]
        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_gestao WHERE id=%s", (item_id,))
                reg = cur.fetchone()
                if messagebox.askyesno("Concluir", "Marcar como CONCLUÍDA e mover para o histórico?"):
                    sql = """
                        INSERT INTO tb_concluidas (id, saj, sei, diligencia, secretaria, categoria, caminho, `Id SEI`, data_envio, prazo, usuario_criacao, data_criacao, usuario_conclusao, data_conclusao)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """
                    cur.execute(sql, (
                        reg['id'], reg['saj'], reg['sei'], reg['diligencia'], reg['secretaria'], reg.get('categoria', ''), reg.get('caminho', ''),
                        reg.get('Id SEI', ''),
                        reg['data_envio'], reg['prazo'], reg['usuario_criacao'], reg['data_criacao'], getattr(self.app, 'usuario_atual', 'N/A')
                    ))
                    cur.execute("DELETE FROM tb_gestao WHERE id=%s", (item_id,))
                    con.commit()
                    self.dil_limpar_campos()
                    self.dil_listar()
                    if hasattr(self.app, 'aba_cobranca'): 
                        self.app.aba_cobranca.cob_listar()
            except mysql.connector.Error as err:
                logging.error(f"Erro ao concluir: {err}")
                messagebox.showerror("Erro no Banco", f"Falha ao concluir a diligência:\n{err}")
            finally: 
                cur.close()
                con.close()

    def dil_abrir_concluidas(self):
        janela = ctk.CTkToplevel(self.master)
        janela.title("Diligências Concluídas")
        janela.geometry("1100x720")
        janela.transient(self.master)

        ctk.CTkLabel(janela, text="Histórico de Concluídas (Diligência)", font=("Arial", 16, "bold")).pack(pady=10)
        
        cols = ("id", "saj", "sei", "sec", "categoria", "caminho", "id_sei", "envio", "prazo", "enviado_por", "concluido_por", "conclusao")
        headers = ("ID", "SAJ", "SEI", "Secretaria", "Categoria", "Caminho", "ID SEI", "Envio", "Prazo", "Enviado por", "Concluído por", "Data Conclusão")
        
        tb_conc = ttk.Treeview(janela, columns=cols, show="headings")

        for col, t in zip(cols, headers):
            tb_conc.heading(col, text=t)

        tb_conc.column("id", width=40, anchor="center")
        tb_conc.column("saj", width=120)
        tb_conc.column("sei", width=120)
        tb_conc.column("sec", width=110)
        tb_conc.column("categoria", width=110)
        tb_conc.column("caminho", width=110)
        tb_conc.column("id_sei", width=90, anchor="center")
        tb_conc.column("envio", width=85, anchor="center")
        tb_conc.column("prazo", width=85, anchor="center")
        tb_conc.column("enviado_por", width=110)
        tb_conc.column("concluido_por", width=110)
        tb_conc.column("conclusao", width=130, anchor="center")

        tb_conc.pack(fill="both", expand=True, padx=10, pady=5)

        con = obter_conexao()
        if con:
            try:
                cur = con.cursor(dictionary=True)
                cur.execute("SELECT * FROM tb_concluidas")
                for row in cur.fetchall():
                    env = row['data_envio'].strftime('%d/%m/%Y') if hasattr(row['data_envio'], 'strftime') else ""
                    pz = row['prazo'].strftime('%d/%m/%Y') if hasattr(row['prazo'], 'strftime') else ""
                    conc = row['data_conclusao'].strftime('%d/%m/%Y %H:%M') if hasattr(row['data_conclusao'], 'strftime') else ""
                    tb_conc.insert("", "end", values=(
                        row['id'], row['saj'], row['sei'], row['secretaria'], row.get('categoria', ''), row.get('caminho', '-'), row.get('Id SEI') or "-", env, pz, 
                        row['usuario_criacao'] or "N/A", row['usuario_conclusao'] or "N/A", conc
                    ))
            except mysql.connector.Error as err:
                logging.error(f"Erro ao consultar concluídas: {err}")
            finally: 
                cur.close()
                con.close()

        if hasattr(self.app, 'ajustar_colunas_treeview'):
            self.app.ajustar_colunas_treeview(tb_conc)

        def restaurar():
            sel = tb_conc.selection()
            if not sel: 
                return
            item_id = tb_conc.item(sel[0], "values")[0]
            if messagebox.askyesno("Restaurar", "Restaurar para a fila ativa?", parent=janela):
                c = obter_conexao()
                if c:
                    try:
                        cr = c.cursor(dictionary=True)
                        cr.execute("SELECT * FROM tb_concluidas WHERE id=%s", (item_id,))
                        reg = cr.fetchone()
                        
                        cr.execute("INSERT INTO tb_gestao (id, saj, sei, diligencia, secretaria, categoria, caminho, `Id SEI`, data_envio, prazo, usuario_criacao, data_criacao) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                   (reg['id'], reg['saj'], reg['sei'], reg['diligencia'], reg['secretaria'], reg.get('categoria', ''), reg.get('caminho', ''), reg.get('Id SEI', ''), reg['data_envio'], reg['prazo'], reg['usuario_criacao'], reg['data_criacao']))
                        cr.execute("DELETE FROM tb_concluidas WHERE id=%s", (item_id,))
                        c.commit()
                        janela.destroy()
                        self.dil_listar()
                        if hasattr(self.app, 'aba_cobranca'): 
                            self.app.aba_cobranca.cob_listar()
                    except mysql.connector.Error as err:
                        logging.error(f"Erro ao restaurar: {err}")
                        messagebox.showerror("Erro no Banco", f"Falha ao restaurar:\n{err}")
                    finally: 
                        cr.close()
                        c.close()

        if getattr(self.app, 'perfil_atual', '') == 'admin':
            ctk.CTkButton(janela, text="↩ Restaurar Selecionada (Admin)", command=restaurar, fg_color="#b37400", hover_color="orange").pack(pady=10)
        else:
            ctk.CTkLabel(janela, text="* Apenas usuários administradores podem restaurar processos concluídos.", text_color="gray").pack(pady=10)