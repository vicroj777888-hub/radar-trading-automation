# ==========================================
# MÉTODO CARDONA - AUTOMATIZACIÓN COMPLETA (VERSIÓN RESTAURADA Y CORREGIDA)
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
    # 1. PM 40
    if float(datos_diarios['SMA100'].iloc[-1]) > float(datos_diarios['SMA200'].iloc[-1]):
        if len(datos_horarios) >= 2:
            if float(datos_horarios['Close'].iloc[-1]) < float(datos_horarios['Close'].iloc[-2]):
                if abs(distancia_pm40) <= 2.0:
                    estrategias_call.append("PM 40")
                    
    # 2. Caída Normal / Fuerte
    if tendencia_1h == "Alcista" and len(datos_horarios) >= 2:
        caida_pct = ((float(datos_horarios['Close'].iloc[-2]) - float(datos_horarios['Close'].iloc[-1])) / float(datos_horarios['Close'].iloc[-2])) * 100
        if caida_pct > 0:
            if caida_pct > 1.5 or (float(datos_horarios['Close'].iloc[-2]) - float(datos_horarios['Close'].iloc[-1])) >= 5:
                estrategias_call.append("Caída Fuerte")
            else:
                estrategias_call.append("Caída Normal")
                
    # 3. Ruptura Canal Bajista
    hay_canal, techo, _ = detectar_canal_bajista(datos_horarios)
    ultima_vela = datos_horarios.iloc[-1]
    if hay_canal and (es_vela_verde_fuerte(ultima_vela) or es_martillo(ultima_vela)):
        if float(ultima_vela['Close']) > techo:
            estrategias_call.append("Ruptura Canal Bajista")
            
    # 4. Gap al Alza
    if len(datos_diarios) >= 2 and len(datos_horarios) >= 2:
        cierre_ayer = float(datos_diarios['Close'].iloc[-2])
        apertura_hoy = float(datos_horarios['Open'].iloc[0])
        if apertura_hoy > cierre_ayer:
            v1, v2 = datos_horarios.iloc[0], datos_horarios.iloc[1]
            if (es_vela_verde(v1) and es_vela_verde(v2)) or (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
                estrategias_call.append("Gap al Alza")
                
    # 5. Gap Bajista al Alza
    if len(datos_diarios) >= 2 and len(datos_horarios) >= 2:
        cierre_ayer = float(datos_diarios['Close'].iloc[-2])
        apertura_hoy = float(datos_horarios['Open'].iloc[0])
        if apertura_hoy < cierre_ayer:
            v1, v2 = datos_horarios.iloc[0], datos_horarios.iloc[1]
            if (es_vela_verde(v1) and es_vela_verde(v2)) or (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
                estrategias_call.append("Gap Bajista al Alza")
                
    # 6. Piso Fuerte
    if en_piso_fuerte and hay_canal and es_vela_verde_fuerte(ultima_vela) and float(ultima_vela['Close']) > techo:
        estrategias_call.append("Piso Fuerte")
        
    # 7. Primer Gap al Alza
    if en_piso_fuerte and len(datos_horarios) >= 1:
        v1 = datos_horarios.iloc[0]
        if es_vela_verde(v1) and float(v1['Volume']) >= 1000000: # Volumen ajustable
            estrategias_call.append("Primer Gap al Alza")

    # --- EVALUACIÓN PUT ---
    # 1. Primera Vela Roja
    if len(datos_horarios) >= 1:
        v1 = datos_horarios.iloc[0]
        if es_vela_roja(v1) and not en_piso_fuerte:
            estrategias_put.append("Primera Vela Roja")
            
    # 2. Ruptura del Piso del Gap
    if len(datos_horarios) >= 2:
        v1 = datos_horarios.iloc[0]
        if es_vela_verde(v1):
            piso_gap = float(v1['Low'])
            if float(ultima_vela['Close']) < piso_gap and es_vela_roja(ultima_vela):
                estrategias_put.append("Ruptura Piso del Gap")
                
    # 3. Modelo de los 4 Pasos
    if hay_canal and len(datos_horarios) >= 3:
        v_verde = datos_horarios.iloc[-3]
        v_roja_borra = datos_horarios.iloc[-2]
        v_roja_rompe = datos_horarios.iloc[-1]
        if es_vela_verde(v_verde) and es_vela_roja(v_roja_borra) and es_vela_roja(v_roja_rompe):
            if float(v_roja_borra['Close']) < float(v_verde['Close']):
                estrategias_put.append("Modelo 4 Pasos")
                
    # 4. Hanger en Diario
    if es_hanger(ultimo_diario) and not en_piso_fuerte:
        estrategias_put.append("Hanger en Diario")

    # Determinar estrategia principal
    if estrategias_call and not estrategias_put:
        estrategia_cardona = estrategias_call[0]
    elif estrategias_put and not estrategias_call:
        estrategia_cardona = estrategias_put[0]
    elif estrategias_call and estrategias_put:
        estrategia_cardona = f"{estrategias_call[0]} + {estrategias_put[0]}"
    else:
        estrategia_cardona = "Sin Estrategia Clara"
        
    # Validación Humana (Horario)
    ahora_ny = datetime.now(NY_TZ)
    if "Primera Vela Roja" in estrategia_cardona:
        validacion = "10:00 - Entrada única"
    elif ahora_ny.hour >= 11:
        validacion = "11:00+ - Verificar vela formada"
    elif ahora_ny.hour >= 15 and ahora_ny.minute >= 55:
        validacion = "15:58 - Cerca del cierre"
    else:
        validacion = "Esperar confirmación"

    return {
        "estrategia_cardona": estrategia_cardona,
        "tendencia_1h": tendencia_1h,
        "sma40_1h": round(sma40_h, 2),
        "distancia_pm40": f"{distancia_pm40:.2f}%",
        "zona_diario": zona_diario,
        "validacion_humana": validacion,
        "call_viable": "VIABLE" if len(estrategias_call) > 0 and tendencia_1h == "Alcista" else "NO VIABLE",
        "put_viable": "VIABLE" if len(estrategias_put) > 0 and tendencia_1h == "Bajista" else "NO VIABLE"
    }

# ==========================================
# FUNCIÓN PRINCIPAL DE ANÁLISIS
# ==========================================

def analizar_activo(ticker):
    print(f"Analizando {ticker}...")
    datos_diarios, datos_horarios, stock = obtener_datos(ticker)
    
    if datos_diarios is None or datos_horarios is None or stock is None:
        return None
    
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    
    # 1. Evaluar estrategias técnicas
    analisis = evaluar_estrategias(ticker, datos_diarios, datos_horarios)
    
    # 2. Obtener datos de opciones (Viernes + OTM)
    vencimiento, strike_call, call_ask, strike_put, put_ask = obtener_datos_opciones(stock, precio_actual)
    
    # 3. Construir fila exacta para el Sheet (Columnas A hasta R)
    return {
        'Ticker': ticker,
        'Fecha_Hora_Escaneo': datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'Precio Spot': round(precio_actual, 2),
        'Tendencia 1H': analisis['tendencia_1h'],
        'SMA 40 (1H)': analisis['sma40_1h'],  # COLUMNA E
        'Estrategia Cardona': analisis['estrategia_cardona'],
        'Condicion 1: Tendencia': analisis['tendencia_1h'],
        'Condicion 2: Distancia PM40': analisis['distancia_pm40'],  # COLUMNA H
        'Condicion 3: Zona Diario': analisis['zona_diario'],
        'Validación Humana': analisis['validacion_humana'],
        'Vencimiento': vencimiento,
        'Strike Call OTM': strike_call,
        'Call Ask ($)': call_ask,
        'Call Estado': analisis['call_viable'],
        'Strike Put OTM': strike_put,
        'Put Ask ($)': put_ask,
        'Put Estado': analisis['put_viable']
    }

# ==========================================
# GUARDAR EN GOOGLE SHEETS
# ==========================================

def guardar_en_sheet(resultados):
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1
        worksheet.clear()
        
        # Headers EXACTOS (A hasta R)
        headers = [
            'Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H', 
            'SMA 40 (1H)', 'Estrategia Cardona', 'Condicion 1: Tendencia', 
            'Condicion 2: Distancia PM40', 'Condicion 3: Zona Diario', 
            'Validación Humana', 'Vencimiento', 'Strike Call OTM', 
            'Call Ask ($)', 'Call Estado', 'Strike Put OTM', 
            'Put Ask ($)', 'Put Estado'
        ]
        worksheet.append_row(headers)
        
        for r in resultados:
            if r is None: continue
            worksheet.append_row([
                r['Ticker'], r['Fecha_Hora_Escaneo'], r['Precio Spot'], r['Tendencia 1H'],
                r['SMA 40 (1H)'], r['Estrategia Cardona'], r['Condicion 1: Tendencia'],
                r['Condicion 2: Distancia PM40'], r['Condicion 3: Zona Diario'],
                r['Validación Humana'], r['Vencimiento'], r['Strike Call OTM'],
                r['Call Ask ($)'], r['Call Estado'], r['Strike Put OTM'],
                r['Put Ask ($)'], r['Put Estado']
            ])
        
        print(f"✅ Datos guardados correctamente. Total: {len(resultados)} activos.")
        return True
    except Exception as e:
        print(f"❌ Error al guardar en Google Sheets: {e}")
        import traceback
        traceback.print_exc()
        return False

# ==========================================
# MAIN
# ==========================================

def main():
    print(f"\n{'='*60}")
    print(f"MÉTODO CARDONA - Escaneo Restaurado")
    print(f"Fecha: {datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S')} (NY)")
    print(f"{'='*60}\n")
    
    resultados = []
    for ticker in TICKERS:
        resultado = analizar_activo(ticker)
        if resultado:
            resultados.append(resultado)
    
    guardar_en_sheet(resultados)
    
    print(f"\n{'='*60}")
    print("RESUMEN:")
    for r in resultados:
        print(f"{r['Ticker']}: {r['Estrategia Cardona']} | Call: {r['Call Estado']} | Put: {r['Put Estado']}")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
