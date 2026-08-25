# ==========================================
# METODO CARDONA - AUTOMATIZACION COMPLETA
# main.py - VERSION FINAL v5 (AUTOPILOTO + APRENDIZAJE)
# 25 de agosto de 2026
# v5 = v4 + columnas Bid + pestana SIMULADOR + autopiloto
#      + venta escalonada + regla del viernes + pausa de estrategias flojas
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
import time

# ==========================================
# CONFIGURACION INICIAL
# ==========================================
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

try:
    credentials_json = os.environ['GOOGLE_CREDENTIALS']
    credentials_info = json.loads(credentials_json)
    creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
except Exception as e:
    print("Error critico en autenticacion: " + str(e))
    raise

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
TICKERS = ['F', 'T', 'PFE', 'VALE', 'AAL', 'BAC', 'USO', 'SOFI', 'CCL', 'NFLX']
NY_TZ = pytz.timezone('America/New_York')

# ---------- Reglas del Autopiloto ----------
MAX_INVERSION = 30.0          # dolares maximos por operacion
MAX_ABIERTAS = 5              # contratos abiertos maximos
META_GAIN = 2.0               # +100% (precio limit = compra x 2)
COMISION = 0.0                # 0 = modo curso; cambiar a 1.55 para modo broker

SIM_HEADERS = [
    'NOM', 'Fecha', 'Hora', 'Simbolo', 'Strike', 'F. Exp', 'Call/Put',
    'Cantidad', 'Precio Compra', 'Total Inv.', 'Precio Limit',
    'Fecha Venta Prog', 'Fecha Venta', 'Precio Venta', 'Total Venta',
    'Ganancia $', 'Ganancia %', 'Bid Actual', 'Estrategia', 'Estado',
    'Notas', 'VI', 'DTE', 'Break Even', 'Max Loss'
]

# ==========================================
# FUNCIONES DE ANALISIS TECNICO (sin cambios v4)
# ==========================================

def obtener_datos(ticker):
    try:
        stock = yf.Ticker(ticker)
        datos_diarios = stock.history(period="1y", interval="1d")
        datos_horarios = stock.history(period="2mo", interval="1h")
        datos_30m = stock.history(period="5d", interval="30m")

        if datos_diarios.empty or datos_horarios.empty:
            print("Aviso: " + ticker + " datos vacios")
            return None, None, None, stock

        datos_diarios['SMA20'] = datos_diarios['Close'].rolling(window=20).mean()
        datos_diarios['SMA40'] = datos_diarios['Close'].rolling(window=40).mean()
        datos_diarios['SMA100'] = datos_diarios['Close'].rolling(window=100).mean()
        datos_diarios['SMA200'] = datos_diarios['Close'].rolling(window=200).mean()

        datos_horarios['SMA20'] = datos_horarios['Close'].rolling(window=20).mean()
        datos_horarios['SMA40'] = datos_horarios['Close'].rolling(window=40).mean()

        return datos_diarios, datos_horarios, datos_30m, stock
    except Exception as e:
        print("Error obteniendo datos de " + ticker + ": " + str(e))
        return None, None, None, None

def velas_de_hoy(datos_horarios):
    h = datos_horarios.copy()
    h['fecha'] = h.index.date
    ultima_fecha = h['fecha'].iloc[-1]
    return h[h['fecha'] == ultima_fecha]

def primera_vela_del_dia(datos_30m, ahora_ny):
    if datos_30m is None or len(datos_30m) == 0:
        return None
    df = datos_30m.copy()
    ts = df.index
    try:
        if ts.tz is None:
            ts = ts.tz_localize(NY_TZ)
        else:
            ts = ts.tz_convert(NY_TZ)
    except Exception:
        return None
    df['t'] = ts
    ultimo_dia = ts.date.max()
    dia = df[ts.date == ultimo_dia]
    if dia.empty:
        return None
    reg = dia[(dia['t'].dt.hour > 9) | ((dia['t'].dt.hour == 9) & (dia['t'].dt.minute >= 30))]
    if reg.empty:
        return None
    if ahora_ny.hour < 10:
        return None
    return reg.iloc[0]

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
# OPCIONES: ahora trae Ask, Bid y Volatilidad
# ==========================================

def leer_fila_opcion(row):
    """Devuelve (precio de referencia, bid, volatilidad implicita)"""
    ask = float(row['ask']) if pd.notna(row['ask']) else 0.0
    bid = float(row['bid']) if pd.notna(row['bid']) else 0.0
    last = float(row['lastPrice']) if pd.notna(row['lastPrice']) else 0.0
    iv = float(row['impliedVolatility']) if 'impliedVolatility' in row.index and pd.notna(row['impliedVolatility']) else 0.0
    if ask > 0:
        precio = round(ask, 2)
    elif last > 0:
        precio = round(last, 2)
    elif bid > 0:
        precio = round(bid, 2)
    else:
        precio = 0.05
    return precio, round(bid, 2), round(iv, 2)

def obtener_datos_opciones(stock, precio_actual):
    try:
        expiraciones = stock.options
        if not expiraciones:
            return None

        hoy = datetime.now().date()
        fecha_venc = None

        for exp in expiraciones:
            try:
                exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                if exp_date >= hoy and exp_date.weekday() == 4:
                    fecha_venc = exp
                    break
            except Exception:
                continue

        if not fecha_venc:
            for exp in expiraciones:
                try:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                    if exp_date >= hoy:
                        fecha_venc = exp
                        break
                except Exception:
                    continue

        if not fecha_venc:
            return None

        chain = stock.option_chain(fecha_venc)
        calls = chain.calls
        puts = chain.puts

        out = {'venc': str(fecha_venc), 'chain': chain}

        calls_otm = calls[calls['strike'] > precio_actual]
        if not calls_otm.empty:
            r = calls_otm.iloc[0]
            p, b, iv = leer_fila_opcion(r)
            out['strike_call'] = float(r['strike'])
            out['call_ask'] = p
            out['call_bid'] = b
            out['call_iv'] = iv
        else:
            out['strike_call'] = "N/A"
            out['call_ask'] = "N/A"
            out['call_bid'] = "N/A"
            out['call_iv'] = "N/A"

        puts_otm = puts[puts['strike'] < precio_actual]
        if not puts_otm.empty:
            r = puts_otm.iloc[-1]
            p, b, iv = leer_fila_opcion(r)
            out['strike_put'] = float(r['strike'])
            out['put_ask'] = p
            out['put_bid'] = b
            out['put_iv'] = iv
        else:
            out['strike_put'] = "N/A"
            out['put_ask'] = "N/A"
            out['put_bid'] = "N/A"
            out['put_iv'] = "N/A"

        return out
    except Exception as e:
        print("Error obteniendo opciones: " + str(e))
        return None

def bid_de_cadena(chain, strike, lado):
    """Bid actual de un strike especifico (para posiciones abiertas)"""
    try:
        df = chain.calls if lado == 'CALL' else chain.puts
        m = df[df['strike'] == float(strike)]
        if m.empty:
            return 0.0
        r = m.iloc[0]
        bid = float(r['bid']) if pd.notna(r['bid']) else 0.0
        last = float(r['lastPrice']) if pd.notna(r['lastPrice']) else 0.0
        return round(bid if bid > 0 else last, 2)
    except Exception:
        return 0.0

# ==========================================
# ESTRATEGIAS (sin cambios v4)
# ==========================================

def estrategia_pm40(datos_diarios, datos_horarios):
    if len(datos_diarios) < 40 or len(datos_horarios) < 2:
        return False
    pm20_d = float(datos_diarios['SMA20'].iloc[-1])
    pm40_d = float(datos_diarios['SMA40'].iloc[-1])
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    if pm20_d <= pm40_d:
        return False
    if precio_actual >= float(datos_horarios['Close'].iloc[-2]):
        return False
    distancia = abs(precio_actual - pm40_d) / pm40_d * 100
    return distancia <= 2.0

def estrategia_caida(datos_diarios, datos_horarios):
    if len(datos_horarios) < 2:
        return False, ""
    precio_actual = float(datos_horarios['Close'].iloc[-1])
    precio_anterior = float(datos_horarios['Close'].iloc[-2])
    if precio_actual >= precio_anterior:
        return False, ""
    caida_pct = ((precio_anterior - precio_actual) / precio_anterior) * 100
    caida_puntos = precio_anterior - precio_actual
    if caida_pct > 1.5 or caida_puntos >= 5:
        return True, "Caida Fuerte"
    elif caida_pct > 0:
        return True, "Caida Normal"
    return False, ""

def estrategia_ruptura_canal(datos_horarios):
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
    hoy = velas_de_hoy(datos_horarios)
    if len(datos_diarios) < 2 or len(hoy) < 2:
        return False
    cierre_ayer = float(datos_diarios['Close'].iloc[-2])
    apertura_hoy = float(hoy['Open'].iloc[0])
    if apertura_hoy <= cierre_ayer:
        return False
    v1, v2 = hoy.iloc[0], hoy.iloc[1]
    if (es_vela_verde(v1) and es_vela_verde(v2)) or \
       (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
        return True
    return False

def estrategia_gap_bajista_al_alza(datos_diarios, datos_horarios):
    hoy = velas_de_hoy(datos_horarios)
    if len(datos_diarios) < 2 or len(hoy) < 2:
        return False
    cierre_ayer = float(datos_diarios['Close'].iloc[-2])
    apertura_hoy = float(hoy['Open'].iloc[0])
    if apertura_hoy >= cierre_ayer:
        return False
    v1, v2 = hoy.iloc[0], hoy.iloc[1]
    if (es_vela_verde(v1) and es_vela_verde(v2)) or \
       (es_vela_roja(v1) and es_vela_verde_fuerte(v2)):
        return True
    return False

def estrategia_piso_fuerte(datos_diarios, datos_horarios):
    if len(datos_diarios) < 200 or len(datos_horarios) < 10:
        return False
    precio = float(datos_diarios['Close'].iloc[-1])
    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    if pm100 <= pm200:
        return False
    en_piso = (abs(precio - pm100) / pm100 <= 0.02) or \
              (abs(precio - pm200) / pm200 <= 0.02)
    if not en_piso:
        return False
    hay_canal, techo = detectar_canal_bajista(datos_horarios)
    if not hay_canal or techo is None:
        return False
    ultima = datos_horarios.iloc[-1]
    if es_vela_verde_fuerte(ultima) and float(ultima['Close']) > techo:
        return True
    return False

def estrategia_primer_gap(datos_diarios, datos_horarios):
    hoy = velas_de_hoy(datos_horarios)
    if len(datos_diarios) < 200 or len(hoy) < 1:
        return False
    precio = float(datos_diarios['Close'].iloc[-1])
    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    en_zona = (precio <= pm100 * 1.05) or (precio <= pm200 * 1.03)
    if not en_zona:
        return False
    v1 = hoy.iloc[0]
    if not es_vela_verde(v1):
        return False
    volumen = float(v1['Volume'])
    if volumen < 1000000:
        return False
    return True

def estrategia_primera_vela_roja(datos_30m, ahora_ny):
    v1 = primera_vela_del_dia(datos_30m, ahora_ny)
    if v1 is None:
        return False
    return es_vela_roja(v1)

def estrategia_ruptura_piso_gap(datos_horarios):
    hoy = velas_de_hoy(datos_horarios)
    if len(hoy) < 2:
        return False
    v1 = hoy.iloc[0]
    if not es_vela_verde(v1):
        return False
    piso_gap = float(v1['Low'])
    resto = hoy.iloc[1:]
    if (resto['Close'] < piso_gap).any():
        return True
    return False

def estrategia_modelo_4_pasos(datos_horarios):
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
    minimos = datos_horarios.tail(10)['Low'].rolling(window=3).min().dropna()
    if len(minimos) < 3:
        return False
    piso_interno = (techo + float(minimos.iloc[-1])) / 2
    if float(v_roja_rompe['Close']) >= piso_interno:
        return False
    return True

def estrategia_hanger_diario(datos_diarios):
    if len(datos_diarios) < 20:
        return False
    ultima = datos_diarios.iloc[-1]
    if not es_hanger(ultima):
        return False
    pm20_actual = float(datos_diarios['SMA20'].iloc[-1])
    pm20_anterior = float(datos_diarios['SMA20'].iloc[-10]) if len(datos_diarios) >= 30 else pm20_actual
    if pm20_actual <= pm20_anterior:
        return False
    return True

# ==========================================
# REGLAS DEL AUTOPILOTO
# ==========================================

def viernes_venta_prog(fecha_ny):
    """Lun-mie -> viernes de esa semana; jue-vie (o fin de semana) -> viernes siguiente"""
    wd = fecha_ny.weekday()
    if wd <= 2:
        delta = 4 - wd
    elif wd == 3:
        delta = 8
    elif wd == 4:
        delta = 7
    elif wd == 5:
        delta = 6
    else:
        delta = 5
    return fecha_ny + timedelta(days=delta)

def hora_entrada_ok(estrategia, lado, ahora_ny):
    """Respeta los horarios del Metodo Cardona"""
    if ahora_ny.weekday() > 4:
        return False
    h = ahora_ny.hour
    if h < 9 or h >= 16:
        return False
    if lado == 'PUT' and 'Primera Vela Roja' in estrategia:
        return h >= 10
    if 'Hanger' in estrategia:
        return h >= 15
    return h >= 11

def estrategias_pausadas(filas):
    """APRENDIZAJE: pausa estrategias con <40% aciertos tras 5+ operaciones"""
    stats = {}
    for f in filas:
        if str(f.get('Estado', '')) == 'CERRADA':
            e = str(f.get('Estrategia', ''))
            try:
                g = float(f.get('Ganancia $', 0) or 0)
            except Exception:
                g = 0.0
            w, t = stats.get(e, (0, 0))
            stats[e] = (w + (1 if g > 0 else 0), t + 1)
    pausadas = [e for e, (w, t) in stats.items() if t >= 5 and (w / t) < 0.4]
    return pausadas

def cerrar_fila(row, bid, hoy_str, nota):
    try:
        compra = float(row['Precio Compra'])
    except Exception:
        compra = 0.0
    row['Estado'] = 'CERRADA'
    row['Fecha Venta'] = hoy_str
    row['Precio Venta'] = bid
    row['Total Venta'] = round(bid * 100.0, 2)
    row['Ganancia $'] = round((bid - compra) * 100.0, 2)
    row['Ganancia %'] = round((bid / compra - 1.0) * 100.0, 2) if compra > 0 else 0.0
    row['Notas'] = str(row.get('Notas', '')) + ' | ' + nota

# ==========================================
# ANALISIS PRINCIPAL (radar, con Bid nuevo)
# ==========================================

def analizar_activo(ticker, ahora_ny):
    print("Analizando " + ticker + "...")

    datos_diarios, datos_horarios, datos_30m, stock = obtener_datos(ticker)
    if datos_diarios is None or datos_horarios is None:
        return None

    precio = float(datos_horarios['Close'].iloc[-1])
    estrategias_call = []
    estrategias_put = []

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

    if estrategia_primera_vela_roja(datos_30m, ahora_ny):
        estrategias_put.append("Primera Vela Roja")

    if estrategia_ruptura_piso_gap(datos_horarios):
        estrategias_put.append("Ruptura Piso del Gap")

    if estrategia_modelo_4_pasos(datos_horarios):
        estrategias_put.append("Modelo 4 Pasos")

    if estrategia_hanger_diario(datos_diarios):
        estrategias_put.append("Hanger en Diario")

    if estrategias_call and not estrategias_put:
        estrategia_ppal = estrategias_call[0]
    elif estrategias_put and not estrategias_call:
        estrategia_ppal = estrategias_put[0]
    elif estrategias_call and estrategias_put:
        estrategia_ppal = estrategias_call[0] + " + " + estrategias_put[0]
    else:
        estrategia_ppal = "Sin Estrategia Clara"

    sma40 = float(datos_horarios['SMA40'].iloc[-1]) if len(datos_horarios) >= 40 else precio
    tendencia = "Alcista" if precio > sma40 else "Bajista"

    distancia_pm40 = f"{((precio - sma40) / sma40 * 100):.2f}%" if sma40 > 0 else "N/A"

    pm100 = float(datos_diarios['SMA100'].iloc[-1])
    pm200 = float(datos_diarios['SMA200'].iloc[-1])
    en_piso = (abs(precio - pm100) / pm100 <= 0.02) or (abs(precio - pm200) / pm200 <= 0.02)
    zona_diario = "En Piso Fuerte" if en_piso else "Fuera de Piso"

    if "Primera Vela Roja" in estrategia_ppal:
        validacion = "10:00 - Entrada unica"
    elif ahora_ny.hour >= 15 and ahora_ny.minute >= 55:
        validacion = "15:58 - Cerca del cierre"
    elif ahora_ny.hour >= 11:
        validacion = "11:00+ - Verificar vela formada"
    else:
        validacion = "Esperar confirmacion"

    opc = obtener_datos_opciones(stock, precio)
    if opc is None:
        opc = {'venc': 'N/A', 'strike_call': 'N/A', 'call_ask': 'N/A', 'call_bid': 'N/A',
               'call_iv': 'N/A', 'strike_put': 'N/A', 'put_ask': 'N/A', 'put_bid': 'N/A',
               'put_iv': 'N/A', 'chain': None}

    call_estado = "VIABLE" if len(estrategias_call) > 0 and tendencia == "Alcista" else "NO VIABLE"
    put_estado = "VIABLE" if len(estrategias_put) > 0 and tendencia == "Bajista" else "NO VIABLE"

    return {
        'Ticker': ticker,
        'Fecha_Hora_Escaneo': ahora_ny.strftime('%Y-%m-%d %H:%M:%S'),
        'Precio Spot': round(precio, 2),
        'Tendencia 1H': tendencia,
        'SMA 40 (1H)': round(sma40, 2),
        'Estrategia Cardona': estrategia_ppal,
        'Condicion 1: Tendencia': tendencia,
        'Condicion 2: Distancia PM40': distancia_pm40,
        'Condicion 3: Zona Diario': zona_diario,
        'Validación Humana': validacion,
        'Vencimiento': opc['venc'],
        'Strike Call OTM': opc['strike_call'],
        'Call Ask ($)': opc['call_ask'],
        'Call Bid ($)': opc['call_bid'],
        'Call Estado': call_estado,
        'Strike Put OTM': opc['strike_put'],
        'Put Ask ($)': opc['put_ask'],
        'Put Bid ($)': opc['put_bid'],
        'Put Estado': put_estado,
        '_opc': opc
    }

# ==========================================
# HOJAS DE GOOGLE SHEETS
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
            'Call Ask ($)', 'Call Bid ($)', 'Call Estado', 'Strike Put OTM',
            'Put Ask ($)', 'Put Bid ($)', 'Put Estado'
        ]
        worksheet.append_row(headers)

        for r in resultados:
            worksheet.append_row([
                r['Ticker'], r['Fecha_Hora_Escaneo'], r['Precio Spot'],
                r['Tendencia 1H'], r['SMA 40 (1H)'], r['Estrategia Cardona'],
                r['Condicion 1: Tendencia'], r['Condicion 2: Distancia PM40'],
                r['Condicion 3: Zona Diario'], r['Validación Humana'],
                r['Vencimiento'], r['Strike Call OTM'], r['Call Ask ($)'],
                r['Call Bid ($)'], r['Call Estado'], r['Strike Put OTM'],
                r['Put Ask ($)'], r['Put Bid ($)'], r['Put Estado']
            ])
        print("Radar guardado: " + str(len(resultados)) + " activos")
        return sh
    except Exception as e:
        print("Error guardando radar: " + str(e))
        return None

def abrir_simulador(sh):
    try:
        ws = sh.worksheet('SIMULADOR')
    except Exception:
        ws = sh.add_worksheet(title='SIMULADOR', rows="200", cols="30")
        ws.append_row(SIM_HEADERS)
    return ws

def leer_simulador(ws):
    try:
        filas = ws.get_all_records()
        return [dict(f) for f in filas]
    except Exception:
        return []

def escribir_simulador(ws, filas):
    try:
        ws.clear()
        ws.append_row(SIM_HEADERS)
        for f in filas:
            ws.append_row([str(f.get(h, '')) for h in SIM_HEADERS])
        print("Simulador guardado: " + str(len(filas)) + " filas")
    except Exception as e:
        print("Error guardando simulador: " + str(e))

# ==========================================
# MAIN (radar + autopiloto)
# ==========================================

def main():
    ahora_ny = datetime.now(NY_TZ)
    print("=" * 60)
    print("METODO CARDONA - RADAR + AUTOPILOTO v5")
    print("Fecha: " + ahora_ny.strftime('%Y-%m-%d %H:%M:%S') + " (NY)")
    print("=" * 60)

    resultados = []

    # Leer simulador al inicio
    sh0 = gc.open_by_key(SPREADSHEET_ID)
    ws_sim = abrir_simulador(sh0)
    filas_sim = leer_simulador(ws_sim)
    pausadas = estrategias_pausadas(filas_sim)
    if pausadas:
        print("Estrategias en pausa (aprendizaje): " + ", ".join(pausadas))

    abiertas = [f for f in filas_sim if str(f.get('Estado', '')) == 'ABIERTA']
    tickers_con_posicion = set(str(f.get('Simbolo', '')) for f in abiertas)
    contratos_abiertos = sum(int(float(f.get('Cantidad', 1) or 1)) for f in abiertas)

    cadenas_cache = {}

    for ticker in TICKERS:
        r = analizar_activo(ticker, ahora_ny)
        if r is None:
            continue
        resultados.append(r)

        # ---------- VENTAS: revisar posiciones de este ticker ----------
        for f in filas_sim:
            if str(f.get('Estado', '')) != 'ABIERTA' or str(f.get('Simbolo', '')) != ticker:
                continue
            exp = str(f.get('F. Exp', ''))
            lado = str(f.get('Call/Put', ''))
            clave = (ticker, exp)
            if clave not in cadenas_cache:
                try:
                    cadenas_cache[clave] = yf.Ticker(ticker).option_chain(exp)
                except Exception:
                    cadenas_cache[clave] = None
            chain = cadenas_cache[clave]
            if chain is None:
                continue
            bid = bid_de_cadena(chain, f.get('Strike'), lado)
            f['Bid Actual'] = bid
            if bid <= 0:
                continue
            try:
                limit = float(f.get('Precio Limit', 0) or 0)
            except Exception:
                limit = 0.0
            notas = str(f.get('Notas', ''))
            hoy_str = ahora_ny.strftime('%Y-%m-%d')
            try:
                f_prog = datetime.strptime(str(f.get('Fecha Venta Prog', '')), '%Y-%m-%d').date()
            except Exception:
                f_prog = ahora_ny.date()

            if limit > 0 and bid >= limit:
                cerrar_fila(f, bid, hoy_str, 'venta auto +100%')
                print("AUTOPILOTO VENTA +100%: " + ticker + " " + lado)
            elif ahora_ny.date() >= f_prog:
                cerrar_fila(f, bid, hoy_str, 'venta programada viernes')
                print("AUTOPILOTO VENTA VIERNES: " + ticker + " " + lado)

        # ---------- COMPRAS: senal viable + reglas ----------
        if r['Call Estado'] == 'VIABLE':
            lado, strike, ask, bid0, iv = 'CALL', r['Strike Call OTM'], r['Call Ask ($)'], r['Call Bid ($)'], r['call_iv'] if 'call_iv' in r.get('_opc', {}) else r['_opc'].get('call_iv', 0)
        elif r['Put Estado'] == 'VIABLE':
            lado, strike, ask, bid0, iv = 'PUT', r['Strike Put OTM'], r['Put Ask ($)'], r['Put Bid ($)'], r['_opc'].get('put_iv', 0)
        else:
            lado = None

        if lado is None:
            time.sleep(1)
            continue
        if ticker in tickers_con_posicion:
            time.sleep(1)
            continue
        if str(r['Estrategia Cardona']) in pausadas:
            print("AUTOPILOTO: estrategia en pausa para " + ticker)
            time.sleep(1)
            continue
        if not hora_entrada_ok(str(r['Estrategia Cardona']), lado, ahora_ny):
            time.sleep(1)
            continue
        if not isinstance(ask, (int, float)) or ask <= 0 or strike == 'N/A':
            time.sleep(1)
            continue

        costo = ask * 100.0
        if costo <= 15.0:
            qty = 2
        elif costo <= MAX_INVERSION:
            qty = 1
        else:
            print("AUTOPILOTO: " + ticker + " muy cara (" + str(round(costo, 2)) + "), no compra")
            time.sleep(1)
            continue

        if contratos_abiertos + qty > MAX_ABIERTAS:
            print("AUTOPILOTO: limite de 5 contratos alcanzado")
            time.sleep(1)
            continue

        venc = r['Vencimiento']
        try:
            dte = (datetime.strptime(venc, '%Y-%m-%d').date() - ahora_ny.date()).days
        except Exception:
            dte = 0
        f_prog = viernes_venta_prog(ahora_ny.date())
        be = round(strike + ask, 2) if lado == 'CALL' else round(strike - ask, 2)

        lotes = []
        if qty == 2:
            lotes = [('lote meta +100%',), ('lote viernes',)]
        else:
            lotes = [('lote unico: meta o viernes',)]

        for (nota_lote,) in lotes:
            filas_sim.append({
                'NOM': 'AUTOPILOTO',
                'Fecha': ahora_ny.strftime('%Y-%m-%d'),
                'Hora': ahora_ny.strftime('%H:%M:%S'),
                'Simbolo': ticker,
                'Strike': strike,
                'F. Exp': venc,
                'Call/Put': lado,
                'Cantidad': 1,
                'Precio Compra': ask,
                'Total Inv.': round(costo, 2),
                'Precio Limit': round(ask * META_GAIN, 2),
                'Fecha Venta Prog': f_prog.strftime('%Y-%m-%d'),
                'Fecha Venta': '',
                'Precio Venta': '',
                'Total Venta': '',
                'Ganancia $': '',
                'Ganancia %': '',
                'Bid Actual': bid0,
                'Estrategia': r['Estrategia Cardona'],
                'Estado': 'ABIERTA',
                'Notas': nota_lote,
                'VI': iv,
                'DTE': dte,
                'Break Even': be,
                'Max Loss': round(costo + COMISION, 2)
            })

        contratos_abiertos += qty
        tickers_con_posicion.add(ticker)
        print("AUTOPILOTO COMPRA: " + ticker + " " + lado + " strike " + str(strike) + " x" + str(qty) + " a " + str(ask))

        time.sleep(1)

    # Guardar radar y simulador
    sh = guardar_en_sheet(resultados)
    if sh is not None:
        ws_sim = abrir_simulador(sh)
        escribir_simulador(ws_sim, filas_sim)

    print("=" * 60)
    print("RESUMEN:")
    for r in resultados:
        print(r['Ticker'] + " | " + r['Estrategia Cardona'] + " | CALL: " + r['Call Estado'] + " | PUT: " + r['Put Estado'])
    print("=" * 60)

if __name__ == '__main__':
    main()
