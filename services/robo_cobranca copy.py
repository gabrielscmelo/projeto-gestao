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

# Configurações globais
pyautogui.PAUSE = 0.2
pyautogui.FAILSAFE = True

PASTA_TEMP_PDF = os.path.abspath("temp_cobrancas")
os.makedirs(PASTA_TEMP_PDF, exist_ok=True)

SEI_URL = "https://sip.recife.pe.gov.br/sip/login.php?sigla_orgao_sistema=PR&sigla_sistema=SEI"
ARQUIVO_SESSAO_SEI = os.path.abspath("sei_session.json")


def registrar_log(mensagem, callback_log=None):
    """Registra a mensagem no log do sistema e na interface visual."""
    logging.info(mensagem)
    print(mensagem)
    if callback_log:
        callback_log(mensagem)


def obter_diligencias_vencidas():
    """Consulta no MySQL as diligências com prazo vencido incluindo o campo 'diligencia'."""
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
    Varre a árvore lateral do SEI (ifrArvore) para capturar o ID da diligência,
    com resiliência a reloads de frame (Execution context destroyed).
    """
    registrar_log(f"  [SEI] Buscando ID da Diligência '{num_diligencia}' na árvore...", callback_log)

    num_normalizado = normalizar_num_diligencia(str(num_diligencia))
    partes = num_normalizado.split('/')
    digitos_num = re.sub(r'\D', '', partes[0]) if partes else ""
    num_base = str(int(digitos_num)) if digitos_num else ""
    ano_base = partes[1] if len(partes) > 1 else ""

    registrar_log(f"  [SEI] Alvo normalizado -> Número: '{num_base}', Ano: '{ano_base}'", callback_log)

    # Tentativas resilientes com re-obtenção do frame caso ocorra navegação
    for tentativa in range(12):
        try:
            time.sleep(0.6)
            
            # Reobtém a referência atual do frame a cada iteração
            frame_arvore = page.frame(name="ifrArvore")
            if not frame_arvore:
                frame_arvore = page

            # Garante que o body do frame já está acessível
            texto_total = frame_arvore.evaluate("() => document.body ? document.body.innerText : ''")
            if not texto_total:
                continue

            # Expande pastas na árvore
            try:
                frame_arvore.evaluate("""() => {
                    const icones = document.querySelectorAll("img[src*='mais'], img[src*='pasta_fechada'], a[href*='expandir'], span.infraArvoreMais");
                    icones.forEach(el => { try { el.click(); } catch(e) {} });
                }""")
            except Exception:
                pass

            # Executa a busca DOM no contexto atual do frame
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

            # Fallback regex no HTML
            html_frame = frame_arvore.content()
            padrao_html = rf"Dilig[eê]ncia[^\\(]*?0*{re.escape(num_base)}(?:/{re.escape(ano_base)})?[^\\(]*?\\((\\d{{6,10}})\\)"
            m_html = re.search(padrao_html, html_frame, re.IGNORECASE)
            if m_html:
                id_html = m_html.group(1)
                registrar_log(f"  [SEI] ID '{id_html}' capturado via regex no HTML bruto do frame.", callback_log)
                return id_html

        except Exception as ex:
            # Captura a destruição de contexto decorrente de navegações/reloads
            if "Execution context was destroyed" in str(ex) or "navigated" in str(ex):
                registrar_log(f"  [SEI] Frame 'ifrArvore' recarregando (tentativa {tentativa + 1}/12). Aguardando estabilização...", callback_log)
                time.sleep(1.0)
                continue
            registrar_log(f"  ⚠️ [SEI] Alerta temporário na leitura da árvore: {ex}", callback_log)

    registrar_log(f"  ⚠️ [SEI] Falha ao encontrar ID para a Diligência '{num_diligencia}' na árvore.", callback_log)
    return ""


def processar_fluxo_completo_sei(page, num_sei, num_diligencia, secretaria, caminho, usuario_sei, senha_sei, callback_log=None):
    """Executa a rotina completa no SEI garantindo salvamento e encaminhamento corretos."""
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

    time.sleep(1.0)

    # Identificação do ID da Diligência com expansão de pastas na árvore
    id_documento = extrair_id_diligencia_arvore(page, num_diligencia, callback_log)
    if id_documento:
        registrar_log(f"  [SEI] Diligência {num_diligencia} identificada com sucesso (ID: {id_documento}).", callback_log)
    else:
        registrar_log(f"  ⚠️ [SEI] Não foi possível localizar a Diligência {num_diligencia} na árvore lateral.", callback_log)

    registrar_log("  [SEI] Limpando linhas em branco e inserindo texto de cobrança...", callback_log)
    
    conteudo_despacho_html = f"""<p>À {secretaria}</p>
<p>Prezados,<br>
Alguma informação acerca desta demanda?</p>
<p>Peço que por gentileza nos posicione acerca da Diligência {num_diligencia}. (id. {id_documento if id_documento else '0000000'})</p>
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

        // Seleciona todo o conteúdo do editor e limpa
        const range = instance.createRange();
        range.selectNodeContents(body);
        instance.getSelection().selectRanges([range]);
        body.setHtml('');

        // Insere o texto padrão de cobrança
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

    # ----------------------------------------------------------------------
    # SALVAMENTO DO EDITOR ANTES DE ASSINAR
    # ----------------------------------------------------------------------
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

    # ----------------------------------------------------------------------
    # ASSINATURA DO DOCUMENTO
    # ----------------------------------------------------------------------
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

    # ----------------------------------------------------------------------
    # ENCAMINHAMENTO DIRETO DA TELA DO DOCUMENTO
    # ----------------------------------------------------------------------
    destino_final = caminho if caminho else secretaria
    registrar_log(f"  [SEI] Encaminhando processo para o destino: {destino_final}...", callback_log)

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

    registrar_log("  ✅ [SEI] Despacho criado, assinado e processo encaminhado!", callback_log)


def atualizar_banco_pos_cobranca(id_diligencia, usuario_app, dias_prorrogacao=5, callback_log=None):
    """Atualiza o prazo no MySQL e registra o histórico garantindo tipo inteiro nos parâmetros."""
    con = obter_conexao()
    if not con:
        return
    try:
        cur = con.cursor()

        try:
            dias_num = int(dias_prorrogacao)
        except (ValueError, TypeError):
            dias_num = 5

        try:
            cur.execute("ALTER TABLE tb_cobranca_respostas ADD COLUMN caminho VARCHAR(255) NULL AFTER orgao_secretaria")
            con.commit()
        except Exception:
            pass

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
                'Despacho SEI (Auto)', 
                %s, 
                'Despacho de Cobrança enviado via SEI', 
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
                    'Despacho SEI (Auto)', 
                    %s, 
                    'Despacho de Cobrança enviado via SEI', 
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
    """Orquestrador da automação aceitando os dias de prorrogação dinâmicos."""
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
                id_dil = item.get("id")
                secretaria = item.get("secretaria", "")
                caminho = item.get("caminho", "")
                
                num_diligencia = item.get("diligencia", "")
                if not num_diligencia:
                    num_diligencia = f"{id_dil:02d}/{datetime.now().year}"

                if not num_sei:
                    continue

                registrar_log(f"\n--- Processando ID {id_dil} (SEI: {num_sei}) ---", callback_log)

                try:
                    processar_fluxo_completo_sei(
                        page=page,
                        num_sei=num_sei,
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

                except Exception as ex:
                    registrar_log(f"❌ Falha no processamento do ID {id_dil}: {ex}", callback_log)

        finally:
            browser.close()
            registrar_log("\n[ROBÔ] Processamento finalizado.", callback_log)