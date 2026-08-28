import os
import re
import time
import logging
import pyautogui
import pyperclip
import mysql.connector
from datetime import datetime
from playwright.sync_api import sync_playwright
from database.connection import obter_conexao

try:
    import pywinauto
    from pywinauto import Application
    from pywinauto.keyboard import send_keys as _pywinauto_send_keys
except ImportError:
    pywinauto = None
    _pywinauto_send_keys = None

NOME_TIPO_DOCUMENTO_COBRANCA = "Cobrança de Ofício - Solicitação"

# Configurações globais do PyAutoGUI
pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True

SEI_URL = "https://sip.recife.pe.gov.br/sip/login.php?sigla_orgao_sistema=PR&sigla_sistema=SEI"
ARQUIVO_SESSAO_SEI = os.path.abspath("sei_session.json")

# Pasta para screenshots de diagnóstico da etapa de impressão (janelas nativas do Windows/
# do próprio Chrome, fora do DOM — o Playwright não "enxerga" nem consegue clicar nelas
# diretamente, por isso a necessidade do pyautogui/pywinauto e desses screenshots de apoio).
PASTA_TEMP_PDF = os.path.abspath("temp_cobrancas")
os.makedirs(PASTA_TEMP_PDF, exist_ok=True)


def registrar_log(mensagem, callback_log=None):
    """Registra a mensagem no log do sistema e na interface visual."""
    logging.info(mensagem)
    print(mensagem)
    if callback_log:
        callback_log(mensagem)


def obter_diligencias_vencidas():
    """Consulta no MySQL as diligências com prazo vencido."""
    con = obter_conexao()
    if not con:
        return []
    try:
        cur = con.cursor(dictionary=True)
        try:
            query = """
                SELECT id, saj, sei, diligencia, categoria, secretaria, caminho 
                FROM tb_gestao 
                WHERE prazo < CURDATE()
            """
            cur.execute(query)
            return cur.fetchall()
        except mysql.connector.Error:
            query = """
                SELECT id, saj, sei, diligencia, categoria, secretaria 
                FROM tb_gestao 
                WHERE prazo < CURDATE()
            """
            cur.execute(query)
            dils = cur.fetchall()
            for d in dils:
                d['caminho'] = ""
            return dils
    finally:
        cur.close()
        con.close()


def obter_frame_visualizacao(page):
    """Retorna a referência atualizada do iframe principal do SEI."""
    time.sleep(0.3)
    frame = page.frame(name="ifrVisualizacao")
    return frame if frame else page


def aguardar_pesquisa_rapida(page):
    """Garante que a barra de pesquisa rápida do SEI está pronta para uso."""
    for _ in range(30):
        try:
            if page.locator("#txtPesquisaRapida").is_visible():
                return page.locator("#txtPesquisaRapida")
            for frame in page.frames:
                if frame.locator("#txtPesquisaRapida").is_visible():
                    return frame.locator("#txtPesquisaRapida")
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError("Não foi possível localizar o campo #txtPesquisaRapida após o login.")


def normalizar_num_diligencia(texto):
    """Remove zeros à esquerda e espaços para comparar ex: '0003/2026' com '03/2026'."""
    if not texto:
        return ""
    partes = texto.strip().split('/')
    if len(partes) == 2:
        num = re.sub(r'^0+', '', partes[0])
        return f"{num}/{partes[1]}"
    return texto.strip()


def extrair_id_diligencia_arvore(page, num_diligencia, callback_log=None):
    """
    Varre a árvore lateral do SEI (ifrArvore) de forma exaustiva para capturar o ID da diligência.
    """
    registrar_log(f"  [SEI] Buscando ID da Diligência '{num_diligencia}' na árvore...", callback_log)
    
    frame_arvore = None
    for _ in range(25):
        frame_arvore = page.frame(name="ifrArvore")
        if frame_arvore:
            try:
                texto_total = frame_arvore.evaluate("() => document.body ? document.body.innerText : ''")
                if texto_total and len(texto_total.strip()) > 0:
                    break
            except Exception:
                pass
        time.sleep(0.4)

    if not frame_arvore:
        frame_arvore = page

    try:
        frame_arvore.evaluate("""() => {
            const icones = document.querySelectorAll("img[src*='mais'], img[src*='pasta_fechada'], a[href*='expandir'], span.infraArvoreMais");
            icones.forEach(el => { try { el.click(); } catch(e) {} });
        }""")
        time.sleep(1.0)
    except Exception:
        pass

    num_normalizado = normalizar_num_diligencia(str(num_diligencia))
    partes = num_normalizado.split('/')
    digitos_num = re.sub(r'\D', '', partes[0]) if partes else ""
    num_base = str(int(digitos_num)) if digitos_num else ""
    ano_base = partes[1] if len(partes) > 1 else ""

    registrar_log(f"  [SEI] Alvo normalizado -> Número: '{num_base}', Ano: '{ano_base}'", callback_log)

    resultado_busca = frame_arvore.evaluate("""({ numBase, anoBase }) => {
        const todosElementos = Array.from(document.querySelectorAll('a, span, div, td'));
        
        for (const el of todosElementos) {
            const texto = el.textContent || '';
            const html = el.outerHTML || '';
            const lowerTexto = texto.toLowerCase();

            if (lowerTexto.includes('dilig')) {
                const matchNum = lowerTexto.match(/dilig[eê]ncia.*?0*(\\d+)(?:\\/(\\d{4}))?/);
                let bateu = false;
                
                if (matchNum) {
                    const nEl = matchNum[1];
                    const aEl = matchNum[2] || '';
                    if (nEl === numBase && (!anoBase || !aEl || aEl === anoBase)) {
                        bateu = true;
                    }
                } else if (lowerTexto.includes(numBase)) {
                    bateu = true;
                }

                if (bateu) {
                    const matchIdTexto = texto.match(/\\((\\d{6,10})\\)/);
                    if (matchIdTexto) {
                        return { id: matchIdTexto[1], metodo: 'texto', elemento: texto };
                    }

                    const matchIdUrl = html.match(/id_documento[='](\\d+)/i) || html.match(/(\\d{6,10})/);
                    if (matchIdUrl) {
                        return { id: matchIdUrl[1], metodo: 'url/html', elemento: texto };
                    }
                }
            }
        }

        const bodyText = document.body ? document.body.innerText : '';
        const linhas = bodyText.split('\\n');
        for (const linha of linhas) {
            if (linha.toLowerCase().includes('dilig') && linha.includes(numBase)) {
                const matchId = linha.match(/\\((\\d{6,10})\\)/);
                if (matchId) {
                    return { id: matchId[1], metodo: 'fallback_linha', elemento: linha };
                }
            }
        }

        return { id: '', metodo: 'nenhum', elemento: '' };
    }""", {"numBase": num_base, "anoBase": ano_base})

    if resultado_busca and resultado_busca.get('id'):
        id_encontrado = resultado_busca['id']
        registrar_log(f"  [SEI] ID '{id_encontrado}' capturado com sucesso via método '{resultado_busca.get('metodo')}' (Texto: {resultado_busca.get('elemento').strip()}).", callback_log)
        return id_encontrado

    try:
        html_frame = frame_arvore.content()
        padrao_html = rf"Dilig[eê]ncia[^\\(]*?0*{re.escape(num_base)}(?:/{re.escape(ano_base)})?[^\\(]*?\\((\\d{{6,10}})\\)"
        m_html = re.search(padrao_html, html_frame, re.IGNORECASE)
        if m_html:
            id_html = m_html.group(1)
            registrar_log(f"  [SEI] ID '{id_html}' capturado via regex no HTML bruto do frame.", callback_log)
            return id_html
    except Exception:
        pass

    registrar_log(f"  ⚠️ [SEI] Falha ao encontrar ID para a Diligência '{num_diligencia}' na árvore.", callback_log)
    return ""


def selecionar_ultimo_despacho_arvore(page, callback_log=None):
    """Localiza e clica no Despacho recém-criado na árvore lateral (ifrArvore)."""
    registrar_log("  [SEI] Selecionando o Despacho recém-elaborado na árvore lateral...", callback_log)
    page.bring_to_front()
    time.sleep(1.0)

    for tentativa in range(1, 11):
        try:
            frame_arvore = page.frame(name="ifrArvore")
            if frame_arvore:
                frame_arvore.evaluate("""() => {
                    const icones = document.querySelectorAll("img[src*='mais'], img[src*='pasta_fechada'], a[href*='expandir']");
                    icones.forEach(el => { try { el.click(); } catch(e) {} });
                }""")
                time.sleep(0.5)

                seletor_despacho = "a[href*='arvore_visualizar']:has-text('Despacho'), a:has-text('Despacho')"
                no_despacho = frame_arvore.locator(seletor_despacho).last

                if no_despacho.is_visible(timeout=3000):
                    no_despacho.click()

                    # IMPORTANTE: page.wait_for_load_state() monitora a página
                    # principal, mas o clique no despacho só navega o iframe
                    # 'ifrVisualizacao'. Esperar a página principal "idle" não
                    # garante que o conteúdo do despacho (e o ícone Imprimir)
                    # já renderizou dentro do iframe — por isso aguardamos o
                    # próprio iframe carregar, com uma folga extra.
                    try:
                        elemento_iframe = page.wait_for_selector(
                            "iframe[name='ifrVisualizacao']", timeout=10000
                        )
                        frame_visualizacao = elemento_iframe.content_frame()
                        if frame_visualizacao:
                            frame_visualizacao.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass

                    time.sleep(2.0)
                    registrar_log("  [SEI] Despacho selecionado com sucesso. Tela de impressão disponível.", callback_log)
                    return True
        except Exception:
            pass
        time.sleep(1.0)

    raise RuntimeError("Não foi possível selecionar o Despacho elaborado na árvore lateral.")


def _aguardar_janela_em_foco(titulo_regex=r".*(SAJ|Movimenta).*", timeout_seg=30, callback_log=None):
    """
    Aguarda uma janela do SAJ (ou de suas modais, ex.: 'Movimentações com
    Digitalização a Realizar') e tenta trazê-la para o foco.

    Importante: retorna True/False mas NUNCA lança exceção. A janela do SAJ é
    um app externo (fora do DOM do Playwright) e o pywinauto pode falhar em
    "enxergá-la" mesmo que ela já esteja visível na tela — por exemplo quando
    o SAJ roda elevado (Administrador) e o script Python não, o Windows
    (UIPI) bloqueia a automação de janelas de processos com privilégio mais
    alto, mesmo que pyautogui.screenshot() continue capturando a tela
    normalmente. Por isso o chamador NÃO deve tratar o retorno False como
    erro fatal — apenas como "não consegui confirmar/focar", e deve seguir
    tentando fechar o pop-up mesmo assim (ver imprimir_para_saj_virtual).
    """
    if not pywinauto:
        registrar_log(
            "  [SAJ Virtual][AVISO] pywinauto não instalado.",
            callback_log,
        )
        return False

    from pywinauto import Desktop

    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        # Tenta primeiro o backend win32 (mais tolerante com apps legados/
        # Delphi/VB e imune a boa parte das limitações do UIA) e, se não
        # achar nada, cai para uia.
        for backend in ("win32", "uia"):
            try:
                janelas = Desktop(backend=backend).windows(
                    title_re=titulo_regex, visible_only=True, enabled_only=False
                )
                if janelas:
                    janela = janelas[0]
                    try:
                        janela.set_focus()
                    except Exception:
                        # Mesmo sem set_focus (comum em janelas elevadas por
                        # causa do UIPI), a janela existe -> já é suficiente
                        # para seguirmos o fluxo.
                        pass
                    time.sleep(0.3)
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _digitar_letra_por_letra(texto, pausa=0.08, callback_log=None):
    """
    Digita o texto caractere por caractere disparando eventos de tecla reais
    (via pywinauto.keyboard.send_keys), em vez de colar via clipboard.

    Isso é necessário porque campos de autocomplete como 'Tipo de documento
    digital' no SAJ só filtram/atualizam a lista a cada tecla pressionada
    (KEYDOWN/KEYUP); um Ctrl+V insere o texto inteiro de uma vez e não
    dispara esses eventos, então a lista não é filtrada e o Enter acaba
    confirmando o item que já estava destacado (ex.: 'Citação' em vez de
    'Cobrança de Ofício - Solicitação').
    """
    if _pywinauto_send_keys:
        try:
            # Escapa caracteres especiais do "mini-idioma" do send_keys
            # (parênteses, chaves, e os modificadores + ^ % ~) para que
            # sejam digitados literalmente, não interpretados como comandos.
            especiais = "{}()+^%~"
            texto_escapado = "".join(f"{{{c}}}" if c in especiais else c for c in texto)
            _pywinauto_send_keys(texto_escapado, pause=pausa, with_spaces=True)
            return
        except Exception as e:
            registrar_log(
                f"  [SAJ Virtual][AVISO] Falha ao digitar via pywinauto ({e}); usando pyautogui como fallback.",
                callback_log,
            )

    pyautogui.write(texto, interval=pausa)



def _obter_janela(titulo_regex, timeout_seg=15):
    """
    Retorna o wrapper pywinauto da PRIMEIRA janela visível cujo título bata
    com titulo_regex (tentando win32 e depois uia), ou None se não achar.
    Diferente de _aguardar_janela_em_foco, devolve o objeto da janela para
    que o chamador possa localizar e clicar em botões/campos específicos.
    """
    if not pywinauto:
        return None
    from pywinauto import Desktop
    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        for backend in ("win32", "uia"):
            try:
                janelas = Desktop(backend=backend).windows(title_re=titulo_regex, visible_only=True)
                if janelas:
                    return janelas[0]
            except Exception:
                pass
        time.sleep(0.5)
    return None


def _listar_titulos_janelas():
    """Lista os títulos de todas as janelas visíveis no momento (best-effort)."""
    if not pywinauto:
        return set()
    from pywinauto import Desktop
    titulos = set()
    for backend in ("win32", "uia"):
        try:
            for w in Desktop(backend=backend).windows(visible_only=True):
                try:
                    t = w.window_text()
                    if t:
                        titulos.add(t)
                except Exception:
                    pass
        except Exception:
            pass
    return titulos


def _clicar_botao_janela(janela, textos_possiveis, callback_log=None):
    """
    Tenta clicar num botão/controle da janela cujo texto bata com algum de
    textos_possiveis (ex.: ['Fechar'], ['OK', 'Salvar', 'Confirmar']).

    Diferente da versão anterior (que só procurava control_type='Button'),
    esta varre TODOS os descendentes da janela e compara o texto — muitos
    apps legados (Delphi/VB6/etc.) não expõem os botões como o tipo
    'Button' esperado pelo UIA/win32, então a busca restrita a Button
    simplesmente não encontrava nada e caía sempre no fallback (ESC), que
    não tem o mesmo efeito de clicar no botão de verdade.

    Retorna True se conseguiu clicar em algum.
    """
    try:
        descendentes = janela.descendants()
    except Exception:
        descendentes = []

    textos_vistos = []
    for texto_alvo in textos_possiveis:
        alvo_lower = texto_alvo.strip().lower()

        # 1) match exato (ignorando espaços/caixa)
        for elem in descendentes:
            try:
                texto_elem = (elem.window_text() or "").strip()
            except Exception:
                continue
            if texto_elem and texto_elem not in textos_vistos:
                textos_vistos.append(texto_elem)
            if texto_elem.lower() == alvo_lower:
                if _tentar_clicar_elemento(elem):
                    return True

        # 2) match parcial (contém o texto alvo)
        for elem in descendentes:
            try:
                texto_elem = (elem.window_text() or "").strip()
            except Exception:
                continue
            if texto_elem and alvo_lower in texto_elem.lower():
                if _tentar_clicar_elemento(elem):
                    return True

        # 3) fallback: identificadores diretos do pywinauto (funcionava em
        # alguns apps padrão; mantido como tentativa extra)
        for kwargs in (
            {"title": texto_alvo, "control_type": "Button"},
            {"title_re": f".*{re.escape(texto_alvo)}.*", "control_type": "Button"},
            {"title": texto_alvo},
        ):
            try:
                botao = janela.child_window(**kwargs)
                if botao.exists(timeout=1):
                    if _tentar_clicar_elemento(botao):
                        return True
            except Exception:
                pass

    if callback_log and textos_vistos:
        registrar_log(
            f"  [SAJ Virtual][DEBUG] Controles encontrados na janela (nenhum bateu com {textos_possiveis}): "
            f"{textos_vistos[:20]}",
            callback_log,
        )
    return False


def _tentar_clicar_elemento(elem):
    """Clica num elemento pywinauto tentando alguns métodos, em ordem de preferência."""
    for metodo in ("click_input", "click"):
        try:
            getattr(elem, metodo)()
            return True
        except Exception:
            pass
    try:
        elem.set_focus()
        pyautogui.press('enter')
        return True
    except Exception:
        return False


def _dump_arvore_controles(janela, callback_log=None, arquivo_saida=None):
    """
    Diagnóstico: lista TODOS os descendentes de `janela`, mesmo os que não
    têm texto (window_text() vazio) — diferente de _clicar_botao_janela, que
    só reporta os que têm texto não-vazio.

    Para cada descendente, tenta reportar:
      - classe (friendly_class_name): ex. 'Button', 'TSpeedButton', 'Panel'
      - texto (window_text())
      - nome via UIA (element_info.name), que em alguns controles legados
        expõe um rótulo diferente/adicional ao window_text()
      - retângulo na tela (útil para depois mapear cliques por coordenada
        relativa à janela, caso o controle nunca apareça como elemento
        clicável de verdade)

    Isso serve para confirmar se os botões 'Fechar'/'Associar' existem como
    algum tipo de controle filho (mesmo sem texto capturado) ou se são
    puramente desenhados (owner-draw) dentro de um painel como
    'bfBarraCadastro', caso em que não há classe alguma disponível na
    posição esperada e a única saída é clique por coordenada ou OCR restrito
    ao retângulo do pop-up.

    Se `arquivo_saida` for informado, também grava a árvore completa nesse
    caminho (texto simples), pois a lista pode ser grande demais para o log
    da interface.
    """
    try:
        descendentes = janela.descendants()
    except Exception as e:
        registrar_log(f"  [DEBUG] Falha ao obter descendentes da janela: {e}", callback_log)
        return []

    try:
        rect_janela = janela.rectangle()
    except Exception:
        rect_janela = None

    linhas = [f"[DEBUG] Janela: {janela.window_text()!r} rect={rect_janela}"]
    linhas.append(f"[DEBUG] Total de descendentes: {len(descendentes)}")

    for i, elem in enumerate(descendentes):
        try:
            classe = elem.friendly_class_name()
        except Exception:
            classe = "?"
        try:
            texto = (elem.window_text() or "").strip()
        except Exception:
            texto = ""
        try:
            nome_uia = elem.element_info.name or ""
        except Exception:
            nome_uia = ""
        try:
            rect = elem.rectangle()
        except Exception:
            rect = None

        linhas.append(
            f"  [{i:03d}] classe={classe!r} texto={texto!r} nome_uia={nome_uia!r} rect={rect}"
        )

    texto_completo = "\n".join(linhas)

    # No callback/log da interface, manda um resumo para não poluir demais;
    # a árvore completa vai só para o arquivo (se informado) e para o log
    # padrão (logging.info, via registrar_log em cada linha relevante).
    registrar_log(
        f"  [SAJ Virtual][DEBUG] Árvore de controles despejada ({len(descendentes)} itens). "
        f"Ver log completo{' em ' + arquivo_saida if arquivo_saida else ''}.",
        callback_log,
    )
    for linha in linhas:
        logging.info(linha)
        print(linha)

    if arquivo_saida:
        try:
            with open(arquivo_saida, "w", encoding="utf-8") as f:
                f.write(texto_completo)
            registrar_log(f"  [SAJ Virtual][DEBUG] Árvore de controles salva em: {arquivo_saida}", callback_log)
        except Exception as e:
            registrar_log(f"  [SAJ Virtual][AVISO] Não consegui salvar a árvore de controles em arquivo: {e}", callback_log)

    return linhas


def _clicar_fechar_por_coordenada(janela, callback_log=None):
    """
    Fallback para quando o botão 'Fechar' não é enumerável via texto/
    acessibilidade — caso confirmado por diagnóstico (_dump_arvore_controles):
    o botão 'Fechar' é desenhado direto no canvas da barra 'bfBarraCadastro',
    sem controle próprio, então nenhuma busca por texto (exata ou parcial)
    o encontra.

    IMPORTANTE (corrigido após 1ª tentativa real ter clicado no vazio):
    o único subcontrole enumerável ('TspBotoesCadastro') NÃO é só o botão
    'Associar' — ele engloba o GRUPO 'Associar' + 'Fechar' inteiro. Medindo
    por pixel um screenshot real de falha, com o container reportado em
    rect (L369, T485, R480, B507):
      - texto 'Associar' ocupa aproximadamente x 378–419 (relativo ~8%–45%
        da largura do container)
      - texto 'Fechar' ocupa aproximadamente x 439–470 (relativo ~63%–91%
        da largura do container)
    Ou seja, 'Fechar' fica DENTRO do mesmo retângulo, na metade direita —
    não fora/à direita dele, como a versão anterior assumia (por isso o
    clique em x=535 caiu no vazio da barra, depois do fim do texto).

    Estratégia atual: clicar no centro da metade direita do próprio
    container do grupo de botões (~75% da largura, a partir da esquerda),
    que cai dentro da faixa medida do texto 'Fechar'.
    """
    try:
        descendentes = janela.descendants()
    except Exception as e:
        registrar_log(
            f"  [SAJ Virtual][AVISO] Não consegui listar descendentes para estimar a posição de 'Fechar': {e}",
            callback_log,
        )
        return False

    rect_barra = None
    rect_grupo_botoes = None
    for elem in descendentes:
        try:
            classe = elem.friendly_class_name()
            texto = (elem.window_text() or "").strip()
        except Exception:
            continue

        if classe == "TspToolBar" and texto == "bfBarraCadastro":
            try:
                rect_barra = elem.rectangle()
            except Exception:
                pass
        elif classe == "TspBotoesCadastro":
            try:
                r = elem.rectangle()
            except Exception:
                continue
            # Só nos interessa o TspBotoesCadastro alinhado com a barra de
            # baixo (existe outro perto da grade, no topo, que não tem nada
            # a ver com 'Associar'/'Fechar').
            if rect_barra is not None and abs(r.top - rect_barra.top) <= 5 and abs(r.bottom - rect_barra.bottom) <= 5:
                if rect_grupo_botoes is None or r.left < rect_grupo_botoes.left:
                    rect_grupo_botoes = r

    rect_ref = rect_grupo_botoes or rect_barra
    if not rect_ref:
        registrar_log(
            "  [SAJ Virtual][AVISO] Não localizei a barra 'bfBarraCadastro' nem o grupo de botões "
            "para estimar a posição de 'Fechar'.",
            callback_log,
        )
        return False

    largura = rect_ref.right - rect_ref.left
    # Centro da metade direita do container (onde 'Fechar' ficou, medido por
    # pixel): ~75% da largura a partir da borda esquerda.
    x_centro = rect_ref.left + round(0.75 * largura)
    y_centro = (rect_ref.top + rect_ref.bottom) // 2

    registrar_log(
        f"  [SAJ Virtual][AVISO] Botão 'Fechar' não é acessível via automação (confirmado por diagnóstico). "
        f"Clicando por coordenada estimada ({x_centro}, {y_centro}).",
        callback_log,
    )
    pyautogui.click(x_centro, y_centro)
    return True


def _fechar_modal_movimentacoes_e_nomear_documento(
    nome_documento="Cobrança de Ofício - Solicitação", callback_log=None
):
    """
    Fecha a modal 'Movimentações com Digitalização a Realizar' clicando de
    verdade no botão 'Fechar' (em vez de ESC, que não dispara a lógica da
    aplicação) e, como isso abre um novo pop-up pedindo o NOME do documento,
    preenche esse nome e confirma.

    Essa modal é OPCIONAL: só aparece quando existem movimentações
    pendentes de digitalização para aquele processo. Nem todo processo tem
    isso, então "a modal nunca apareceu" é um resultado normal do fluxo, não
    uma falha — bem diferente de "a modal apareceu e não conseguimos
    fechar" (aí sim é um problema real, porque a tela continua bloqueada e
    qualquer campo preenchido depois cairia no lugar errado).

    Retorno:
      - True  -> modal apareceu e foi fechada com sucesso.
      - False -> modal apareceu mas NÃO foi possível fechá-la (falha real;
                 o chamador deve abortar o processamento deste item).
      - None  -> modal nunca apareceu (nada para fechar; o chamador deve
                 seguir o fluxo normalmente, como se essa etapa não existisse).
    """
    janela_mov = _obter_janela(r".*Movimenta.*", timeout_seg=15)
    if not janela_mov:
        registrar_log(
            "  [SAJ Virtual] Modal 'Movimentações com Digitalização a Realizar' não apareceu "
            "(esse processo não tem movimentações pendentes de digitalização). Seguindo o fluxo normal.",
            callback_log,
        )
        return None

    titulos_antes = _listar_titulos_janelas()

    clicou = False
    for tentativa in range(1, 4):
        registrar_log(
            f"  [SAJ Virtual] Clicando no botão 'Fechar' da modal 'Movimentações' (tentativa {tentativa}/3)...",
            callback_log,
        )
        # Reobtém a janela a cada tentativa: se a primeira clicagem já
        # tiver disparado alguma animação/reflow, a referência antiga pode
        # ter ficado obsoleta.
        janela_mov = _obter_janela(r".*Movimenta.*", timeout_seg=5) or janela_mov
        try:
            janela_mov.set_focus()
        except Exception:
            pass

        clicou = _clicar_botao_janela(janela_mov, ["Fechar"], callback_log=callback_log)
        if clicou:
            time.sleep(1.0)
            if not _obter_janela(r".*Movimenta.*", timeout_seg=2):
                break  # modal realmente sumiu
            clicou = False  # clicou em algo, mas a modal continua lá — tenta de novo
        time.sleep(1.0)

    if not clicou:
        registrar_log(
            "  [SAJ Virtual][AVISO] Não consegui clicar no botão 'Fechar' por texto após 3 tentativas "
            "(botão provavelmente sem controle acessível — confirmado via diagnóstico anterior). "
            "Tentando clique por coordenada estimada...",
            callback_log,
        )
        janela_mov = _obter_janela(r".*Movimenta.*", timeout_seg=5) or janela_mov
        try:
            janela_mov.set_focus()
        except Exception:
            pass

        clicou = _clicar_fechar_por_coordenada(janela_mov, callback_log=callback_log)
        if clicou:
            time.sleep(1.0)
            if _obter_janela(r".*Movimenta.*", timeout_seg=2):
                clicou = False  # clicou em algo, mas a modal continua lá

    if not clicou:
        registrar_log(
            "  [SAJ Virtual][AVISO] Clique por coordenada também não fechou a modal. "
            "Despejando árvore de controles para diagnóstico antes de usar ESC como último recurso.",
            callback_log,
        )
        try:
            caminho_dump = os.path.join(
                PASTA_TEMP_PDF, f"debug_arvore_controles_{int(time.time())}.txt"
            )
            _dump_arvore_controles(janela_mov, callback_log=callback_log, arquivo_saida=caminho_dump)
        except Exception as e:
            registrar_log(f"  [SAJ Virtual][AVISO] Falha ao despejar árvore de controles: {e}", callback_log)
        try:
            janela_mov.set_focus()
        except Exception:
            pass
        pyautogui.press('esc')

    # ================ Aguarda o pop-up de nomeação do documento aparecer ================
    novo_titulo = None
    inicio = time.time()
    while time.time() - inicio < 8:
        titulos_agora = _listar_titulos_janelas()
        diferenca = titulos_agora - titulos_antes
        diferenca = {t for t in diferenca if t.strip()}
        if diferenca:
            novo_titulo = next(iter(diferenca))
            break
        time.sleep(0.4)

    # NOTA: este pop-up é o próprio seletor 'Escolha o Tipo de Documento a
    # ser Digitalizado' (busca + grade + botão 'Selecionar'), e é tratado
    # INTEIRAMENTE aqui (única vez no fluxo) através de
    # _selecionar_tipo_documento_no_popup. A ETAPA 4, mais adiante em
    # imprimir_para_saj_virtual, apenas confirma que ele já foi resolvido —
    # não digita o tipo de documento de novo.
    tipo_documento_ok = False
    if novo_titulo:
        registrar_log(f"  [SAJ Virtual] Pop-up de tipo de documento detectado: '{novo_titulo}'.", callback_log)
        janela_nome = _obter_janela(re.escape(novo_titulo), timeout_seg=5)
        if janela_nome:
            tipo_documento_ok = _selecionar_tipo_documento_no_popup(
                janela_nome, nome_documento, callback_log=callback_log
            )
            if not tipo_documento_ok:
                # Tenta mais uma vez com uma referência fresca da janela,
                # caso o pop-up ainda esteja na tela.
                janela_nome_retry = _obter_janela(re.escape(novo_titulo), timeout_seg=3)
                if janela_nome_retry:
                    registrar_log(
                        "  [SAJ Virtual][AVISO] Pop-up de tipo de documento ainda aberto após a 1ª tentativa. "
                        "Tentando novamente...",
                        callback_log,
                    )
                    tipo_documento_ok = _selecionar_tipo_documento_no_popup(
                        janela_nome_retry, nome_documento, callback_log=callback_log
                    )

            if not tipo_documento_ok:
                try:
                    caminho_falha = os.path.join(
                        PASTA_TEMP_PDF, f"debug_tipo_documento_popup_errado_{int(time.time())}.png"
                    )
                    pyautogui.screenshot(caminho_falha)
                except Exception:
                    caminho_falha = "(falha ao salvar screenshot)"
                registrar_log(
                    f"  [SAJ Virtual][AVISO] Não consegui confirmar/fechar o pop-up de tipo de documento após "
                    f"2 tentativas (screenshot: {caminho_falha}). Verifique manualmente.",
                    callback_log,
                )
        else:
            registrar_log(
                f"  [SAJ Virtual][AVISO] Pop-up '{novo_titulo}' detectado mas não consegui obter sua janela.",
                callback_log,
            )
    else:
        registrar_log(
            "  [SAJ Virtual][AVISO] Nenhum pop-up novo de tipo de documento foi detectado após 'Fechar'. "
            "Verifique manualmente se o fluxo do SAJ mudou.",
            callback_log,
        )

    # ================ Confirma que a modal de Movimentações realmente sumiu ================
    ainda_aberta = None
    for _ in range(6):  # até ~9s de folga extra
        time.sleep(1.5)
        ainda_aberta = _obter_janela(r".*Movimenta.*", timeout_seg=1)
        if not ainda_aberta:
            break

    if ainda_aberta:
        registrar_log(
            "  [SAJ Virtual][AVISO] A modal 'Movimentações' ainda parece estar aberta após o fechamento.",
            callback_log,
        )
        return False

    if novo_titulo and not tipo_documento_ok:
        # A modal 'Movimentações' fechou, mas o pop-up de tipo de documento
        # que ela abriu não foi confirmado — se seguíssemos em frente, a
        # ETAPA 4 encontraria o pop-up ainda aberto (ou pior, campos da
        # tela errada) e travaria. Trata como falha real do mesmo jeito que
        # 'Movimentações' continuar aberta.
        registrar_log(
            "  [SAJ Virtual][AVISO] Modal 'Movimentações' fechada, mas o tipo de documento não foi "
            "confirmado no pop-up. Abortando este item.",
            callback_log,
        )
        return False

    registrar_log("  [SAJ Virtual] Modal 'Movimentações' fechada com sucesso.", callback_log)
    return True



def _centralizar_mouse(callback_log=None):
    """
    Move o cursor do mouse para o centro da tela primária.

    Não evita um toque físico de alguém no canto da tela no exato instante
    de uma chamada do PyAutoGUI (isso é o fail-safe funcionando como
    projetado — um "botão de pânico" manual), mas evita que o cursor fique
    'parado' num canto por causa de uma ação anterior do próprio robô e
    dispare o fail-safe na próxima chamada sem motivo real.
    """
    try:
        largura, altura = pyautogui.size()
        pyautogui.moveTo(largura // 2, altura // 2, duration=0.1)
    except Exception as e:
        if callback_log:
            registrar_log(f"  [AVISO] Não consegui recentralizar o mouse: {e}", callback_log)


def _encontrar_campo_edicao(janela, callback_log=None):
    """
    Retorna o primeiro descendente de `janela` que seja um campo de
    texto/combo editável (classes 'Edit' ou 'ComboBox'), ou None.

    Diferente de usar `janela.child_window(...)`, que só existe em objetos
    WindowSpecification (referências "preguiçosas" obtidas via
    `app.window(...)`), aqui a busca é feita em `.descendants()` — que
    funciona nos wrappers concretos devolvidos por `_obter_janela`
    (DialogWrapper/HwndWrapper vindos de `Desktop(...).windows(...)`), os
    quais não têm `child_window` (foi exatamente esse `AttributeError` que
    quebrou o preenchimento do pop-up 'Escolha o Tipo de Documento a ser
    Digitalizado').
    """
    try:
        descendentes = janela.descendants()
    except Exception as e:
        if callback_log:
            registrar_log(f"  [SAJ Virtual][AVISO] Não consegui listar descendentes para achar o campo de edição: {e}", callback_log)
        return None

    for elem in descendentes:
        try:
            classe = elem.friendly_class_name()
        except Exception:
            continue
        if classe in ("Edit", "ComboBox"):
            return elem
    return None


def _selecionar_tipo_documento_no_popup(janela_popup, nome_documento, callback_log=None):
    """
    Preenche o campo de busca do pop-up 'Escolha o Tipo de Documento a ser
    Digitalizado' (busca + grade + botão 'Selecionar' — NÃO é um campo de
    texto simples com autocomplete, como o restante do código assumia) e
    confirma a seleção clicando em 'Selecionar'.

    IMPORTANTE sobre a verificação: depois que a grade filtra e destaca a
    linha correspondente, o foco sai do campo de busca e vai para a
    própria grade. Isso quebra qualquer verificação baseada em Ctrl+A/
    Ctrl+C (a leitura sempre volta vazia, mesmo com a linha certa já
    destacada na tela — foi exatamente esse falso negativo que travava o
    robô). Por isso a única confirmação usada aqui é objetiva: clicar em
    'Selecionar' e checar se a janela do pop-up realmente fechou.

    Retorna True se o pop-up fechou após o clique (sucesso), False caso
    contrário.
    """
    titulo_popup = None
    try:
        titulo_popup = janela_popup.window_text()
    except Exception:
        pass

    campo_texto = _encontrar_campo_edicao(janela_popup, callback_log=callback_log)
    if campo_texto is None:
        registrar_log(
            "  [SAJ Virtual][AVISO] Não encontrei o campo de busca no pop-up 'Escolha o Tipo de "
            "Documento a ser Digitalizado'.",
            callback_log,
        )
        return False

    campo_texto.click_input()
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('backspace')
    time.sleep(0.2)
    _digitar_letra_por_letra(nome_documento, pausa=0.08, callback_log=callback_log)
    registrar_log("  [SAJ Virtual] Aguardando a grade filtrar/destacar o item correto...", callback_log)
    time.sleep(1.2)

    confirmou_clique = _clicar_botao_janela(
        janela_popup, ["Selecionar", "OK", "Confirmar", "Salvar", "Gravar"], callback_log=callback_log
    )
    if not confirmou_clique:
        # Com a grade em foco, Enter costuma disparar a mesma ação que o
        # botão 'Selecionar' (seleção do item destacado).
        pyautogui.press('enter')
    time.sleep(1.0)

    # Única confirmação confiável: a própria janela do pop-up sumiu.
    if titulo_popup:
        ainda_aberto = _obter_janela(re.escape(titulo_popup), timeout_seg=2)
    else:
        ainda_aberto = None
    fechou = not bool(ainda_aberto)

    if fechou:
        registrar_log(
            f"  [SAJ Virtual] Tipo de documento '{nome_documento}' selecionado e pop-up confirmado/fechado.",
            callback_log,
        )
    return fechou


def _normalizar_numero_saj(texto):
    """Mantém só os dígitos, para comparar 'Nosso Nº' (2026.01.000001) com
    o número formatado que o robô tentou inserir, ignorando pontuação."""
    return re.sub(r'\D', '', texto or "")


def _obter_nosso_numero_atual(callback_log=None):
    """
    Lê o valor atualmente exibido no campo 'Nosso Nº' da janela 'Digitalização
    de Peças Processuais' do SAJ — é esse campo que determina em qual processo
    o despacho será vinculado. Usado para CONFIRMAR que o robô está mesmo no
    SAJ esperado antes de prosseguir com a inserção do despacho de cobrança.
    """
    janela = _obter_janela(r".*Digitaliza.*Pe.*Processuais.*", timeout_seg=5)
    if not janela:
        janela = _obter_janela(r".*SAJ.*", timeout_seg=5)
    if not janela:
        registrar_log(
            "  [SAJ Virtual][AVISO] Não localizei a janela do SAJ para ler o campo 'Nosso Nº'.",
            callback_log,
        )
        return ""

    try:
        descendentes = janela.descendants()
    except Exception:
        descendentes = []

    # O 'Nosso Nº' segue o padrão AAAA.NN.NNNNNN (ano.unidade.sequencial).
    for elem in descendentes:
        try:
            texto = (elem.window_text() or "").strip()
        except Exception:
            continue
        if re.match(r'^\d{4}\.\d{2}\.\d{6}', texto):
            return texto
    return ""


def verificar_e_corrigir_saj(num_saj_esperado, callback_log=None):
    """
    Checagem obrigatória ANTES de inserir o despacho de cobrança: confirma
    se o processo carregado na tela 'Digitalização de Peças Processuais' do
    SAJ é realmente o esperado (num_saj_esperado).

    - Está no SAJ correto? -> retorna True, e o chamador segue e insere o
      despacho de cobrança normalmente.
    - Está no SAJ errado?  -> fecha o pop-up de 'Movimentações' (se estiver
      aberto, pois ele pode ter sido disparado para o processo errado),
      limpa a pasta digital carregada ('Limpar' / atalho Alt+L) e insere
      novamente o número correto no campo 'Nosso Nº', para então o fluxo
      continuar a partir do processo certo.

    Retorna True se, ao final, a tela está confirmadamente no SAJ correto
    (False só se mesmo após a correção não foi possível confirmar).
    """
    esperado_norm = _normalizar_numero_saj(num_saj_esperado)
    if not esperado_norm:
        return True

    atual = _obter_nosso_numero_atual(callback_log=callback_log)
    atual_norm = _normalizar_numero_saj(atual)

    registrar_log(
        f"  [SAJ Virtual] Checagem antes do despacho -> SAJ esperado: '{num_saj_esperado}' | "
        f"SAJ atual na tela: '{atual or '(não lido)'}'.",
        callback_log,
    )

    if atual_norm and atual_norm == esperado_norm:
        registrar_log("  [SAJ Virtual] ✅ SAJ correto confirmado. Pode inserir o despacho de cobrança.", callback_log)
        return True

    # ------- SAJ errado (ou não confirmado): aciona a correção -------
    registrar_log(
        f"  ⚠️ [SAJ Virtual] SAJ DIVERGENTE do esperado! Corrigindo antes de inserir o despacho...",
        callback_log,
    )

    # Se o pop-up de 'Movimentações' apareceu (referente ao processo errado),
    # fecha antes de mexer em qualquer outra coisa.
    janela_mov = _obter_janela(r".*Movimenta.*", timeout_seg=2)
    if janela_mov:
        registrar_log("  [SAJ Virtual] Fechando o pop-up de Movimentações do SAJ errado...", callback_log)
        if not _clicar_botao_janela(janela_mov, ["Fechar"], callback_log=callback_log):
            pyautogui.press('esc')
        time.sleep(0.8)

    # Limpa a pasta digital carregada na tela (botão 'Limpar' / atalho Alt+L).
    _aguardar_janela_em_foco(r".*SAJ.*", timeout_seg=5, callback_log=callback_log)
    registrar_log("  [SAJ Virtual] Limpando a pasta digital (Alt+L)...", callback_log)
    pyautogui.hotkey('alt', 'l')
    time.sleep(1.0)

    # Reinsere o número correto do SAJ, limpando o campo antes de colar para
    # não deixar resíduo do número anterior (causa raiz do problema original:
    # o Ctrl+V não substituía o conteúdo antigo do campo 'Nosso Nº').
    registrar_log(f"  [SAJ Virtual] Inserindo o SAJ correto nº {num_saj_esperado}...", callback_log)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('delete')
    pyperclip.copy(num_saj_esperado)
    pyautogui.hotkey('ctrl', 'v')
    pyautogui.press('tab')
    time.sleep(1.5)

    # Confirma se a correção funcionou.
    atual_pos_correcao = _obter_nosso_numero_atual(callback_log=callback_log)
    atual_pos_correcao_norm = _normalizar_numero_saj(atual_pos_correcao)
    corrigido = atual_pos_correcao_norm == esperado_norm

    if corrigido:
        registrar_log("  [SAJ Virtual] ✅ SAJ corrigido com sucesso. Continuando o fluxo.", callback_log)
    else:
        registrar_log(
            f"  ❌ [SAJ Virtual] Mesmo após a correção, o SAJ na tela ('{atual_pos_correcao or '(não lido)'}') "
            f"ainda não bate com o esperado ('{num_saj_esperado}').",
            callback_log,
        )
    return corrigido


def imprimir_para_saj_virtual(page, num_saj, callback_log=None):
    """
    Aciona o ícone de impressão do Despacho no SEI, fecha a modal de movimentações no SAJ e vincula o documento.
    """
    # ==================== ETAPA 0: CLICAR NO ÍCONE 'IMPRIMIR' DO SEI ====================
    registrar_log("  [SEI] Clicando no ícone 'Imprimir' do Despacho...", callback_log)

    btn_imprimir = None
    for tentativa in range(1, 7):  # ~6 x 5s = até 30s, re-obtendo o frame a cada tentativa
        try:
            frame_vis = obter_frame_visualizacao(page)
            candidato = frame_vis.locator(
                "a[href*='acao=documento_imprimir'], img[title*='Imprimir'], a[title*='Imprimir']"
            ).first
            candidato.wait_for(state="visible", timeout=5000)
            btn_imprimir = candidato
            break
        except Exception:
            if tentativa == 1:
                registrar_log(
                    "  [SEI] Ícone 'Imprimir' ainda não visível, aguardando o iframe terminar de renderizar...",
                    callback_log,
                )
            time.sleep(1.0)

    if not btn_imprimir:
        raise RuntimeError(
            "Ícone 'Imprimir' do Despacho não apareceu na tela de visualização após a assinatura "
            "(possível demora do SEI para renderizar o documento assinado)."
        )

    btn_imprimir.click()

    registrar_log("  [SEI] Aguardando o carregamento da tela nativa de impressão do Chrome...", callback_log)
    time.sleep(3.5)

    # Recentraliza o cursor antes de qualquer ação nativa via PyAutoGUI: se o
    # mouse ficou 'parado' num canto da tela por causa de alguma ação
    # anterior (ou de alguém usando a máquina), evita que a próxima chamada
    # do PyAutoGUI dispare o fail-safe sem necessidade.
    _centralizar_mouse(callback_log)

    registrar_log("  [SEI] Pressionando 'Enter' para disparar a impressão...", callback_log)
    pyautogui.press('enter')
    
    registrar_log("  [SEI] Comando de impressão enviado. Aguardando a janela do SAJ...", callback_log)
    time.sleep(2.5)

    # ==================== ETAPA 1: AGUARDAR JANELA DO SAJ OU MODAL EM FOCO ====================
    janela_confirmada = _aguardar_janela_em_foco(r".*(SAJ|Movimenta).*", timeout_seg=30, callback_log=callback_log)

    if not janela_confirmada:
        # Não é mais um erro fatal: na prática a janela do SAJ e o pop-up de
        # 'Movimentações com Digitalização a Realizar' já estão na tela nesse
        # ponto (o próprio screenshot de diagnóstico confirma isso) — o
        # pywinauto só não conseguiu "enxergar"/focar a janela (ex.: SAJ
        # rodando como Administrador). Como o pop-up sempre pode ser fechado
        # com ESC (ou no botão 'Fechar'), seguimos o fluxo normalmente.
        try:
            caminho_falha = os.path.join(PASTA_TEMP_PDF, f"debug_janela_nao_encontrada_{int(time.time())}.png")
            pyautogui.screenshot(caminho_falha)
            registrar_log(
                f"  [SAJ Virtual][AVISO] Não confirmei a janela do SAJ via pywinauto "
                f"(screenshot salvo em {caminho_falha}). Prosseguindo mesmo assim.",
                callback_log,
            )
        except Exception:
            registrar_log(
                "  [SAJ Virtual][AVISO] Não confirmei a janela do SAJ via pywinauto. Prosseguindo mesmo assim.",
                callback_log,
            )

    # ==================== ETAPA 2: INSERIR O 'NOSSO Nº' (PROCESSO SAJ) ====================
    # IMPORTANTE: o pop-up 'Movimentações com Digitalização a Realizar' só
    # aparece DEPOIS que o campo 'Nosso Nº' é preenchido (o SAJ dispara a
    # checagem de movimentações pendentes ao processar esse número) — por
    # isso o número precisa ser inserido antes de tentar fechar a modal.
    registrar_log(f"  [SAJ Virtual] Inserindo processo SAJ nº {num_saj}...", callback_log)
    pyautogui.hotkey('ctrl', 'a')  # seleciona o que já estiver no campo 'Nosso Nº'...
    pyautogui.press('delete')      # ...e apaga, para não deixar resíduo do processo anterior
    pyperclip.copy(num_saj)
    pyautogui.hotkey('ctrl', 'v')
    pyautogui.press('tab')
    time.sleep(1.5)  # dá tempo do SAJ processar o número e, se for o caso, abrir a modal

    # ==================== ETAPA 2.5: CHECAGEM OBRIGATÓRIA — ESTÁ NO SAJ CORRETO? ====================
    # Antes de deixar o robô inserir o despacho de cobrança, confirma que o
    # processo carregado na tela é realmente o esperado (num_saj). Isso evita
    # o cenário observado em produção: o campo 'Nosso Nº' não atualiza entre
    # um processo e outro e o despacho acaba sendo vinculado ao SAJ anterior.
    #   - Está no SAJ correto? -> segue o fluxo normalmente (ETAPA 3).
    #   - Está no SAJ errado?  -> fecha o pop-up de Movimentações (se surgiu
    #     para o processo errado), limpa a pasta digital (Alt+L) e reinsere
    #     o SAJ correto antes de continuar.
    saj_ok = verificar_e_corrigir_saj(num_saj, callback_log=callback_log)
    if not saj_ok:
        raise RuntimeError(
            f"Não foi possível confirmar/corrigir o SAJ correto (esperado: {num_saj}) antes de inserir "
            f"o despacho de cobrança. Abortando este item para não vincular o despacho ao processo errado."
        )

    # ==================== ETAPA 3: FECHAR O POP-UP DE MOVIMENTAÇÕES (SE APARECER) ====================
    modal_fechada_ok = _fechar_modal_movimentacoes_e_nomear_documento(
        nome_documento="Cobrança de Ofício - Solicitação", callback_log=callback_log
    )

    if modal_fechada_ok is False:
        try:
            caminho_falha = os.path.join(PASTA_TEMP_PDF, f"debug_modal_nao_fechou_{int(time.time())}.png")
            pyautogui.screenshot(caminho_falha)
        except Exception:
            caminho_falha = "(falha ao salvar screenshot)"
        # Não faz mais sentido seguir com a modal ainda bloqueando a tela:
        # qualquer tecla/clique enviado a partir daqui cai "no vácuo" (foi
        # exatamente isso que causou o campo de tipo de documento vir vazio
        # no processamento anterior). Melhor abortar este item com um erro
        # claro do que preencher dados no lugar errado.
        raise RuntimeError(
            f"A modal 'Movimentações com Digitalização a Realizar' não fechou mesmo após as tentativas "
            f"de clicar em 'Fechar'. Abortando este processo para não preencher campos no lugar errado "
            f"(screenshot: {caminho_falha})."
        )
    # modal_fechada_ok is None -> modal nunca apareceu; segue o fluxo normal
    # sem abortar (não é um erro, é um caminho legítimo do processo).

    # Reconfirma (best-effort, sem travar o fluxo) o foco na tela principal do SAJ
    _aguardar_janela_em_foco(r".*SAJ.*", timeout_seg=5, callback_log=callback_log)

    # ==================== ETAPA 4: CONTINUAR APÓS O TIPO DE DOCUMENTO ====================
    # O tipo de documento já foi digitado, selecionado na grade e confirmado
    # (pop-up fechado) dentro de _fechar_modal_movimentacoes_e_nomear_documento,
    # na ETAPA 3 — chegar aqui com modal_fechada_ok != False É a garantia
    # disso. Repetir a digitação aqui era exatamente a causa da falha: o
    # tipo de documento acabava sendo digitado até 3 vezes (2 tentativas na
    # função da ETAPA 3 + 1+ aqui), e a verificação via Ctrl+A/Ctrl+C usada
    # nesta etapa nunca funcionava para esse pop-up (é uma grade, não um
    # campo de texto — o foco migra para a grade depois que ela filtra, e
    # Ctrl+C numa grade não copia nada), fazendo o robô abortar mesmo
    # quando a seleção já estava visivelmente correta na tela.
    registrar_log(
        f"  [SAJ Virtual] Tipo de documento '{NOME_TIPO_DOCUMENTO_COBRANCA}' já confirmado na etapa anterior. "
        f"Prosseguindo...",
        callback_log,
    )

    pyautogui.press('tab')
    time.sleep(0.5)

    registrar_log("  [SAJ Virtual] Confirmando dados...", callback_log)
    pyautogui.press('enter')
    time.sleep(1.5)

    try:
        pyautogui.press('esc')
        time.sleep(0.5)
    except Exception:
        pass

    registrar_log("  [SAJ Virtual] Salvando e finalizando vinculação na Pasta Digital...", callback_log)
    pyautogui.hotkey('alt', 'f')
    pyautogui.press('enter')
    time.sleep(2.0)

    registrar_log(f"  ✅ [SAJ Virtual] Despacho vinculado com sucesso ao processo SAJ {num_saj}!", callback_log)
def processar_fluxo_completo_sei(page, num_sei, num_saj, num_diligencia, secretaria, caminho, usuario_sei, senha_sei, callback_log=None):
    """Executa a rotina completa no SEI e no SAJ Impressora Virtual."""
    
    registrar_log(f"  [SEI] Abrindo processo: {num_sei}", callback_log)

    campo_pesquisa = aguardar_pesquisa_rapida(page)
    campo_pesquisa.fill(num_sei)
    campo_pesquisa.press("Enter")
    page.wait_for_load_state("networkidle")

    frame_vis = obter_frame_visualizacao(page)
    btn_reabrir = frame_vis.locator("a[href*='acao=procedimento_reabrir'], img[title*='Reabrir Processo'], a[title*='Reabrir Processo']").first
    try:
        if btn_reabrir.is_visible(timeout=3000):
            registrar_log("  [SEI] Processo fechado na unidade. Reabrindo processo...", callback_log)
            btn_reabrir.click()
            page.wait_for_load_state("networkidle")
            time.sleep(0.5)
    except Exception:
        pass

    id_documento = extrair_id_diligencia_arvore(page, num_diligencia, callback_log)
    if id_documento:
        registrar_log(f"  [SEI] Diligência {num_diligencia} identificada com sucesso (ID: {id_documento}).", callback_log)
        str_id = f" (id. {id_documento})"
    else:
        registrar_log(f"  ⚠️ [SEI] Não foi possível localizar o ID da Diligência {num_diligencia} na árvore lateral.", callback_log)
        str_id = ""

    registrar_log("  [SEI] Clicando em 'Incluir Documento'...", callback_log)
    frame_vis = obter_frame_visualizacao(page)
    frame_vis.locator("a[href*='acao=documento_escolher_tipo'], img[title*='Incluir Documento']").first.click()

    registrar_log("  [SEI] Selecionando tipo 'Despacho'...", callback_log)
    frame_vis = obter_frame_visualizacao(page)
    frame_vis.wait_for_selector("a:has-text('Despacho')")
    frame_vis.locator("a:has-text('Despacho')").first.click()

    frame_vis = obter_frame_visualizacao(page)
    if frame_vis.locator("#optNenhum").is_visible():
        frame_vis.locator("#optNenhum").evaluate("el => { el.checked = true; el.click(); }")

    registrar_log("  [SEI] Configurando Nível de Acesso para 'Público'...", callback_log)
    opt_publico = frame_vis.locator("#optPublico, input[type='radio'][value='0']").first
    opt_publico.scroll_into_view_if_needed()
    opt_publico.evaluate("el => { el.checked = true; el.click(); }")

    registrar_log("  [SEI] Salvando configurações do documento e abrindo Editor...", callback_log)
    btn_salvar_doc = frame_vis.locator("#btnSalvar, input[value='Salvar']").first
    btn_salvar_doc.scroll_into_view_if_needed()

    with page.expect_popup() as editor_info:
        btn_salvar_doc.click()

    page_editor = editor_info.value
    page_editor.wait_for_load_state("domcontentloaded")
    registrar_log("  [SEI] Editor do Despacho aberto.", callback_log)

    registrar_log("  [SEI] Inserindo texto de cobrança no editor...", callback_log)

    conteudo_despacho_html = f"""<p>À {secretaria}</p>
    <p>Prezados,<br>
    Alguma informação acerca desta demanda?</p>
    <p>Peço que por gentileza nos posicione acerca da Diligência {num_diligencia}.{str_id}</p>
    <br>
    <p><strong>OBS: Favor responder ao oficio na unidade SEI PGM/PGA/PUMA.</strong></p>"""

    page_editor.wait_for_selector("iframe.cke_wysiwyg_frame", timeout=15000)

    page_editor.wait_for_function(
        """() => {
            if (!window.CKEDITOR) return false;
            const keys = Object.keys(CKEDITOR.instances);
            if (keys.length === 0) return false;
            return CKEDITOR.instances[keys[0]].status === 'ready';
        }""",
        timeout=15000,
    )

    resultado_insercao = page_editor.evaluate("""(html) => {
        if (!window.CKEDITOR) return { ok: false, motivo: 'CKEDITOR não carregado' };

        const keys = Object.keys(CKEDITOR.instances);
        if (keys.length === 0) return { ok: false, motivo: 'nenhuma instância do CKEditor' };

        const candidatos = keys.map(k => {
            const inst = CKEDITOR.instances[k];
            let altura = 0;
            try {
                const frame = inst.window && inst.window.getFrame ? inst.window.getFrame().$ : null;
                if (frame) altura = frame.getBoundingClientRect().height;
            } catch (e) {}

            const bodyHtml = inst.document ? inst.document.getBody().getHtml() : '';
            const textoPlano = (inst.document ? inst.document.getBody().getText() : '').toLowerCase();

            return {
                nome: k,
                altura: altura,
                temImagem: /<img/i.test(bodyHtml),
                pareceCabecalho: textoPlano.includes('despacho') && (textoPlano.includes('recife') || textoPlano.includes('nº') || textoPlano.includes('n°')),
                previewTexto: textoPlano.slice(0, 80),
            };
        });

        let elegiveis = candidatos.filter(c => !c.temImagem && !c.pareceCabecalho);
        if (elegiveis.length === 0) elegiveis = candidatos.slice();

        elegiveis.sort((a, b) => b.altura - a.altura);
        const escolhido = elegiveis[0];

        const instance = CKEDITOR.instances[escolhido.nome];
        instance.focus();

        const body = instance.document.getBody();

        const range = instance.createRange();
        range.selectNodeContents(body);
        instance.getSelection().selectRanges([range]);
        body.setHtml('');

        instance.insertHtml(html, 'unfiltered_html');
        instance.updateElement();

        return {
            ok: true,
            instanciaUsada: escolhido.nome,
        };
    }""", conteudo_despacho_html)

    if not resultado_insercao or not resultado_insercao.get("ok"):
        motivo = resultado_insercao.get("motivo") if resultado_insercao else "retorno vazio"
        raise RuntimeError(f"Falha ao inserir texto no editor do Despacho: {motivo}")

    registrar_log(f"  [SEI] Texto inserido com sucesso na instância '{resultado_insercao.get('instanciaUsada')}'.", callback_log)

    registrar_log("  [SEI] Salvando documento no editor...", callback_log)
    btn_salvar_editor = page_editor.locator("a.cke_button__salvar, a[title*='Salvar']").first
    if btn_salvar_editor.is_visible():
        btn_salvar_editor.click()
    else:
        page_editor.evaluate("""() => {
            if (window.CKEDITOR) {
                Object.keys(CKEDITOR.instances).forEach(k => {
                    const inst = CKEDITOR.instances[k];
                    inst.updateElement();
                    if (inst.getCommand('save')) inst.execCommand('save');
                });
            }
        }""")

    page_editor.wait_for_timeout(1500)

    registrar_log("  [SEI] Localizando e acionando botão 'Assinar'...", callback_log)

    def _aceitar_dialogo(dialog):
        registrar_log(f"  [SEI] Confirmando alerta: '{dialog.message}'", callback_log)
        dialog.accept()

    page_editor.on("dialog", _aceitar_dialogo)

    page_assinatura = None
    try:
        with page_editor.expect_popup(timeout=5000) as popup_info:
            seletor_assinar = "a.cke_button__assinatura, a[title*='Assinar'], #btnAssinar"
            btn_assinar = page_editor.locator(seletor_assinar).first
            if btn_assinar.is_visible():
                btn_assinar.click()
            else:
                page_editor.evaluate("""() => {
                    if (window.CKEDITOR) {
                        Object.keys(CKEDITOR.instances).forEach(k => {
                            const inst = CKEDITOR.instances[k];
                            if (inst.getCommand('assinatura')) inst.execCommand('assinatura');
                        });
                    }
                }""")
        page_assinatura = popup_info.value
        page_assinatura.wait_for_load_state("domcontentloaded")
        registrar_log("  [SEI] Janela de assinatura aberta em popup.", callback_log)
    except Exception:
        registrar_log("  [SEI] Janela de assinatura aberta no contexto principal.", callback_log)
        page_assinatura = page_editor
    finally:
        try:
            page_editor.remove_listener("dialog", _aceitar_dialogo)
        except Exception:
            pass

    registrar_log("  [SEI] Preenchendo campo de senha da assinatura...", callback_log)

    senha_preenchida = False
    paginas_alvo = [page_assinatura] if page_assinatura else []
    paginas_alvo.extend([p for p in page_editor.context.pages if p not in paginas_alvo])

    seletor_senha = "input[type='password'], #pwdSenha, #txtSenha, #pw3_txt_senha"

    for p_alvo in paginas_alvo:
        try:
            p_alvo.bring_to_front()

            campo_pwd = None
            for frame in p_alvo.frames:
                try:
                    loc = frame.locator(seletor_senha).first
                    if loc.is_visible(timeout=1500):
                        campo_pwd = loc
                        break
                except Exception:
                    pass

            if not campo_pwd:
                loc = p_alvo.locator(seletor_senha).first
                if loc.is_visible(timeout=1500):
                    campo_pwd = loc

            if campo_pwd:
                campo_pwd.fill(senha_sei)
                
                btn_confirmar_ass = p_alvo.locator("#btnAssinar, #btn_assinar, input[value='Assinar']").first
                if btn_confirmar_ass.is_visible(timeout=1000):
                    btn_confirmar_ass.click()
                else:
                    campo_pwd.press("Enter")

                senha_preenchida = True
                registrar_log("  [SEI] Senha preenchida e assinatura confirmada com sucesso.", callback_log)
                break
        except Exception:
            pass

    if not senha_preenchida:
        for p_alvo in paginas_alvo:
            try:
                p_alvo.keyboard.type(senha_sei)
                p_alvo.keyboard.press("Enter")
                senha_preenchida = True
                registrar_log("  [SEI] Senha enviada para o elemento ativo e 'Enter' pressionado.", callback_log)
                break
            except Exception:
                pass

    if not senha_preenchida:
        raise RuntimeError("Não foi possível enviar a senha para o campo ativo da janela de assinatura.")

    time.sleep(1.0)

    try:
        if page_assinatura and page_assinatura != page_editor:
            page_assinatura.close()
    except Exception:
        pass

    try:
        page_editor.close()
    except Exception:
        pass

    selecionar_ultimo_despacho_arvore(page, callback_log)

    if num_saj:
        imprimir_para_saj_virtual(page, num_saj, callback_log)

    destino_final = caminho if caminho else secretaria
    registrar_log(f"  [SEI] Encaminhando processo para a secretaria: {destino_final}...", callback_log)

    page.bring_to_front()
    frame_vis = obter_frame_visualizacao(page)

    btn_enviar_proc = frame_vis.locator("a[href*='acao=unidade_enviar'], img[title*='Enviar Processo'], a[title*='Enviar Processo']").first
    btn_enviar_proc.click()
    page.wait_for_load_state("networkidle")

    time.sleep(1.0)
    frame_vis = obter_frame_visualizacao(page)

    campo_unidade = frame_vis.locator("#txtUnidade")
    campo_unidade.wait_for(state="visible", timeout=15000)

    campo_unidade.click()
    campo_unidade.fill("")
    campo_unidade.press_sequentially(destino_final, delay=80)

    time.sleep(1.5)
    campo_unidade.press("ArrowDown")
    time.sleep(0.5)
    campo_unidade.press("Enter")
    time.sleep(1.0)

    btn_enviar = frame_vis.locator("#btnEnviar, input[value='Enviar'], button:has-text('Enviar')").first
    btn_enviar.click()
    page.wait_for_load_state("networkidle")
    registrar_log("  [SEI] Processo encaminhado para a secretaria.", callback_log)

    registrar_log("  ✅ [SEI/SAJ] Ciclo completo executado com sucesso para este processo!", callback_log)


def atualizar_banco_pos_cobranca(id_diligencia, usuario_app, dias_prorrogacao=5, callback_log=None):
    """Atualiza o prazo no MySQL e registra o histórico de cobrança."""
    con = obter_conexao()
    if not con:
        return
    try:
        cur = con.cursor()

        try:
            dias_num = int(dias_prorrogacao)
        except (ValueError, TypeError):
            dias_num = 5

        sql_log = """
            INSERT INTO tb_cobranca_respostas 
            (diligencia_id, saj, sei, orgao_secretaria, caminho, data_cobranca, meio_cobranca, dias_prorrogados, observacao, usuario_criacao, data_criacao)
            SELECT 
                id, 
                saj, 
                sei, 
                secretaria, 
                caminho, 
                CURDATE(), 
                'Despacho SEI / SAJ Virtual', 
                %s, 
                'Despacho de Cobrança enviado via SEI e SAJ Virtual', 
                %s, 
                NOW()
            FROM tb_gestao
            WHERE id = %s
        """
        try:
            cur.execute(sql_log, (dias_num, usuario_app, id_diligencia))
        except mysql.connector.Error:
            sql_log_fallback = """
                INSERT INTO tb_cobranca_respostas 
                (diligencia_id, saj, sei, orgao_secretaria, data_cobranca, meio_cobranca, dias_prorrogados, observacao, usuario_criacao, data_criacao)
                SELECT 
                    id, 
                    saj, 
                    sei, 
                    secretaria, 
                    CURDATE(), 
                    'Despacho SEI / SAJ Virtual', 
                    %s, 
                    'Despacho de Cobrança enviado via SEI e SAJ Virtual', 
                    %s, 
                    NOW()
                FROM tb_gestao
                WHERE id = %s
            """
            cur.execute(sql_log_fallback, (dias_num, usuario_app, id_diligencia))

        sql_update = """
            UPDATE tb_gestao 
            SET prazo = DATE_ADD(CURDATE(), INTERVAL %s DAY) 
            WHERE id = %s
        """
        cur.execute(sql_update, (dias_num, id_diligencia))
        
        con.commit()
        registrar_log(f"  [BANCO] Prazo prorrogado por +{dias_num} dias.", callback_log)
    except Exception as e:
        registrar_log(f"  ❌ [BANCO] Erro ao atualizar banco de dados: {e}", callback_log)
    finally:
        cur.close()
        con.close()


def iniciar_automacao_completa(usuario_sei, senha_sei, usuario_app="Robô", dias_prorrogacao=5, callback_log=None):
    """Orquestrador da automação completa."""
    diligencias = obter_diligencias_vencidas()

    if not diligencias:
        registrar_log("[ROBÔ] Nenhuma diligência em atraso encontrada.", callback_log)
        return

    registrar_log(f"[ROBÔ] {len(diligencias)} diligências encontradas para cobrança (Prorrogação: +{dias_prorrogacao} dias).", callback_log)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if os.path.exists(ARQUIVO_SESSAO_SEI):
            context = browser.new_context(storage_state=ARQUIVO_SESSAO_SEI, accept_downloads=True)
        else:
            context = browser.new_context(accept_downloads=True)

        page = context.new_page()

        try:
            page.goto(SEI_URL)
            page.wait_for_load_state("domcontentloaded")

            if page.locator("#txtUsuario").is_visible():
                registrar_log("[SEI] Efetuando login no SEI Recife...", callback_log)
                page.fill("#txtUsuario", usuario_sei)
                page.fill("#pwdSenha", senha_sei)

                try:
                    page.select_option("#selOrgao", label="PGM")
                except Exception:
                    page.select_option("#selOrgao", value="PGM")

                btn_acessar = page.locator("#sbmLogin, button:has-text('ACESSAR'), input[value='ACESSAR']")

                with page.expect_navigation(timeout=30000, wait_until="networkidle"):
                    if btn_acessar.is_visible():
                        btn_acessar.first.click()
                    else:
                        page.keyboard.press("Enter")

                context.storage_state(path=ARQUIVO_SESSAO_SEI)
                registrar_log("[SEI] Login efetuado e sessão salva com sucesso.", callback_log)
            else:
                registrar_log("[SEI] Sessão ativa reutilizada.", callback_log)

            aguardar_pesquisa_rapida(page)

            for item in diligencias:
                num_sei = item.get("sei")
                num_saj = item.get("saj")
                id_dil = item.get("id")
                secretaria = item.get("secretaria", "")
                caminho = item.get("caminho", "")
                
                num_diligencia = item.get("diligencia", "")
                if not num_diligencia:
                    num_diligencia = f"{id_dil:02d}/{datetime.now().year}"

                if not num_sei:
                    continue

                registrar_log(f"\n--- Processando ID {id_dil} (SEI: {num_sei} | SAJ: {num_saj}) ---", callback_log)

                try:
                    processar_fluxo_completo_sei(
                        page=page,
                        num_sei=num_sei,
                        num_saj=num_saj,
                        num_diligencia=num_diligencia,
                        secretaria=secretaria,
                        caminho=caminho,
                        usuario_sei=usuario_sei,
                        senha_sei=senha_sei,
                        callback_log=callback_log
                    )
                    atualizar_banco_pos_cobranca(
                        id_diligencia=id_dil, 
                        usuario_app=usuario_app, 
                        dias_prorrogacao=dias_prorrogacao, 
                        callback_log=callback_log
                    )

                except pyautogui.FailSafeException:
                    registrar_log(
                        f"⚠️ [SEGURANÇA] Falha no processamento do ID {id_dil}: o PyAutoGUI abortou a automação "
                        f"porque o mouse foi movido para um canto da tela durante a execução. Isso normalmente "
                        f"indica que alguém usou o mouse/teclado da máquina enquanto o robô estava rodando "
                        f"(o fail-safe é intencional — funciona como um botão de pânico manual). Este item NÃO "
                        f"foi reprocessado automaticamente, pois algumas etapas anteriores (ex.: assinatura do "
                        f"documento) já podem ter sido concluídas no SEI/SAJ; reprocessar do zero poderia "
                        f"duplicá-las. Revise o item {id_dil} manualmente antes de rodar de novo.",
                        callback_log,
                    )
                except Exception as ex:
                    registrar_log(f"❌ Falha no processamento do ID {id_dil}: {ex}", callback_log)

        finally:
            browser.close()
            registrar_log("\n[ROBÔ] Processamento finalizado.", callback_log)