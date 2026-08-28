import re
from datetime import datetime

def aplicar_mascara_data_auto(event):
    widget = event.widget
    texto = re.sub(r'\D', '', widget.get())
    if len(texto) > 8: 
        texto = texto[:8]
    fmt = f"{texto[:2]}/{texto[2:4]}/{texto[4:]}" if len(texto) > 4 else (f"{texto[:2]}/{texto[2:]}" if len(texto) > 2 else texto)
    widget.delete(0, 'end')
    widget.insert(0, fmt)

def calcular_status_diligencia(data_prazo_str):
    if not data_prazo_str or data_prazo_str == 'None': 
        return 'Sem Prazo', 0
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        data_limite = datetime.strptime(str(data_prazo_str), '%Y-%m-%d')
    except ValueError:
        return 'Erro: Data inválida', 0
    dias = (data_limite - hoje).days
    return ('No prazo' if data_limite >= hoje else 'Em atraso'), dias

def formatar_e_validar_data(data_str):
    if not data_str or not data_str.strip():
        return None, "O campo de data não pode estar vazio."
    if len(data_str) == 10 and data_str.count('/') == 2:
        try:
            return datetime.strptime(data_str, "%d/%m/%Y").strftime("%Y-%m-%d"), None
        except ValueError:
            return None, "Data inválida."
    return None, "Formato incorreto. Use DD/MM/AAAA."

def formatar_saj(valor):
    if not valor or not valor.strip(): 
        return ""
    digitos = re.sub(r'\D', '', valor)
    if not digitos: 
        return ""
    if len(digitos) >= 6: 
        return f"{digitos[:4]}.{digitos[4:6]}.{digitos[6:12].zfill(6)}"
    elif len(digitos) > 4: 
        return f"{digitos[:4]}.01.{digitos[4:].zfill(6)}"
    return f"{digitos.zfill(4)}.01.000000"

def formatar_sei(valor):
    if not valor or not valor.strip(): 
        return ""
    digitos = re.sub(r'\D', '', valor)
    if not digitos: 
        return ""
    if len(digitos) == 14: 
        return f"{digitos[:2]}.{digitos[2:8]}/{digitos[8:12]}-{digitos[12:]}"
    elif len(digitos) > 8: 
        return f"{digitos[:2]}.{digitos[2:-6].zfill(6)[:6]}/{digitos[-6:-2]}-{digitos[-2:]}"
    return digitos

def formatar_diligencia(valor):
    if not valor or not valor.strip(): 
        return ""
    ano_atual = str(datetime.now().year)
    if '/' in valor:
        partes = valor.split('/')
        num = re.sub(r'\D', '', partes[0])
        ano = re.sub(r'\D', '', partes[1]) if len(partes) > 1 and partes[1] else ano_atual
        return f"{num.zfill(4)}/{ano}" if num else ""
    digitos = re.sub(r'\D', '', valor)
    if not digitos: 
        return ""
    if len(digitos) > 4 and len(digitos) <= 8: 
        return f"{digitos[:-4].zfill(4)}/{digitos[-4:]}"
    return f"{digitos.zfill(4)}/{ano_atual}"