"""
Gera os 3 ofícios de usucapião (SUPAT, URB, STOP) no layout e com os
destinatários/corpos reais usados pela PUMA/PGM Recife.

Requer: reportlab  (pip install reportlab --break-system-packages)

Opcional: se existirem, usa as imagens abaixo (senão desenha só o texto):
  assets/brasao_recife.png
  assets/assinatura_iranildo.png
"""
import os
import re
from pathlib import Path
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

CAMINHO_BRASAO = os.path.join("assets", "brasao_recife.png")
CAMINHO_ASSINATURA = os.path.join("assets", "assinatura_iranildo.png")
PASTA_SAIDA = str(Path.home() / "Downloads" / "Oficios_Usucapiao")

SIGNATARIO_NOME = "Iranildo Severiano Mendes"
SIGNATARIO_CARGO = "Núcleo de Urbanismo e Meio Ambiente"
URL_QRCODE = "https://pgm.recife.pe.gov.br"

_EXTENSO = {1: "um", 2: "dois", 3: "três", 4: "quatro", 5: "cinco", 10: "dez", 15: "quinze", 30: "trinta"}

# Ordem SUPAT, URB, STOP == ordem real de numeração (ex.: 1183, 1184, 1185)
TEMPLATES = {
    "SUPAT": {
        "destinatario": {"nome": "André Torres Lapa Santos", "cargo": "Superintendente de Patrimônio", "sigla": "SEPLAGTD/SEPAT/SUPAT"},
        "trato": "Ilmo. Sr.",
        "heading": None,
        "saudacao": "Prezado Senhor,",
        "paragrafos": [
            "Cumprimentando-o cordialmente, vimos por meio deste, solicitar que nos informe se o imóvel localizado na **{endereco}**, consta na lista de patrimônio do município do Recife.",
            "Segue, em anexo, cópia da documentação pertinente.",
            "Solicitamos, ainda que, as informações acima requeridas nos sejam enviadas em **até {prazo_dias:02d} ({prazo_extenso}) dias.**",
            "Ao ensejo, renovamos votos de estima e consideração.",
        ],
    },
    "URB": {
        "destinatario": {"nome": "Flávia Rosa de Lima Santos", "cargo": "Gerente de Desapropriação", "sigla": "URB – RECIFE"},
        "trato": "Ilma. Sra.",
        "heading": None,
        "saudacao": "Prezada Senhora,",
        "paragrafos": [
            "Cumprimentando-a cordialmente, vimos por meio deste, solicitar informações acerca do imóvel localizado na **{endereco}**. Se consta na lista de patrimônio do Município do Recife.",
            "a) O imóvel foi desapropriado?",
            "b) Nesse caso, se o imóvel já integrar o patrimônio público, qual é a situação?",
            "c) Há interesse do município na área ocupada pelo imóvel? Qual seria este interesse?",
            "Segue, em anexo, cópia da documentação pertinente.",
            "Solicitamos ainda, que as informações acima requeridas nos sejam enviadas em **até {prazo_dias:02d} ({prazo_extenso}) dias.**",
            "Ao ensejo, renovamos votos de estima e consideração.",
        ],
    },
    "STOP": {
        "destinatario": {"nome": "Sandre Alex dos Santos", "cargo": "Chefe do Setor de Topografia", "sigla": "SEPUL/SELIC/STOP"},
        "trato": "Ilmo. Sr.",
        "heading": "STOP - NÚCLEO DE TOPOGRAFIA",
        "saudacao": None,
        "paragrafos": [
            "Cumprimentando-os cordialmente, solicitamos que nos informe acerca dos questionamentos abaixo, na mesma ordem em que aparecem, acerca de imóvel localizado na **{endereco}**.",
            "a) Se há loteamento / arruamento para a área objeto da ação de usucapião cuja petição inicial segue anexa;",
            "b) Se o imóvel em questão se encontra em área pública;",
            "c) Caso o imóvel se encontre em área pública, indique qual o nome, se houver, da área (rua, praça, etc.) pública, juntando o documento que comprove se tratar de área pública (planta de loteamento) e o croqui do corte a ser feito, na hipótese ser parcial a invasão;",
            "d) Caso não se trate de área pública, que se informe se o imóvel, total ou parcialmente, respeita o alinhamento de fato dominante. Para os imóveis que não respeitem o alinhamento, deve ser enviado croqui e foto colorida da situação encontrada;",
            "e) Se a área onde está situado o imóvel é uma ZEIS, se possui plano urbanístico (anexar o plano, se for o caso) e se a área está sendo objeto de procedimento de regularização fundiária de interesse social (REURB-S) por parte do Município do Recife;",
            "f) Se a ZEIS não possuir plano urbanístico, que se indique se o imóvel atende ao alinhamento dominante.",
            "Segue, em anexo, cópia da documentação pertinente.",
            "Remeter as informações em **até {prazo_dias:02d} ({prazo_extenso}) dias.**",
            "Ao ensejo, renovamos votos de estima e consideração.",
        ],
    },
}


def _mes_pt(mes_num):
    meses_en = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
    meses_pt = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return meses_en[mes_num - 1], meses_pt[mes_num - 1]


_PONTUACAO_SEM_ESPACO = {",", ".", ";", ":", ")"}


def _parse_runs(texto):
    """Divide o texto em trechos (texto, negrito) usando **marcador** como delimitador."""
    partes = texto.split("**")
    return [(p, i % 2 == 1) for i, p in enumerate(partes) if p]


def _tokenizar(runs):
    """Converte os trechos em tokens (palavra, negrito, sem_espaco_antes)."""
    tokens = []
    for texto, negrito in runs:
        for palavra in texto.split():
            tokens.append((palavra, negrito, palavra in _PONTUACAO_SEM_ESPACO))
    return tokens


def _desenhar_linha(c, tokens, x, y):
    cursor = x
    espaco = c.stringWidth(" ", "Helvetica", 10)
    for i, (palavra, negrito, sem_espaco) in enumerate(tokens):
        fonte = "Helvetica-Bold" if negrito else "Helvetica"
        c.setFont(fonte, 10)
        if i > 0 and not sem_espaco:
            cursor += espaco
        c.drawString(cursor, y, palavra)
        cursor += c.stringWidth(palavra, fonte, 10)


def _wrap(c, runs, x, y, largura_max, altura_linha, recuo_primeira=0):
    tokens = _tokenizar(runs)
    espaco = c.stringWidth(" ", "Helvetica", 10)
    linha, largura_linha, recuo = [], 0, recuo_primeira
    for palavra, negrito, sem_espaco in tokens:
        fonte = "Helvetica-Bold" if negrito else "Helvetica"
        largura_p = c.stringWidth(palavra, fonte, 10)
        extra = 0 if (not linha or sem_espaco) else espaco
        if largura_linha + extra + largura_p > (largura_max - recuo) and linha:
            _desenhar_linha(c, linha, x + recuo, y); y -= altura_linha
            linha, largura_linha, recuo = [], 0, 0
            extra = 0
        linha.append((palavra, negrito, sem_espaco))
        largura_linha += extra + largura_p
    if linha:
        _desenhar_linha(c, linha, x + recuo, y); y -= altura_linha
    return y


def _desenhar_qrcode(c, url, x, y, tamanho):
    widget = QrCodeWidget(url)
    b = widget.getBounds()
    w, h = b[2] - b[0], b[3] - b[1]
    d = Drawing(tamanho, tamanho, transform=[tamanho / w, 0, 0, tamanho / h, 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def _desenhar_oficio(c, numero_oficio, processo, interessado, endereco, prazo_dias, tpl):
    largura, altura = A4
    x, y = 2.5 * cm, altura - 2 * cm
    dest = tpl["destinatario"]

    if os.path.exists(CAMINHO_BRASAO):
        larg_img = 4 * cm
        alt_img = larg_img / 1.4716  # proporção real da imagem (518x352)
        c.drawImage(CAMINHO_BRASAO, largura / 2 - larg_img / 2, y - alt_img, width=larg_img, height=alt_img,
                    preserveAspectRatio=True, mask='auto')
        y -= (alt_img + 0.15 * cm)
    else:
        c.setFont("Helvetica-Bold", 12); c.drawCentredString(largura / 2, y, "MUNICÍPIO DO RECIFE"); y -= 0.4 * cm
        c.setFont("Helvetica-Bold", 9); c.drawCentredString(largura / 2, y, "PROCURADORIA-GERAL"); y -= 0.5 * cm
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(largura / 2, y, "Procuradoria de Urbanismo e Meio Ambiente - PUMA"); y -= 0.7 * cm

    c.setFont("Helvetica-Bold", 10)
    for linha in (f"OFÍCIO Nº {numero_oficio}", f"PROCESSO: {processo}", f"INTERESSADO: {interessado.upper()}",
                  "ORGÃO DE ORIGEM: PROCURADORIA DE URBANISMO E MEIO AMBIENTE - PUMA"):
        c.drawString(x, y, linha); y -= 0.53 * cm

    y -= 0.3 * cm
    en, pt = _mes_pt(datetime.now().month)
    c.setFont("Helvetica", 10)
    c.drawRightString(largura - 2.5 * cm, y, datetime.now().strftime("%d de %B de %Y").replace(en, pt))
    y -= 0.7 * cm

    if tpl["heading"]:
        c.setFont("Helvetica-Bold", 10); c.drawString(x, y, tpl["heading"]); y -= 0.5 * cm
    if tpl["saudacao"]:
        c.setFont("Helvetica", 10); c.drawString(x, y, tpl["saudacao"]); y -= 0.5 * cm

    c.setFont("Helvetica", 10)
    prazo_extenso = _EXTENSO.get(prazo_dias, str(prazo_dias))
    for i, p in enumerate(tpl["paragrafos"]):
        texto = p.format(endereco=endereco, prazo_dias=prazo_dias, prazo_extenso=prazo_extenso)
        recuo = 1 * cm if i == 0 else 0
        y = _wrap(c, _parse_runs(texto), x, y, largura - 4.5 * cm, 0.53 * cm, recuo_primeira=recuo)
        item_lista = bool(re.match(r'^[a-z]\)', p))
        y -= (0.15 * cm if item_lista else 0.53 * cm)

    y -= 0.4 * cm
    if os.path.exists(CAMINHO_ASSINATURA):
        larg_img = 6.7 * cm
        alt_img = larg_img / 2.8193  # proporção real da imagem (671x238)
        c.drawImage(CAMINHO_ASSINATURA, largura / 2 - larg_img / 2, y - alt_img, width=larg_img, height=alt_img,
                    preserveAspectRatio=True, mask='auto')
        y -= (alt_img + 0.15 * cm)
    else:
        c.setFont("Helvetica", 10); c.drawCentredString(largura / 2, y, SIGNATARIO_NOME); y -= 0.4 * cm
        c.setFont("Helvetica", 8)
        c.drawCentredString(largura / 2, y, "PROCURADORIA GERAL DO MUNICÍPIO"); y -= 0.35 * cm
        c.drawCentredString(largura / 2, y, SIGNATARIO_CARGO); y -= 0.5 * cm
    c.setFont("Helvetica", 9)
    c.drawCentredString(largura / 2, y, "PROCURADORIA GERAL DO MUNICÍPIO"); y -= 0.4 * cm
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(largura / 2, y, "Procuradoria de Urbanismo e Meio Ambiente")

    y -= 0.45 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, f"{tpl['trato']} {dest['nome']}"); y -= 0.4 * cm
    c.drawString(x, y, dest['cargo']); y -= 0.4 * cm
    c.drawString(x, y, dest['sigla']); y -= 0.4 * cm
    c.drawString(x, y, "NESTA")
    c.line(x, y - 0.06 * cm, x + c.stringWidth("NESTA", "Helvetica-Bold", 10), y - 0.06 * cm)

    c.setFont("Helvetica", 7)
    c.drawCentredString(largura / 2, 1.55 * cm, "Site: pgm.recife.pe.gov.br | Telefone: (81) 3355.8138/3355.8461 | Email: pgm@recife.pe.gov.br")
    c.drawCentredString(largura / 2, 1.3 * cm, "Sede PGM: Av. Cais do Apolo, nº 925, 3º andar, Bairro do Recife, Recife/PE | CEP: 50030-903")
    c.drawCentredString(largura / 2, 1.05 * cm, "Sede PFM: Av. República do Líbano, 251, Edf. Rio-mar Trade Center, Torre C, Pina, Recife/PE")
    c.drawCentredString(largura / 2, 0.8 * cm, processo)
    _desenhar_qrcode(c, URL_QRCODE, largura - 2.5 * cm - 1.7 * cm, 0.65 * cm, 1.7 * cm)


def gerar_oficios_usucapiao(numero_oficio_base, processo, interessado, endereco, prazo_dias=5):
    """Gera 3 PDFs (SUPAT, URB, STOP) com os corpos reais de cada secretaria. Retorna os caminhos."""
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    base_num, ano = numero_oficio_base.split("/")
    caminhos = []
    for i, sigla in enumerate(("SUPAT", "URB", "STOP")):
        numero = f"{int(base_num) + i}/{ano}"
        nome_arquivo = f"Oficio_{sigla}_{processo.replace('/', '-').replace('.', '')}.pdf"
        caminho = os.path.join(PASTA_SAIDA, nome_arquivo)
        c = canvas.Canvas(caminho, pagesize=A4)
        _desenhar_oficio(c, numero, processo, interessado, endereco, prazo_dias, TEMPLATES[sigla])
        c.showPage(); c.save()
        caminhos.append(caminho)
    return caminhos