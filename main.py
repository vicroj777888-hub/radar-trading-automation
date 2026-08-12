# ==========================================
# MÉTODO CARDONA - AUTOMATIZACIÓN COMPLETA
# Versión final - Todas las estrategias
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

credentials_json = os.environ['GOOGLE_CREDENTIALS']
credentials_info = json.loads(credentials_json)
creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
gc = gspread.authorize(creds)

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
TICKERS = ['F', 'T', 'PFE', 'VALE', 'AAL', 'BAC', 'USO', 'SOFI', 'CCL', 'NFLX']
NY_TZ = pytz.timezone('America/New_York')

# ==========================================
# FUNCIONES DE ANÁLISIS TÉCNICO
# ==========================================

def obtener_datos(ticker):
    """Obtiene datos diarios (1 año) y horarios (2 meses)"""
    try:
        stock = yf.Ticker(ticker)
        datos_diarios = stock.history(period="1y", interval="1d")
        datos_horarios = stock.history(period="2mo", interval="1h")
        
        if datos_diarios.empty or datos_horarios.empty:
            return None, None, stock
        
        # Medias móviles diarias
        datos_diarios['SMA20'] = datos_diarios['Close'].rolling(window=20).mean()
        datos_diarios['SMA40'] = datos_diarios['Close'].rolling(window=40).mean()
        datos_diarios['SMA100'] = datos_diarios['Close'].rolling(window=100).mean()
        datos_diarios['SMA200'] = datos_diarios['Close'].rolling(window=200).mean()
        
        # Medias móviles horarias
        datos_horarios['SMA20'] = datos_horarios['Close'].rolling(window=20).mean()
        datos_horarios['SMA40'] = datos_horarios['Close'].rolling(window=40).mean()
        
        return datos_diarios, datos_horarios, stock
    except Exception as e:
        print(f"Error datos {ticker}: {e}")
        return None, None, None

def es_vela_verde(candle):
    return float(candle['Close']) > float(candle['Open'])

def es_vela_roja(candle):
    return float(candle['Close']) < float(candle['Open'])

def es_martillo(candle):
    cuerpo = abs(float(candle['Close']) - float(candle['Open']))
    mecha_inf = min(float(candle['Open']), float(candle['Close'])) - float(candle['Low'])
    mecha_sup = float(candle['High']) - max(float(candle['Open']), float(candle['Close']))
    if cuerpo == 0:
        return False
    return mecha_inf >= (2 * cuerpo) and mecha_sup <= cuerpo

def es_hanger(candle):
    cuerpo = abs(float(candle['Close']) - float(candle['Open']))
    mecha_inf = min(float(candle['Open']), float(candle['Close'])) - float(candle['Low'])
    rango = float(candle['High']) - float(candle['Low'])
    if rango == 0:
        return False
    return mecha_inf >= (2 * cuerpo) and cuerpo <= rango * 0.3

def es_vela_verde_fuerte(candle):
    rango = float(candle['High']) - float(candle['Low'])
    cuerpo = float(candle['Close']) - float(candle['Open'])
    if rango == 0:
        return False
    return cuerpo > 0 and (cuerpo / rango) >= 0.6

def detectar_canal_bajista(datos, num_velas=10):
    if len(datos) < num_velas:
        return False, None
    ultimas = datos.tail(num_velas)
    maximos = ultimas['High'].rolling(window=3).max().dropna()
    if len(maximos) < 3:
        return False, None
    if maximos.iloc[-1] < maximos.iloc[-3]:
        return True, float(maximos.iloc[-1])
    return False, None

# ==========================================
# FUNCIONES DE OPCIONES
# ==========================================

def obtener_datos_opciones(stock, precio_actual):
    """Obtiene vencimiento del viernes y strikes OTM"""
    try:
        expiraciones = stock.options
        if not expiraciones:
            return "N/A", "N/A", "N/A", "N/A", "N/A"
        
        hoy = datetime.now().date()
        fecha_venc = None
        
        # Buscar próximo viernes
        for exp in expiraciones:
            try:
                exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                if exp_date >= hoy and exp_date.weekday() == 4:
                    fecha_venc = exp
                    break
            except:
                continue
        
        if not fecha_venc:
            for exp in expiraciones:
                try:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                    if exp_date >= hoy:
                        fecha_venc = exp
                        break
                except:
                    continue
        
        if not fecha_venc:
            return "N/A", "N/A", "N/A", "N/A", "N/A"
        
        chain = stock.option_chain(fecha_venc)
        calls = chain.calls
        puts = chain.puts
        
        # Call OTM (strike > precio)
        calls_otm = calls[calls['strike'] > precio_actual]
        if not calls_otm.empty:
            row = calls_otm.iloc[0]
            strike_call = float(row['strike'])
            call_ask = float(row['ask']) if pd.notna(row['ask']) else 0.05
        else:
            strike_call, call_ask = "N/A", "N/A"
        
        # Put OTM (strike < precio)
        puts_otm = puts[puts['strike'] < precio_actual]
        if not puts_otm.empty:
            row = puts_otm.iloc[0]
            strike_put = float(row['strike'])
            put_ask = float(row['ask']) if pd.notna(row['ask']) else 0.05
        else:
            strike_put, put_ask = "N/A", "N/A"
        
        return str(fecha_venc), strike_call, call_ask, strike_put, put_ask
    except Exception as e:
        print(f"Error opciones {stock.ticker}: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A"

# ==========================================
# ESTRATEGIAS CALL
# ==========================================

def estrategia_pm40(datos_diarios, datos_horarios):
    """PM 40 - PM20 sobre PM40 + caída que toca PM40"""
    if len(datos_diarios) < 40 or len(datos_horarios) < 2:
        return False
    
    pm20_d = float(datos_diarios['SMA20'].iloc[-1])
    pm40_d = float(datos_diarios['SMA40'].iloc[-1])
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    
    if pm20_d <= pm40_d:
        return False
    
    # Verificar caída
    if precio_actual >= float(datos_horarios['Close'].iloc[-2]):
        return False
    
    # Distancia a PM40
    distancia = abs(precio_actual - pm40_d) / pm40_d * 100
    return distancia <= 2.0

def estrategia_caida(datos_diarios, datos_horarios):
    """Caída Normal (<1.5%) o Fuerte (>1.5% o 5-6 puntos)"""
    if len(datos_horarios) < 2:
        return False, ""
    
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    precio_anterior = float(datos_horarios['Close'].iloc[-2])
    
    if precio_actual >= precio_anterior:
        return False, ""
    
    caida_pct = ((precio_anterior - precio_actual) / precio_anterior) * 100
    caida_puntos = precio_anterior - precio_actual
    
    if caida_pct > 1.5 or caida_puntos >= 5:
        return True, "Caída Fuerte"
    elif caida_pct > 0:
        return True, "Caída Normal"
    
    return False, ""

def estrategia_ruptura_canal(datos_horarios):
    """Ruptura de canal bajista con vela verde fuerte o martillo"""
    if len(datos_horarios) < 10:
        return False
    
    hay_canal, techo = detectar_canal_bajista(datos_horarios)
    if not hay_canal or techo is None:
        return False
    
    ultima = datos_horarios.iloc[-1]
    if es_vela_verde_fuerte(ultima) or es_martillo(ultima):
        if float(ultima['Close']) > techo:
            return True
    
    return False

def estrategia_gap_al_alza(datos_diarios, datos_horarios):
    """Gap alcista + 2 velas verdes o 1ra roja 2da verde fuerte"""
    if len(datos_diarios) < 2 or len(datos_horarios) < 2:
        return False
    
    cierre_ayer = float(datos_diarios['Close'].iloc[-2])
    apertura_hoy = float(datos_horarios['Open'].iloc[0])
    
    if apertura_hoy <= cierre_ayer:
        return False
    
    v1, v2 = datos_horarios.iloc[0], datos_horarios.iloc[1]
    if (es_vela_verde(v1) and es_vela_verde(v2)) or \
       (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
        return True
    
    return False

def estrategia_gap_bajista_al_alza(datos_diarios, datos_horarios):
    """Abre abajo vs cierre anterior + recuperación"""
    if len(datos_diarios) < 2 or len(datos_horarios) < 2:
        return False
    
    cierre_ayer = float(datos_diarios['Close'].iloc[-2])
    apertura_hoy = float(datos_horarios['Open'].iloc[0])
    
    if apertura_hoy >= cierre_ayer:
        return False
    
    v1, v2 = datos_horarios.iloc[0], datos_horarios.iloc[1]
    if (es_vela_verde(v1) and es_vela_verde(v2)) or \
       (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
        return True
    
    return False

def estrategia_piso_fuerte(datos_diarios, datos_horarios):
    """PM100 sobre PM200 + caída toca PM100 + ruptura canal en hora"""
    if len(datos_diarios) < 200 or len(datos_horarios) < 10:
        return False
    
    precio = float(datos_diarios['Close'].iloc[-1])
    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    
    if pm100 <= pm200:
        return False
    
    # Verificar si está en piso fuerte
    en_piso = (abs(precio - pm100) / pm100 <= 0.02) or \
              (abs(precio - pm200) / pm200 <= 0.02)
    
    if not en_piso:
        return False
    
    # Verificar ruptura de canal en hora
    hay_canal, techo = detectar_canal_bajista(datos_horarios)
    if not hay_canal or techo is None:
        return False
    
    ultima = datos_horarios.iloc[-1]
    if es_vela_verde_fuerte(ultima) and float(ultima['Close']) > techo:
        return True
    
    return False

def estrategia_primer_gap(datos_diarios, datos_horarios):
    """Caída en zona piso fuerte + primera vela verde + volumen"""
    if len(datos_diarios) < 200 or len(datos_horarios) < 1:
        return False
    
    precio = float(datos_diarios['Close'].iloc[-1])
    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    
    # Verificar zona de piso
    en_zona = (precio <= pm100 * 1.05) or (precio <= pm200 * 1.03)
    if not en_zona:
        return False
    
    # Primera vela debe ser verde
    v1 = datos_horarios.iloc[0]
    if not es_vela_verde(v1):
        return False
    
    # Verificar volumen (ajustar según el activo)
    volumen = float(v1['Volume'])
    if volumen < 1000000:  # Mínimo 1M
        return False
    
    return True

# ==========================================
# ESTRATEGIAS PUT
# ==========================================

def estrategia_primera_vela_roja(datos_horarios):
    """Primera vela del día (9:30-10:00) debe ser roja"""
    if len(datos_horarios) < 1:
        return False
    
    v1 = datos_horarios.iloc[0]
    return es_vela_roja(v1)

def estrategia_ruptura_piso_gap(datos_horarios):
    """Primera vela verde + vela roja rompe piso del gap"""
    if len(datos_horarios) < 2:
        return False
    
    v1 = datos_horarios.iloc[0]
    if not es_vela_verde(v1):
        return False
    
    piso_gap = float(v1['Low'])
    ultima = datos_horarios.iloc[-1]
    
    if es_vela_roja(ultima) and float(ultima['Close']) < piso_gap:
        return True
    
    return False

def estrategia_modelo_4_pasos(datos_horarios):
    """Canal bajista + vela verde borrada + ruptura piso"""
    if len(datos_horarios) < 3:
        return False
    
    hay_canal, techo = detectar_canal_bajista(datos_horarios)
    if not hay_canal or techo is None:
        return False
    
    v_verde = datos_horarios.iloc[-3]
    v_roja_borra = datos_horarios.iloc[-2]
    v_roja_rompe = datos_horarios.iloc[-1]
    
    if not (es_vela_verde(v_verde) and es_vela_roja(v_roja_borra) and \
            es_vela_roja(v_roja_rompe)):
        return False
    
    if float(v_roja_borra['Close']) >= float(v_verde['Close']):
        return False
    
    # Calcular piso interno del canal
    minimos = datos_horarios.tail(10)['Low'].rolling(window=3).min().dropna()
    if len(minimos) < 3:
        return False
    
    piso_interno = (techo + float(minimos.iloc[-1])) / 2
    
    if float(v_roja_rompe['Close']) >= piso_interno:
        return False
    
    return True

def estrategia_hanger_diario(datos_diarios):
    """Hanger en diario (martillo invertido en tendencia alcista)"""
    if len(datos_diarios) < 20:
        return False
    
    ultima = datos_diarios.iloc[-1]
    if not es_hanger(ultima):
        return False
    
    # Verificar tendencia alcista
    pm20_actual = float(datos_diarios['SMA20'].iloc[-1])
    pm20_anterior = float(datos_diarios['SMA20'].iloc[-10]) if len(datos_diarios) >= 30 else pm20_actual
    
    if pm20_actual <= pm20_anterior:
        return False
    
    return True

# ==========================================
# ANÁLISIS PRINCIPAL
# ==========================================

def analizar_activo(ticker):
    """Analiza todas las estrategias para un activo"""
    print(f"Analizando {ticker}...")
    
    datos_diarios, datos_horarios, stock = obtener_datos(ticker)
    if datos_diarios is None or datos_horarios is None:
        return None
    
    precio = float(datos_horarios['Close'].iloc[-1])
    estrategias_call = []
    estrategias_put = []
    
    # ===== CALL =====
    if estrategia_pm40(datos_diarios, datos_horarios):
        estrategias_call.append("PM 40")
    
    caida_activa, tipo = estrategia_caida(datos_diarios, datos_horarios)
    if caida_activa:
        estrategias_call.append(tipo)
    
    if estrategia_ruptura_canal(datos_horarios):
        estrategias_call.append("Ruptura Canal Bajista")
    
    if estrategia_gap_al_alza(datos_diarios, datos_horarios):
        estrategias_call.append("Gap al Alza")
    
    if estrategia_gap_bajista_al_alza(datos_diarios, datos_horarios):
        estrategias_call.append("Gap Bajista al Alza")
    
    if estrategia_piso_fuerte(datos_diarios, datos_horarios):
        estrategias_call.append("Piso Fuerte")
    
    if estrategia_primer_gap(datos_diarios, datos_horarios):
        estrategias_call.append("Primer Gap al Alza")
    
    # ===== PUT =====
    if estrategia_primera_vela_roja(datos_horarios):
        estrategias_put.append("Primera Vela Roja")
    
    if estrategia_ruptura_piso_gap(datos_horarios):
        estrategias_put.append("Ruptura Piso del Gap")
    
    if estrategia_modelo_4_pasos(datos_horarios):
        estrategias_put.append("Modelo 4 Pasos")
    
    if estrategia_hanger_diario(datos_diarios):
        estrategias_put.append("Hanger en Diario")
    
    # Determinar estrategia principal
    if estrategias_call and not estrategias_put:
        estrategia_ppal = estrategias_call[0]
    elif estrategias_put and not estrategias_call:
        estrategia_ppal = estrategias_put[0]
    elif estrategias_call and estrategias_put:
        estrategia_ppal = f"{estrategias_call[0]} + {estrategias_put[0]}"
    else:
        estrategia_ppal = "Sin Estrategia Clara"
    
    # Tendencia 1H
    sma40 = float(datos_horarios['SMA40'].iloc[-1]) if len(datos_horarios) >= 40 else precio
    tendencia = "Alcista" if precio > sma40 else "Bajista"
    
    # Distancia PM40
    distancia_pm40 = f"{((precio - sma40) / sma40 * 100):.2f}%" if sma40 > 0 else "N/A"
    
    # Zona diario
    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    en_piso = (abs(precio - pm100) / pm100 <= 0.02) or (abs(precio - pm200) / pm200 <= 0.02)
    zona_diario = "En Piso Fuerte" if en_piso else "Fuera de Piso"
    
    # Validación humana (horario)
    ahora_ny = datetime.now(NY_TZ)
    if "Primera Vela Roja" in estrategia_ppal:
        validacion = "10:00 - Entrada única"
    elif ahora_ny.hour >= 11:
        validacion = "11:00+ - Verificar vela formada"
    elif ahora_ny.hour >= 15 and ahora_ny.minute >= 55:
        validacion = "15:58 - Cerca del cierre"
    else:
        validacion = "Esperar confirmación"
    
    # Obtener opciones
    venc, strike_call, call_ask, strike_put, put_ask = obtener_datos_opciones(stock, precio)
    
    # CALL/PUT Estado
    call_estado = "VIABLE" if len(estrategias_call) > 0 and tendencia == "Alcista" else "NO VIABLE"
    put_estado = "VIABLE" if len(estrategias_put) > 0 and tendencia == "Bajista" else "NO VIABLE"
    
    return {
        'Ticker': ticker,
        'Fecha_Hora_Escaneo': datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'Precio Spot': round(precio, 2),
        'Tendencia 1H': tendencia,
        'SMA 40 (1H)': round(sma40, 2),
        'Estrategia Cardona': estrategia_ppal,
        'Condicion 1: Tendencia': tendencia,
        'Condicion 2: Distancia PM40': distancia_pm40,
        'Condicion 3: Zona Diario': zona_diario,
        'Validación Humana': validacion,
        'Vencimiento': venc,
        'Strike Call OTM': strike_call,
        'Call Ask ($)': call_ask,
        'CALL Estado': call_estado,
        'Strike Put OTM': strike_put,
        'Put Ask ($)': put_ask,
        'PUT Estado': put_estado
    }

# ==========================================
# GUARDAR EN SHEET
# ==========================================

def guardar_en_sheet(resultados):
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1
        worksheet.clear()
        
        headers = [
            'Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H',
            'SMA 40 (1H)', 'Estrategia Cardona', 'Condicion 1: Tendencia',
            'Condicion 2: Distancia PM40', 'Condicion 3: Zona Diario',
            'Validación Humana', 'Vencimiento', 'Strike Call OTM',
            'Call Ask ($)', 'CALL Estado', 'Strike Put OTM',
            'Put Ask ($)', 'PUT Estado'
        ]
        worksheet.append_row(headers)
        
        for r in resultados:
            if r is None:
                continue
            worksheet.append_row([
                r['Ticker'], r['Fecha_Hora_Escaneo'], r['Precio Spot'],
                r['Tendencia 1H'], r['SMA 40 (1H)'], r['Estrategia Cardona'],
                r['Condicion 1: Tendencia'], r['Condicion 2: Distancia PM40'],
                r['Condicion 3: Zona Diario'], r['Validación Humana'],
                r['Vencimiento'], r['Strike Call OTM'], r['Call Ask ($)'],
                r['CALL Estado'], r['Strike Put OTM'], r['Put Ask ($)'],
                r['PUT Estado']
            ])
        
        print(f"✅ Guardados {len(resultados)} activos")
        return True
    except Exception as e:
        print(f"❌ Error guardando: {e}")
        import traceback
        traceback.print_exc()
        return False

# ==========================================
# MAIN
# ==========================================

def main():
    print(f"\n{'='*60}")
    print(f"MÉTODO CARDONA - Escaneo")
    print(f"Fecha: {datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S')} (NY)")
    print(f"Tickers: {', '.join(TICKERS)}")
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
        print(f"{r['Ticker']}: {r['Estrategia Cardona']} | CALL: {r['CALL Estado']} | PUT: {r['PUT Estado']}")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
