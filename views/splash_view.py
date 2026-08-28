import time
import threading
import customtkinter as ctk

class SplashScreen(ctk.CTk):
    def __init__(self, callback_concluido):
        super().__init__()
        self.callback_concluido = callback_concluido

        # Remove bordas e barra de título da janela
        self.overrideredirect(True)

        # Centraliza a janela na tela
        largura, altura = 420, 260
        largura_tela = self.winfo_screenwidth()
        altura_tela = self.winfo_screenheight()
        x = (largura_tela // 2) - (largura // 2)
        y = (altura_tela // 2) - (altura // 2)
        self.geometry(f"{largura}x{altura}+{x}+{y}")

        # Estilização
        self.configure(fg_color="#1e1e1e")

        # Rótulo Principal
        ctk.CTkLabel(self, text="Gestão de Diligências", font=("Arial", 22, "bold"), text_color="white").pack(pady=(40, 5))
        ctk.CTkLabel(self, text="Iniciando aplicação...", font=("Arial", 11), text_color="#aaaaaa").pack(pady=(0, 25))

        # Barra de Progresso
        self.progress_bar = ctk.CTkProgressBar(self, width=320, height=12, corner_radius=6, progress_color="#1f538d")
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0)

        # Mensagem de Status
        self.lbl_status = ctk.CTkLabel(self, text="Aguarde...", font=("Arial", 10), text_color="#888888")
        self.lbl_status.pack(pady=5)

        # Dispara o carregamento em segundo plano (para não congelar a janela)
        threading.Thread(target=self.rotina_carregamento, daemon=True).start()

        try:
            self.iconbitmap("assets/icon.ico")
        except Exception:
            pass

    def rotina_carregamento(self):
        # Lista de rotinas/etapas de carregamento do sistema
        etapas = [
            ("Conectando ao banco de dados MySQL...", 0.25),
            ("Carregando módulos da interface...", 0.50),
            ("Verificando permissões de usuário...", 0.75),
            ("Pronto!", 1.0)
        ]

        for texto, progresso in etapas:
            time.sleep(0.4)  # Aqui entra sua lógica de inicialização real
            self.lbl_status.configure(text=texto)
            self.progress_bar.set(progresso)

        time.sleep(0.2)
        # Finaliza a splash screen na thread principal do CustomTkinter
        self.after(0, self.finalizar)

    def finalizar(self):
        self.destroy()
        if self.callback_concluido:
            self.callback_concluido()