import customtkinter as ctk
from database.connection import inicializar_banco_dados
from views.login_view import TelaLogin
from views.main_view import Aplicativo


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

if __name__ == "__main__":
    # Garante a criação de tabelas e do admin inicial
    inicializar_banco_dados()

    while True:
        login_app = TelaLogin()
        login_app.mainloop()

        if not login_app.usuario_logado:
            break

        app = Aplicativo(
            usuario_atual=login_app.usuario_logado,
            perfil_atual=login_app.perfil_logado,
            nome_atual=login_app.nome_logado,
            lotacao_selecionada=login_app.lotacao_selecionada,
            lotacoes_permitidas=login_app.lotacoes_permitidas
        )
        app.mainloop()

        if not app.deseja_fazer_logout:
            break