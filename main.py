# ==========================================
# MÉTODO CARDONA - AUTOMATIZACIÓN COMPLETA (VERSIÓN FINAL)
# Incluye: SMA40 correcto, Estrategias, y Cadena de Opciones (Vencimiento Viernes + OTM Ask)
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

# Tickers específicos acordados
TICKERS = ['F', 'T', 'PFE', 'VALE', 'AAL', 'BAC', 'USO', 'SOFI', 'CCL', 'NFLX']

# Zona horaria Nueva York
NY_TZ = pytz.timezone('America/New_York')

# ==========================================
# FUNCIONES AUXILIARES DE ANÁLISIS TÉCNICO
# ==========================================

def obtener_datos(ticker):
    """Obtiene datos diarios e intradía del activo"""
    try:
        stock = yf.Ticker(ticker)
        
        # Datos diarios (1 año para medias móviles largas)
        datos_diarios = stock.history(period="1y", interval="1d")
        
        # Datos horarios (2 MESES para asegurar al menos 40 velas de 1H y calcular el SMA 40 correctamente)
        datos_horarios = stock.history(period="2mo", interval="1h")
        
        if datos_diarios.empty or datos_horarios.empty:
            return None, None, stock
        
        # Calcular medias móviles en datos diarios
        datos_diarios['SMA100'] = datos_diarios['Close'].rolling(window=100).mean()
        datos_diarios['SMA200'] = datos_diarios['Close'].rolling(window=200).mean()
        
        # Calcular medias móviles en datos horarios
        datos_horarios['SMA20'] = datos_horarios['Close'].rolling(window=20).mean()
        datos_horarios['SMA40'] = datos_horarios['Close'].rolling(window=40).mean()
        
        return datos_diarios, datos_horarios, stock
    except Exception as e:
        print(f"Error obteniendo datos de {ticker}: {e}")
        return None, None, None

def es_vela_verde(candle):
    return float(candle['Close']) > float(candle['Open'])

def es_vela_roja(candle):
    return float(candle['Close']) < float(candle['Open'])

def es_martillo(candle):
    cuerpo = abs(float(candle['Close']) - float(candle['Open']))
    mecha_inferior = min(float(candle['Open']), float(candle['Close'])) - float(candle['Low'])
    mecha_superior = float(candle['High']) - max(float(candle['Open']), float(candle['Close']))
    return mecha_inferior >= (2 * cuerpo) and mecha_superior <= cuerpo

def es_hanger(candle):
    cuerpo = abs(float(candle['Close']) - float(candle['Open']))
    mecha_inferior = min(float(candle['Open']), float(candle['Close'])) - float(candle['Low'])
    mecha_superior = float(candle['High']) - max(float(candle['Open']), float(candle['Close']))
    return mecha_inferior >= (2 * cuerpo) and cuerpo <= (float(candle['High']) - float(candle['Low'])) * 0.3

def es_vela_verde_fuerte(candle):
    rango_total = float(candle['High']) - float(candle['Low'])
    cuerpo = float(candle['Close']) - float(candle['Open'])
    return cuerpo > 0 and (cuerpo / rango_total) >= 0.6 if rango_total > 0 else False

def detectar_canal_bajista(datos, num_velas=10):
    if len(datos) < num_velas:
        return False, None, None
    ultimas_velas = datos.tail(num_velas)
    maximos = ultimas_velas['High'].rolling(window=3).max().dropna()
    if len(maximos) < 3:
        return False, None, None
    techo_actual = maximos.iloc[-1]
    techo_anterior = maximos.iloc[-3]
    if techo_actual < techo_anterior:
        return True, float(techo_actual), None
    return False, None, None

# ==========================================
# LÓGICA DE OPCIONES (VIERNES + OTM)
# ==========================================

def obtener_datos_opciones(stock, precio_actual):
    """Obtiene el vencimiento del viernes y los strikes OTM con su precio Ask"""
    try:
        expiraciones = stock.options
        if not expiraciones:
            return "N/A", "N/A", "N/A", "N/A", "N/A"
        
        hoy = datetime.now().date()
        fecha_vencimiento = None
        
        # Buscar el próximo viernes (o el viernes de esta semana si aún no ha pasado)
        for exp in expiraciones:
            try:
                exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                if exp_date >= hoy and exp_date.weekday() == 4:  # 4 es Viernes
                    fecha_vencimiento = exp
                    break
            except:
                continue
        
        # Si no hay un viernes exacto, tomar la primera expiración futura disponible
        if not fecha_vencimiento:
            for exp in expiraciones:
                try:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                    if exp_date >= hoy:
                        fecha_vencimiento = exp
                        break
                except:
                    continue
        
        if not fecha_vencimiento:
            return "N/A", "N/A", "N/A", "N/A", "N/A"
        
        chain = stock.option_chain(fecha_vencimiento)
        calls = chain.calls
        puts = chain.puts
        
        # OTM Call: Strike > precio_actual (el más cercano)
        calls_otm = calls[calls['strike'] > precio_actual]
        if not calls_otm.empty:
            call_row = calls_otm.iloc[0]
            strike_call = float(call_row['strike'])
            call_ask = float(call_row['ask']) if pd.notna(call_row['ask']) else 0.05
        else:
            strike_call = "N/A"
            call_ask = "N/A"
            
        # OTM Put: Strike < precio_actual (el más cercano)
        puts_otm = puts[puts['strike'] < precio_actual]
        if not puts_otm.empty:
            put_row = puts_otm.iloc[0]
            strike_put = float(put_row['strike'])
            put_ask = float(put_row['ask']) if pd.notna(put_row['ask']) else 0.05
        else:
            strike_put = "N/A"
            put_ask = "N/A"
            
        return str(fecha_vencimiento), strike_call, call_ask, strike_put, put_ask
        
    except Exception as e:
        print(f"Error obteniendo opciones para {stock.ticker}: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A"

# ==========================================
# EVALUACIÓN DE ESTRATEGIAS (MÉTODO CARDONA)
# ==========================================

def evaluar_estrategias(ticker, datos_diarios, datos_horarios):
    estrategias_call = []
    estrategias_put = []
    
    # --- DATOS BASE ---
    ultimo_diario = datos_diarios.iloc[-1]
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    sma100_d = float(ultimo_diario['SMA100'])
    sma200_d = float(ultimo_diario['SMA200'])
    
    # Zona Diario
    en_piso_fuerte = (abs(precio_actual - sma100_d) / sma100_d <= 0.02) or (abs(precio_actual - sma200_d) / sma200_d <= 0.02)
    zona_diario = "En Piso Fuerte" if en_piso_fuerte else "Fuera de Piso"
    
    # Datos Horarios
    sma40_h = float(datos_horarios['SMA40'].iloc[-1]) if len(datos_horarios) >= 40 else precio_actual
    tendencia_1h = "Alcista" if precio_actual > sma40_h else "Bajista"
    distancia_pm40 = ((precio_actual - sma40_h) / sma40_h) * 100
    
    # --- EVALUACIÓN CALL ---
    # 1. PM 40 - El PM20 debe estar por encima del PM40 + caída que toque PM40
    if len(datos_diarios) >= 40:
        pm20_d = float(datos_diarios['Close'].rolling(20).mean().iloc[-1])
        pm40_d = float(datos_diarios['SMA100'].iloc[-1])  # Usamos SMA100 como referencia
        if pm20_d > pm40_d:
            if len(datos_horarios) >= 2:
                if float(datos_horarios['Close'].iloc[-1]) < float(datos_horarios['Close'].iloc[-2]):
                    if abs(distancia_pm40) <= 2.0:
                        estrategias_call.append("PM 40")
                    
    # 2. Caída Normal / Fuerte - Tendencia alcista + caída
    if tendencia_1h == "Alcista" and len(datos_horarios) >= 2:
        caida_pct = ((float(datos_horarios['Close'].iloc[-2]) - float(datos_horarios['Close'].iloc[-1])) / float(datos_horarios['Close'].iloc[-2])) * 100
        caida_puntos = float(datos_horarios['Close'].iloc[-2]) - float(datos_horarios['Close'].iloc[-1])
        if caida_pct > 0:
            if caida_pct > 1.5 or caida_puntos >= 5:
                estrategias
