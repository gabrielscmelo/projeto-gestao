import secrets, string
from datetime import datetime, timedelta
import customtkinter as ctk
from tkinter import messagebox
from database.connection import obter_conexao
from utils.security import gerar_hash_senha


def gerar_senha_aleatoria(n=12):
    alf = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alf) for _ in range(n))


def senha_expirada(trocar, alterada_em):
    return bool(trocar) or not alterada_em or datetime.now() - alterada_em > timedelta(days=365)


def exigir_troca_senha(parent, user_id):
    """Retorna True se a senha foi alterada; False se o usuário cancelou."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title("Troca de Senha Obrigatória")
    dlg.geometry("320x250"); dlg.transient(parent)
    dlg.after(200, dlg.grab_set)
    ok = {"v": False}

    ctk.CTkLabel(dlg, text="Defina uma nova senha", font=("Arial", 14, "bold")).pack(pady=12)
    e1 = ctk.CTkEntry(dlg, placeholder_text="Nova senha (mín. 8)", show="*", width=240); e1.pack(pady=5)
    e2 = ctk.CTkEntry(dlg, placeholder_text="Confirmar senha", show="*", width=240); e2.pack(pady=5)

    def confirmar():
        s = e1.get()
        if len(s) < 8 or s != e2.get():
            messagebox.showwarning("Aviso", "Mínimo de 8 caracteres e as senhas devem coincidir.", parent=dlg)
            return
        con = obter_conexao()
        if not con: return
        try:
            cur = con.cursor()
            cur.execute("UPDATE tb_usuarios SET senha=%s, trocar_senha=0, senha_alterada_em=NOW() WHERE id=%s",
                        (gerar_hash_senha(s), user_id))
            con.commit(); ok["v"] = True; dlg.destroy()
        finally:
            cur.close(); con.close()

    ctk.CTkButton(dlg, text="Salvar", command=confirmar, width=240).pack(pady=12)
    dlg.wait_window()
    return ok["v"]
