import pyautogui
import time

print("Posicione o mouse sobre o botão desejado no SAJ. Em 5 segundos a posição será exibida...")
time.sleep(5)
print("Coordenada capturada:", pyautogui.position())