import customtkinter as ctk
from database.connection import inicializar_banco_dados
from views.splash_view import SplashScreen
from views.login_view import TelaLogin
from views.main_view import Aplicativo

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

def iniciar_aplicacao():
    def abrir_login():
        app_login = TelaLogin()
        app_login.mainloop()

        # Quando a TelaLogin fechar (autenticado com sucesso)
        if app_login.usuario_logado:
            app_main = Aplicativo(
                usuario_atual=app_login.usuario_logado,
                perfil_atual=app_login.perfil_logado,
                nome_atual=app_login.nome_logado
            )
            app_main.mainloop()

    # Exibe a tela de carregamento inicial
    splash = SplashScreen(callback_concluido=abrir_login)
    splash.mainloop()

if __name__ == "__main__":
    iniciar_aplicacao()

if __name__ == "__main__":
    # Garante a criação de tabelas e do admin inicial
    inicializar_banco_dados()

    while True:
        login_app = TelaLogin()
        login_app.mainloop()

        if login_app.usuario_logado:
            app = Aplicativo(
                usuario_atual=login_app.usuario_logado,
                perfil_atual=login_app.perfil_logado,
                nome_atual=login_app.nome_logado
            )
            app.mainloop()

            if not app.deseja_fazer_logout:
                break
        else:
            break