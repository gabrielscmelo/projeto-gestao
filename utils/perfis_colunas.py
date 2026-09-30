import json
import os
from tkinter import Menu, simpledialog, messagebox

_ARQ = os.path.join(os.path.expanduser("~"), ".sistema_gestao_perfis_colunas.json")


def _ler():
    try:
        with open(_ARQ, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _gravar(d):
    try:
        with open(_ARQ, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except OSError as e:
        messagebox.showerror("Perfis", f"Falha ao salvar o perfil:\n{e}")


def _preencher(tree):
    """Distribui a largura livre entre as colunas visíveis, preenchendo a tabela."""
    tree.update_idletasks()
    orig = tree.__dict__.setdefault("_larg_orig", {c: int(tree.column(c, "width")) for c in tree["columns"]})
    vis = tree["displaycolumns"]
    vis = list(tree["columns"]) if "#all" in vis else list(vis)
    if not vis:
        return
    extra = tree.winfo_width() - sum(orig[c] for c in vis)
    for c in vis:
        tree.column(c, width=orig[c] + max(extra, 0) // len(vis), stretch=True)


def adicionar_perfis(menu, master, chave, col_vars, aplicar, tree=None):
    """Adiciona ao menu de colunas: salvar, carregar e excluir perfis.
    chave: nome da aba; col_vars: {coluna: BooleanVar}; aplicar: função que reaplica a visibilidade."""
    m_car, m_exc = Menu(menu, tearoff=0), Menu(menu, tearoff=0)

    def recarregar():
        m_car.delete(0, "end")
        m_exc.delete(0, "end")
        perfis = _ler().get(chave, {})
        for n in perfis:
            m_car.add_command(label=n, command=lambda n=n: carregar(n))
            m_exc.add_command(label=n, command=lambda n=n: excluir(n))
        if not perfis:
            for m in (m_car, m_exc):
                m.add_command(label="(nenhum perfil)", state="disabled")

    def carregar(n):
        vis = _ler().get(chave, {}).get(n, [])
        for c, v in col_vars.items():
            v.set(c in vis)
        aplicar()
        if tree is not None:
            master.after(50, lambda: _preencher(tree))

    def salvar():
        n = simpledialog.askstring("Salvar perfil", "Nome do perfil:", parent=master)
        if not n or not n.strip():
            return
        d = _ler()
        d.setdefault(chave, {})[n.strip()] = [c for c, v in col_vars.items() if v.get()]
        _gravar(d)
        recarregar()

    def excluir(n):
        d = _ler()
        d.get(chave, {}).pop(n, None)
        _gravar(d)
        recarregar()

    menu.add_separator()
    menu.add_command(label="💾 Salvar perfil atual...", command=salvar)
    menu.add_cascade(label="📂 Carregar perfil", menu=m_car)
    menu.add_cascade(label="🗑 Excluir perfil", menu=m_exc)
    recarregar()