# utils/lotacoes.py
"""
Definição centralizada das "lotações" (áreas/abas) do sistema.

Cada lotação tem uma CHAVE (usada no banco de dados, na coluna
tb_usuarios.lotacoes, como texto separado por vírgulas — ex.:
"diligencias,cobranca") e um RÓTULO amigável para exibição na tela.

Manter essa lista em um único lugar evita que usuarios_view.py,
login_view.py e main_view.py fiquem com 3 cópias divergentes do mesmo
mapeamento.
"""

# Ordem também define a ordem de exibição nas telas de cadastro e seleção.
LOTACOES = [
    ("diligencias", "Lotação Diligência"),
    ("usucapiao", "Lotação Usucapião"),
    ("cobranca", "Cobrança de Respostas"),
    ("gestao_pessoas", "Gestão de Pessoas"),
]

LOTACOES_CHAVES = [chave for chave, _ in LOTACOES]
LOTACOES_LABELS = dict(LOTACOES)


def normalizar_lotacoes(lotacoes_str):
    """
    Converte o valor bruto salvo no banco (ex.: "diligencias,usucapiao",
    None, ou "") na lista de chaves de lotação que o usuário pode acessar.

    Por retrocompatibilidade: usuários cadastrados ANTES dessa
    funcionalidade existir (coluna NULL/vazia) recebem acesso a TODAS as
    lotações, em vez de ficarem subitamente sem acesso a nada.
    """
    if not lotacoes_str:
        return list(LOTACOES_CHAVES)

    lista = [c.strip() for c in lotacoes_str.split(',') if c.strip() in LOTACOES_LABELS]
    return lista if lista else list(LOTACOES_CHAVES)


def rotulo(chave):
    """Retorna o rótulo amigável de uma chave de lotação (ou a própria chave, se desconhecida)."""
    return LOTACOES_LABELS.get(chave, chave)


def formatar_lista(lotacoes_str):
    """Formata o valor bruto do banco em texto amigável para exibição em listas/tabelas."""
    chaves = normalizar_lotacoes(lotacoes_str)
    if len(chaves) == len(LOTACOES_CHAVES):
        return "Todas"
    return ", ".join(rotulo(c) for c in chaves)