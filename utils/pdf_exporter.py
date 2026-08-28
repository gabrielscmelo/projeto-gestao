import logging
from datetime import datetime
from tkinter import messagebox, filedialog

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    REPORTLAB_DISPONIVEL = True
except ImportError:
    REPORTLAB_DISPONIVEL = False

def exportar_treeview_pdf(tree, titulo_relatorio):
    if not REPORTLAB_DISPONIVEL:
        messagebox.showerror("Erro", "Biblioteca 'reportlab' não instalada.\nInstale com: pip install reportlab")
        return

    caminho = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("Arquivos PDF", "*.pdf")], title="Salvar PDF")
    if not caminho: return

    try:
        # Modo paisagem para caber mais colunas sem estourar a página
        margem = 15
        doc = SimpleDocTemplate(
            caminho, pagesize=landscape(A4),
            rightMargin=margem, leftMargin=margem, topMargin=margem, bottomMargin=margem
        )
        elementos = []
        styles = getSampleStyleSheet()

        elementos.append(Paragraph(f"<b>{titulo_relatorio}</b>", styles['Title']))
        elementos.append(Paragraph(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
        elementos.append(Spacer(1, 15))

        colunas = tree["columns"]
        cabecalho = [tree.heading(col)["text"] for col in colunas]

        # Estilo de célula com quebra automática de linha, para o texto nunca
        # ultrapassar a largura da coluna/página
        estilo_celula = ParagraphStyle(
            'Celula', parent=styles['Normal'], fontSize=7, leading=9, wordWrap='CJK'
        )
        estilo_cabecalho = ParagraphStyle(
            'Cabecalho', parent=styles['Normal'], fontSize=8, leading=10,
            textColor=colors.whitesmoke, fontName='Helvetica-Bold'
        )

        dados = [[Paragraph(str(h), estilo_cabecalho) for h in cabecalho]]
        for item in tree.get_children():
            valores = tree.item(item)["values"]
            linha = [Paragraph(str(v) if v not in (None, "") else "-", estilo_celula) for v in valores]
            dados.append(linha)

        # Largura útil da página (paisagem A4 - margens)
        largura_util = landscape(A4)[0] - (2 * margem)
        num_colunas = len(colunas)
        largura_coluna = largura_util / num_colunas
        larguras = [largura_coluna] * num_colunas

        tabela = Table(dados, colWidths=larguras, repeatRows=1)
        tabela.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1f538d")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#f2f2f2"), colors.white]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
        ]))

        elementos.append(tabela)
        doc.build(elementos)
        messagebox.showinfo("Sucesso", f"Relatório PDF gerado!\n{caminho}")
    except Exception as e:
        logging.error(f"Erro ao gerar PDF: {e}")
        messagebox.showerror("Erro ao Gerar PDF", str(e))