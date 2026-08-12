# ==========================================
# MÉTODO CARDONA - AUTOMATIZACIÓN COMPLETA
# 7 Estrategias CALL + 4 Estrategias PUT
# ==========================================

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import pytz

# ==========================================
# CONFIGURACIÓN INICIAL
# ==========================================
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Autenticación con Google Sheets
credentials_json = os.environ['GOOGLE_CREDENTIALS']
credentials_info = json.loads(credentials_json)
creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
gc = gspread.authorize(creds)

# ID del Google Sheet
SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'

# Activos a analizar
TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'AMD', 'NFLX', 'SPY', 'QQQ', 'IWM', 'DIA']

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================

def obtener_datos(ticker):
    """Obtiene datos diarios e intradía del activo"""
    try:
        stock = yf.Ticker(ticker)
        
        # Datos diarios (1 año para medias móviles largas)
        datos_diarios = stock.history(period="1y", interval="1d")
        
        # Datos horarios (5 días para análisis intradía)
        datos_horarios = stock.history(period="5d", interval="1h")
        
        if datos_diarios.empty or datos_horarios.empty:
            return None, None
        
        # Calcular medias móviles en datos diarios
        datos_diarios['PM20'] = datos_diarios['Close'].rolling(window=20).mean()
        datos_diarios['PM40'] = datos_diarios['Close'].rolling(window=40).mean()
        datos_diarios['PM100'] = datos_diarios['Close'].rolling(window=100).mean()
        datos_diarios['PM200'] = datos_diarios['Close'].rolling(window=200).mean()
        
        # Calcular medias móviles en datos horarios
        datos_horarios['PM20'] = datos_horarios['Close'].rolling(window=20).mean()
        datos_horarios['PM40'] = datos_horarios['Close'].rolling(window=40).mean()
        
        return datos_diarios, datos_horarios
    except Exception as e:
        print(f"Error obteniendo datos de {ticker}: {e}")
        return None, None

def es_vela_verde(candle):
    """Verifica si la vela es verde"""
    return candle['Close'] > candle['Open']

def es_vela_roja(candle):
    """Verifica si la vela es roja"""
    return candle['Close'] < candle['Open']

def es_martillo(candle):
    """Detecte si es vela tipo Martillo/Hammer"""
    cuerpo = abs(candle['Close'] - candle['Open'])
    mecha_inferior = min(candle['Open'], candle['Close']) - candle['Low']
    mecha_superior = candle['High'] - max(candle['Open'], candle['Close'])
    
    # Martillo: mecha inferior al menos 2 veces el cuerpo, mecha superior pequeña
    return mecha_inferior >= (2 * cuerpo) and mecha_superior <= cuerpo

def es_hanger(candle):
    """Detecte si es vela tipo Hanger (martillo invertido en tendencia alcista)"""
    cuerpo = abs(candle['Close'] - candle['Open'])
    mecha_inferior = min(candle['Open'], candle['Close']) - candle['Low']
    mecha_superior = candle['High'] - max(candle['Open'], candle['Close'])
    
    # Hanger: mecha inferior larga, cuerpo pequeño en la parte superior
    return mecha_inferior >= (2 * cuerpo) and cuerpo <= (candle['High'] - candle['Low']) * 0.3

def es_vela_verde_fuerte(candle):
    """Verifica si es una vela verde fuerte (cuerpo grande)"""
    rango_total = candle['High'] - candle['Low']
    cuerpo = candle['Close'] - candle['Open']
    
    # Cuerpo debe ser al menos 60% del rango total
    return cuerpo > 0 and (cuerpo / rango_total) >= 0.6 if rango_total > 0 else False

def calcular_porcentaje_caida(candle_anterior, candle_actual):
    """Calcula el porcentaje de caída entre dos velas"""
    if candle_anterior['Close'] == 0:
        return 0
    return ((candle_anterior['Close'] - candle_actual['Close']) / candle_anterior['Close']) * 100

def detectar_canal_bajista(datos, num_velas=10):
    """Detecta si hay un canal bajista formado"""
    if len(datos) < num_velas:
        return False, None, None
    
    ultimas_velas = datos.tail(num_velas)
    
    # Calcular máximos y mínimos del canal
    maximos = ultimas_velas['High'].rolling(window=3).max().dropna()
    minimos = ultimas_velas['Low'].rolling(window=3).min().dropna()
    
    if len(maximos) < 3 or len(minimos) < 3:
        return False, None, None
    
    # Verificar tendencia bajista (máximos decrecientes)
    techo_actual = maximos.iloc[-1]
    techo_anterior = maximos.iloc[-3] if len(maximos) >= 3 else techo_actual
    
    piso_actual = minimos.iloc[-1]
    
    # Canal bajista: máximos decrecientes
    if techo_actual < techo_anterior:
        return True, techo_actual, piso_actual
    
    return False, None, None

# ==========================================
# ESTRATEGIAS CALL
# ==========================================

def estrategia_pm40(datos_diarios, datos_horarios):
    """PM 40 - Promedio Móvil de 40"""
    if datos_diarios is None or datos_horarios is None or len(datos_diarios) < 40:
        return False
    
    ultimo_diario = datos_diarios.iloc[-1]
    
    # PM20 debe estar por encima del PM40
    if ultimo_diario['PM20'] <= ultimo_diario['PM40']:
        return False
    
    # Verificar caída en datos horarios
    if len(datos_horarios) < 2:
        return False
    
    vela_actual = datos_horarios.iloc[-1]
    vela_anterior = datos_horarios.iloc[-2]
    
    # Debe haber una caída
    if vela_actual['Close'] >= vela_anterior['Close']:
        return False
    
    # Debe tocar o acercarse al PM40 (dentro del 2%)
    pm40_horario = ultimo_diario['PM40']
    distancia_pm40 = abs(vela_actual['Close'] - pm40_horario) / pm40_horario * 100
    
    return distancia_pm40 <= 2

def estrategia_caida(datos_diarios, datos_horarios):
    """Caída Normal y Caída Fuerte"""
    if datos_diarios is None or datos_horarios is None or len(datos_horarios) < 2:
        return False, ""
    
    # Verificar tendencia alcista (datos diarios)
    if len(datos_diarios) < 20:
        return False, ""
    
    pm20_actual = datos_diarios['PM20'].iloc[-1]
    pm20_anterior = datos_diarios['PM20'].iloc[-5] if len(datos_diarios) >= 25 else pm20_actual
    
    if pm20_actual <= pm20_anterior:
        return False, ""
    
    # Calcular caída
    vela_actual = datos_horarios.iloc[-1]
    vela_anterior = datos_horarios.iloc[-2]
    
    porcentaje_caida = calcular_porcentaje_caida(vela_anterior, vela_actual)
    caida_puntos = vela_anterior['Close'] - vela_actual['Close']
    
    if porcentaje_caida <= 0:
        return False, ""
    
    # Clasificar caída
    if porcentaje_caida > 1.5 or caida_puntos >= 5:
        return True, "CAIDA_FUERTE"
    elif porcentaje_caida > 0:
        return True, "CAIDA_NORMAL"
    
    return False, ""

def estrategia_ruptura_canal_bajista(datos_horarios):
    """Ruptura de Canal Bajista"""
    if datos_horarios is None or len(datos_horarios) < 10:
        return False
    
    # Detectar canal bajista
    hay_canal, techo_canal, piso_canal = detectar_canal_bajista(datos_horarios)
    
    if not hay_canal or techo_canal is None:
        return False
    
    # Verificar vela actual (debe ser verde fuerte o martillo)
    vela_actual = datos_horarios.iloc[-1]
    
    if not (es_vela_verde_fuerte(vela_actual) or es_martillo(vela_actual)):
        return False
    
    # Debe romper el techo del canal
    if vela_actual['Close'] <= techo_canal:
        return False
    
    return True

def estrategia_gap_al_alza(datos_diarios, datos_horarios):
    """Gap al Alza"""
    if datos_diarios is None or datos_horarios is None or len(datos_horarios) < 2:
        return False
    
    # Verificar gap (salto en el precio)
    if len(datos_diarios) < 2:
        return False
    
    cierre_anterior = datos_diarios['Close'].iloc[-2]
    apertura_hoy = datos_horarios['Open'].iloc[0]
    
    # Debe haber gap alcista
    if apertura_hoy <= cierre_anterior:
        return False
    
    # Las dos primeras velas deben ser verdes (o primera roja y segunda verde fuerte)
    if len(datos_horarios) < 2:
        return False
    
    vela1 = datos_horarios.iloc[0]
    vela2 = datos_horarios.iloc[1]
    
    condicion_velas = (
        (es_vela_verde(vela1) and es_vela_verde(vela2)) or
        (es_vela_roja(vela1) and es_vela_verde_fuerte(vela2))
    )
    
    if not condicion_velas:
        return False
    
    return True

def estrategia_gap_bajista_al_alza(datos_diarios, datos_horarios):
    """Gap Bajista al Alza"""
    if datos_diarios is None or datos_horarios is None or len(datos_horarios) < 2:
        return False
    
    # El mercado abre abajo respecto al cierre anterior
    if len(datos_diarios) < 2:
        return False
    
    cierre_anterior = datos_diarios['Close'].iloc[-2]
    apertura_hoy = datos_horarios['Open'].iloc[0]
    
    if apertura_hoy >= cierre_anterior:
        return False
    
    # Las dos primeras velas deben ser verdes o primera roja y segunda verde fuerte
    if len(datos_horarios) < 2:
        return False
    
    vela1 = datos_horarios.iloc[0]
    vela2 = datos_horarios.iloc[1]
    
    condicion_velas = (
        (es_vela_verde(vela1) and es_vela_verde(vela2)) or
        (es_vela_roja(vela1) and es_vela_verde_fuerte(vela2))
    )
    
    if not condicion_velas:
        return False
    
    return True

def estrategia_piso_fuerte(datos_diarios, datos_horarios):
    """Piso Fuerte"""
    if datos_diarios is None or datos_horarios is None:
        return False
    
    # Análisis en diario: PM100 sobre PM200
    if len(datos_diarios) < 200:
        return False
    
    ultimo_diario = datos_diarios.iloc[-1]
    
    if ultimo_diario['PM100'] <= ultimo_diario['PM200']:
        return False
    
    # Caída que toque PM100 o se acerque a PM200
    precio_actual = ultimo_diario['Close']
    pm100 = ultimo_diario['PM100']
    pm200 = ultimo_diario['PM200']
    
    toca_pm100 = abs(precio_actual - pm100) / pm100 * 100 <= 2
    acerca_pm200 = abs(precio_actual - pm200) / pm200 * 100 <= 3
    
    if not (toca_pm100 or acerca_pm200):
        return False
    
    # Análisis en hora: canal bajista y ruptura con vela verde
    if len(datos_horarios) < 10:
        return False
    
    hay_canal, techo_canal, _ = detectar_canal_bajista(datos_horarios)
    
    if not hay_canal:
        return False
    
    vela_actual = datos_horarios.iloc[-1]
    
    # Vela verde rompe canal
    if not es_vela_verde_fuerte(vela_actual):
        return False
    
    if vela_actual['Close'] <= techo_canal:
        return False
    
    return True

def estrategia_primer_gap_al_alza(datos_diarios, datos_horarios):
    """Primer Gap al Alza"""
    if datos_diarios is None or datos_horarios is None:
        return False
    
    # Debe haber una caída previa
    if len(datos_horarios) < 2:
        return False
    
    # Verificar zona de piso fuerte (PM100, PM200)
    if len(datos_diarios) < 200:
        return False
    
    ultimo_diario = datos_diarios.iloc[-1]
    precio_actual = ultimo_diario['Close']
    pm100 = ultimo_diario['PM100']
    pm200 = ultimo_diario['PM200']
    
    en_zona_piso = precio_actual <= pm100 * 1.05 or precio_actual <= pm200 * 1.03
    
    if not en_zona_piso:
        return False
    
    # Primera vela debe ser verde (SIN EXCEPCIONES)
    if len(datos_horarios) < 1:
        return False
    
    primera_vela = datos_horarios.iloc[0]
    
    if not es_vela_verde(primera_vela):
        return False
    
    # Verificar volumen (ejemplo: 20 millones para SPY)
    volumen_minimo = 20000000 if 'SPY' in str(datos_horarios) else 1000000
    if primera_vela['Volume'] < volumen_minimo:
        return False
    
    return True

# ==========================================
# ESTRATEGIAS PUT
# ==========================================

def estrategia_primera_vela_roja(datos_horarios):
    """Primera Vela Roja"""
    if datos_horarios is None or len(datos_horarios) < 1:
        return False
    
    # La primera vela del día (9:30-10:00) debe ser roja
    primera_vela = datos_horarios.iloc[0]
    
    return es_vela_roja(primera_vela)

def estrategia_ruptura_piso_gap(datos_horarios):
    """Ruptura del Piso del Gap"""
    if datos_horarios is None or len(datos_horarios) < 2:
        return False
    
    # Primera vela debe ser verde
    primera_vela = datos_horarios.iloc[0]
    
    if not es_vela_verde(primera_vela):
        return False
    
    # El piso del gap es el mínimo de la primera vela
    piso_gap = primera_vela['Low']
    
    # Esperar que una vela rompa el piso (desde las 10:00 en adelante)
    if len(datos_horarios) < 2:
        return False
    
    vela_actual = datos_horarios.iloc[-1]
    
    # Vela roja que rompa el piso
    if not es_vela_roja(vela_actual):
        return False
    
    if vela_actual['Low'] >= piso_gap:
        return False
    
    return True

def estrategia_modelo_4_pasos(datos_horarios):
    """Modelo de los 4 Pasos"""
    if datos_horarios is None or len(datos_horarios) < 5:
        return False
    
    # Estar en canal bajista
    hay_canal, techo_canal, piso_canal = detectar_canal_bajista(datos_horarios)
    
    if not hay_canal or techo_canal is None or piso_canal is None:
        return False
    
    # Verificar secuencia: vela verde intenta romper techo, es borrada por roja
    if len(datos_horarios) < 3:
        return False
    
    vela_verde = datos_horarios.iloc[-3]
    vela_roja_borrado = datos_horarios.iloc[-2]
    vela_roja_ruptura = datos_horarios.iloc[-1]
    
    # Vela verde intenta romper
    if not es_vela_verde(vela_verde):
        return False
    
    # Vela roja borra el intento
    if not es_vela_roja(vela_roja_borrado):
        return False
    
    if vela_roja_borrado['Close'] >= vela_verde['Close']:
        return False
    
    # Vela roja rompe piso interno
    piso_interno = (techo_canal + piso_canal) / 2
    
    if not es_vela_roja(vela_roja_ruptura):
        return False
    
    if vela_roja_ruptura['Close'] >= piso_interno:
        return False
    
    return True

def estrategia_hanger_diario(datos_diarios):
    """Hanger en Diario"""
    if datos_diarios is None or len(datos_diarios) < 1:
        return False
    
    # Verificar última vela diaria
    ultima_vela = datos_diarios.iloc[-1]
    
    # Debe ser tipo Hanger
    if not es_hanger(ultima_vela):
        return False
    
    # Verificar tendencia alcista previa
    if len(datos_diarios) < 20:
        return False
    
    pm20_actual = datos_diarios['PM20'].iloc[-1]
    pm20_anterior = datos_diarios['PM20'].iloc[-10] if len(datos_diarios) >= 30 else pm20_actual
    
    if pm20_actual <= pm20_anterior:
        return False
    
    return True

# ==========================================
# FUNCIÓN PRINCIPAL DE ANÁLISIS
# ==========================================

def analizar_activo(ticker):
    """Analiza todas las estrategias para un activo"""
    print(f"\nAnalizando {ticker}...")
    
    datos_diarios, datos_horarios = obtener_datos(ticker)
    
    if datos_diarios is None or datos_horarios is None:
        return None
    
    resultados = {
        'ticker': ticker,
        'fecha': datetime.now().strftime('%Y-%m-%d'),
        'hora_actual': datetime.now().strftime('%H:%M'),
        'precio_actual': datos_horarios['Close'].iloc[-1],
        'estrategias_call': [],
        'estrategias_put': []
    }
    
    # ===== ESTRATEGIAS CALL =====
    
    if estrategia_pm40(datos_diarios, datos_horarios):
        resultados['estrategias_call'].append('PM_40')
    
    caida_activa, tipo_caida = estrategia_caida(datos_diarios, datos_horarios)
    if caida_activa:
        resultados['estrategias_call'].append(tipo_caida)
    
    if estrategia_ruptura_canal_bajista(datos_horarios):
        resultados['estrategias_call'].append('RUPTURA_CANAL_BAJISTA')
    
    if estrategia_gap_al_alza(datos_diarios, datos_horarios):
        resultados['estrategias_call'].append('GAP_AL_ALZA')
    
    if estrategia_gap_bajista_al_alza(datos_diarios, datos_horarios):
        resultados['estrategias_call'].append('GAP_BAJISTA_AL_ALZA')
    
    if estrategia_piso_fuerte(datos_diarios, datos_horarios):
        resultados['estrategias_call'].append('PISO_FUERTE')
    
    if estrategia_primer_gap_al_alza(datos_diarios, datos_horarios):
        resultados['estrategias_call'].append('PRIMER_GAP_AL_ALZA')
    
    # ===== ESTRATEGIAS PUT =====
    
    if estrategia_primera_vela_roja(datos_horarios):
        resultados['estrategias_put'].append('PRIMERA_VELA_ROJA')
    
    if estrategia_ruptura_piso_gap(datos_horarios):
        resultados['estrategias_put'].append('RUPTURA_PISO_GAP')
    
    if estrategia_modelo_4_pasos(datos_horarios):
        resultados['estrategias_put'].append('MODELO_4_PASOS')
    
    if estrategia_hanger_diario(datos_diarios):
        resultados['estrategias_put'].append('HANGER_DIARIO')
    
    return resultados

# ==========================================
# GUARDAR EN GOOGLE SHEETS
# ==========================================

def guardar_en_sheet(resultados):
    """Guarda los resultados en Google Sheets"""
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1
        
        # Limpiar hoja
        worksheet.clear()
        
        # Headers
        headers = [
            'Ticker', 'Fecha', 'Hora', 'Precio Actual',
            'Estrategias CALL', 'Estrategias PUT', 'Total CALL', 'Total PUT',
            'Señal', 'Timestamp'
        ]
        worksheet.append_row(headers)
        
        # Datos
        for resultado in resultados:
            if resultado is None:
                continue
            
            estrategias_call = ', '.join(resultado['estrategias_call']) if resultado['estrategias_call'] else 'Ninguna'
            estrategias_put = ', '.join(resultado['estrategias_put']) if resultado['estrategias_put'] else 'Ninguna'
            
            total_call = len(resultado['estrategias_call'])
            total_put = len(resultado['estrategias_put'])
            
            # Determinar señal
            if total_call > total_put:
                senal = 'CALL'
            elif total_put > total_call:
                senal = 'PUT'
            else:
                senal = 'NEUTRO'
            
            worksheet.append_row([
                resultado['ticker'],
                resultado['fecha'],
                resultado['hora_actual'],
                round(resultado['precio_actual'], 2),
                estrategias_call,
                estrategias_put,
                total_call,
                total_put,
                senal,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        print(f"\n✅ Datos guardados en Google Sheets. Total: {len(resultados)} activos analizados")
        return True
        
    except Exception as e:
        print(f"\n❌ Error al guardar en Google Sheets: {e}")
        import traceback
        traceback.print_exc()
        return False

# ==========================================
# FUNCIÓN MAIN
# ==========================================

def main():
    """Función principal"""
    print(f"\n{'='*60}")
    print(f"MÉTODO CARDONA - Análisis de Estrategias")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    resultados = []
    
    for ticker in TICKERS:
        resultado = analizar_activo(ticker)
        if resultado:
            resultados.append(resultado)
    
    # Guardar en Google Sheets
    guardar_en_sheet(resultados)
    
    # Resumen
    print(f"\n{'='*60}")
    print("RESUMEN DE ESTRATEGIAS DETECTADAS:")
    print(f"{'='*60}")
    
    for resultado in resultados:
        if resultado['estrategias_call'] or resultado['estrategias_put']:
            print(f"\n{resultado['ticker']} - ${resultado['precio_actual']:.2f}")
            if resultado['estrategias_call']:
                print(f"  ✅ CALL: {', '.join(resultado['estrategias_call'])}")
            if resultado['estrategias_put']:
                print(f"  🔻 PUT: {', '.join(resultado['estrategias_put'])}")

if __name__ == '__main__':
    main()
