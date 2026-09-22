# ==========================================
# METODO CARDONA - main.py v16
# MODO SIMULACION - NO SE ENVIAN ORDENES REALES
# Opcion A: puerta 1 solo alerta; cero cierres con perdida antes del viernes
# Repo unico: radar-trading-automation
# ==========================================

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import json
import os
import time
import requests
from zoneinfo import ZoneInfo

VERSION = 'v16'
NY_TZ = ZoneInfo('America/New_York')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

gc = None

def conectar_sheets():
    global gc
    credentials_json = os.environ['GOOGLE_CREDENTIALS']
    credentials_info = json.loads(credentials_json)
    creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    gc = gspread.authorize(creds)

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
TICKERS = ['SPY', 'F', 'T', 'PFE', 'VALE', 'AAL', 'BAC', 'USO', 'SOFI', 'CCL', 'NFLX']

MAX_INVERSION = 30.0
MAX_ABIERTAS = 5
MAX_TRADES_SEMANA = 4
META_GAIN = 2.0
COMISION = float(os.environ.get('COMISION_USD', '0.0'))
MIN_ASK = 0.05
MULTIPLICADOR = 100.0
MODELOS = ['gemini-3.6-flash', 'gemini-3.6-flash', 'gemini-flash-latest', 'gemini-flash-latest']

OBJETIVOS_ESCANEO = [(9, 31), (10, 1), (11, 1), (12, 1), (13, 1), (14, 1), (15, 1), (15, 58), (16, 5)]

RANGOS_ASK = {
    'SPY': (0.25, 0.30), 'QQQ': (0.25, 0.30), 'BAC': (0.10, 0.20), 'SLV': (0.10, 0.20),
    'USO': (0.10, 0.20), 'AAPL': (0.45, 0.80), 'FB': (0.45, 0.80), 'AMZN': (0.60, 0.80),
    'TNA': (0.60, 0.80), 'GLD': (0.60, 0.80), 'XOM': (0.60, 0.80), 'CVX': (0.60, 0.80),
    'NVDA': (0.60, 0.80), 'NFLX': (1.50, 2.50), 'MRNA': (2.00, 2.50), 'TSLA': (2.50, 3.00),
}

FOMC_2026 = ['2026-01-27', '2026-01-28', '2026-03-17', '2026-03-18',
             '2026-04-28', '2026-04-29', '2026-06-16', '2026-06-17',
             '2026-07-28', '2026-07-29', '2026-09-15', '2026-09-16',
             '2026-10-27', '2026-10-28', '2026-12-08', '2026-12-09']

GUIA_CARDONA = """
REGLAS GENERALES: decisiones en marco HORA; diario solo para pisos, techos y hanger. Comprar solo en vela formada desde las 11:00 (12, 13, 14, 15 y 15:58). Unica excepcion: Primera Vela Roja a las 10:00 en punto. No salirse ni poner stop si se entro cumpliendo el metodo; solo se pierde lo que costo la opcion. Limit al 100% las primeras semanas. No operar en reuniones de la FED (FOMC); vender antes si hay ganancia.
CALLS: PM40: en hora PM20 sobre PM40, caida que toca o se acerca al PM40, ruptura de linea bajista con vela verde desde las 11. Caida normal menor a 1.5% o fuerte mayor a 1.5% o 5-6 puntos, siempre en tendencia alcista. Ruptura canal bajista: vela verde fuerte o martillo rompe el techo desde las 11; NUNCA comprar CALL dentro del canal. Gap al alza: abre arriba, dos velas verdes o primera roja y segunda verde fuerte; no dentro de canales. Gap bajista al alza: abre abajo con dos velas verdes o primera roja y segunda verde fuerte; cautela extrema dentro de canal bajista. Piso fuerte: diario PM100 sobre PM200 con caida que toca; en hora vela verde rompe el canal; aparece cada 2-5 meses; subida de 2 a 4 dias. Primer gap al alza: caida previa en zona piso fuerte, primera vela verde obligatoria, volumen alto, compra cerca del cierre 15:58.
PUTS: Primera vela roja: unica a las 10:00; vela roja o martillo rojo de 9:30-10:00; funciona tambien en tendencia alcista; evitar zonas baratas y pisos fuertes; preferible lejos de PM20 y PM40. Ruptura piso del gap: primera vela verde, se traza el piso, vela roja lo rompe desde las 11; lejos del PM40 tiene mas exito; da 100% el mismo dia o al siguiente. Modelo 4 pasos: canal bajista, zona de techo, subida borrada por vela roja, vela roja rompe la linea de piso trazada. Hanger en diario: cola superior mayor al cuerpo en zona cara o lejos de pisos; compra cerca del cierre 3:55-4:00; el color no importa.
SALIDAS: vender en la apertura si hay utilidad grande; la venta parcial es sana; el viernes se vende en la tarde para dar tiempo a reversion.
"""

PRIORIDAD = [
    ("Primera Vela Roja", "PUT"),
    ("Ruptura Piso del Gap", "PUT"),
    ("Modelo 4 Pasos", "PUT"),
    ("Hanger en Diario", "PUT"),
    ("Piso Fuerte", "CALL"),
    ("Ruptura Canal Bajista", "CALL"),
    ("Gap Bajista al Alza", "CALL"),
    ("Gap al Alza", "CALL"),
    ("PM 40", "CALL"),
    ("Caida Fuerte", "CALL"),
    ("Caida Normal", "CALL"),
    ("Primer Gap al Alza", "CALL"),
]

SIM_HEADERS = [
    'NOM', 'Fecha', 'Hora', 'Simbolo', 'Strike', 'F. Exp', 'Call/Put',
    'Cantidad', 'Precio Compra', 'Total Inv.', 'Precio Limit',
    'Fecha Venta Prog', 'Fecha Venta', 'Precio Venta', 'Total Venta',
    'Ganancia $', 'Ganancia %', 'Bid Actual', 'Max Bid', 'Estrategia',
    'Estado', 'Notas', 'VI', 'DTE', 'Break Even', 'Max Loss'
]

# ==========================================
# REGISTRO DE ERRORES (sin secretos)
# ==========================================

def registrar_error(funcion, simbolo, operacion, error):
    msg = str(error)
    for secreto in (os.environ.get('GEMINI_API_KEY', ''), os.environ.get('GOOGLE_CREDENTIALS', '')):
        if secreto:
            msg = msg.replace(secreto, '***')
    print('ERROR [' + datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S') + '] ' +
          funcion + ' | ' + simbolo + ' | ' + operacion + ' | ' + type(error).__name__ + ': ' + msg)

# ==========================================
# ZONA HORARIA Y DATOS
# ==========================================

def normalizar_ny(df):
    out = df.copy()
    idx = out.index
    if getattr(idx, 'tz', None) is None:
        idx = idx.tz_localize('UTC')
    out.index = idx.tz_convert(NY_TZ)
    return out

def obtener_datos(ticker):
    try:
        stock = yf.Ticker(ticker)
        datos_diarios = stock.history(period="1y", interval="1d")
        datos_horarios = stock.history(period="2mo", interval="1h")
        datos_30m = stock.history(period="5d", interval="30m")
        if datos_diarios.empty or datos_horarios.empty:
            return None, None, None, stock
        datos_diarios['SMA20'] = datos_diarios['Close'].rolling(20).mean()
        datos_diarios['SMA40'] = datos_diarios['Close'].rolling(40).mean()
        datos_diarios['SMA100'] = datos_diarios['Close'].rolling(100).mean()
        datos_diarios['SMA200'] = datos_diarios['Close'].rolling(200).mean()
        datos_horarios = normalizar_ny(datos_horarios)
        if datos_30m is not None and not datos_30m.empty:
            datos_30m = normalizar_ny(datos_30m)
        datos_horarios['SMA20'] = datos_horarios['Close'].rolling(20).mean()
        datos_horarios['SMA40'] = datos_horarios['Close'].rolling(40).mean()
        return datos_diarios, datos_horarios, datos_30m, stock
    except Exception as e:
        registrar_error('obtener_datos', ticker, 'descarga de historicos', e)
        return None, None, None, None

def velas_de_hoy(datos_horarios):
    ultima = datos_horarios.index.date.max()
    return datos_horarios[datos_horarios.index.date == ultima]

def primera_vela_del_dia(datos_30m, ahora_ny):
    if datos_30m is None or len(datos_30m) == 0:
        return None
    df = datos_30m
    dia = df[df.index.date == df.index.date.max()]
    if dia.empty:
        return None
    reg = dia[(dia.index.hour > 9) | ((dia.index.hour == 9) & (dia.index.minute >= 30))]
    if reg.empty or ahora_ny.hour < 10:
        return None
    return reg.iloc[0]

def es_vela_verde(c): return float(c['Close']) > float(c['Open'])
def es_vela_roja(c): return float(c['Close']) < float(c['Open'])

def es_martillo(c):
    cuerpo = abs(float(c['Close']) - float(c['Open']))
    mi = min(float(c['Open']), float(c['Close'])) - float(c['Low'])
    ms = float(c['High']) - max(float(c['Open']), float(c['Close']))
    return cuerpo > 0 and mi >= 2 * cuerpo and ms <= cuerpo

def es_hanger(c):
    cuerpo = abs(float(c['Close']) - float(c['Open']))
    mi = min(float(c['Open']), float(c['Close'])) - float(c['Low'])
    r = float(c['High']) - float(c['Low'])
    return r > 0 and mi >= 2 * cuerpo and cuerpo <= r * 0.3

def es_vela_verde_fuerte(c):
    r = float(c['High']) - float(c['Low'])
    cu = float(c['Close']) - float(c['Open'])
    return r > 0 and cu > 0 and (cu / r) >= 0.6

def detectar_canal_bajista(datos, n=10):
    if len(datos) < n:
        return False, None
    mx = datos.tail(n)['High'].rolling(3).max().dropna()
    if len(mx) < 3:
        return False, None
    if mx.iloc[-1] < mx.iloc[-3]:
        return True, float(mx.iloc[-1])
    return False, None

# ==========================================
# OPCIONES
# ==========================================

def leer_fila_opcion(row):
    ask = float(row['ask']) if pd.notna(row['ask']) else 0.0
    bid = float(row['bid']) if pd.notna(row['bid']) else 0.0
    last = float(row['lastPrice']) if pd.notna(row['lastPrice']) else 0.0
    iv = float(row['impliedVolatility']) if 'impliedVolatility' in row.index and pd.notna(row['impliedVolatility']) else 0.0
    precio = ask if ask > 0 else (last if last > 0 else (bid if bid > 0 else 0.05))
    return round(precio, 2), round(bid, 2), round(iv, 2)

def viernes_venta_prog(f):
    wd = f.weekday()
    delta = 4 - wd if wd <= 2 else (8 if wd == 3 else (7 if wd == 4 else (6 if wd == 5 else 5)))
    return f + timedelta(days=delta)

def elegir_fila_rango(df_op, precio, lado, ticker):
    if df_op is None or df_op.empty:
        return None
    if lado == 'CALL':
        cand = df_op[df_op['strike'] > precio]
    else:
        cand = df_op[df_op['strike'] < precio]
    if cand.empty:
        return None
    rng = RANGOS_ASK.get(ticker)
    if rng:
        asks = cand['ask'].fillna(0).astype(float)
        dentro = cand[(asks >= rng[0]) & (asks <= rng[1])]
        if not dentro.empty:
            a = dentro['ask'].fillna(0).astype(float)
            return dentro.iloc[int(a.values.argmax())]
    return cand.iloc[0] if lado == 'CALL' else cand.iloc[-1]

def obtener_datos_opciones(stock, precio, ticker):
    try:
        exps = stock.options
        if not exps:
            return None
        hoy = datetime.now(NY_TZ).date()
        objetivo = viernes_venta_prog(hoy)
        venc = None
        for e in exps:
            try:
                d = datetime.strptime(e, '%Y-%m-%d').date()
                if d >= objetivo and d.weekday() == 4:
                    venc = e
                    break
            except Exception:
                continue
        if not venc:
            for e in exps:
                try:
                    d = datetime.strptime(e, '%Y-%m-%d').date()
                    if d >= objetivo:
                        venc = e
                        break
                except Exception:
                    continue
        if not venc:
            return None
        chain = stock.option_chain(venc)
        out = {'venc': str(venc)}
        rc = elegir_fila_rango(chain.calls, precio, 'CALL', ticker)
        if rc is not None:
            p, b, iv = leer_fila_opcion(rc)
            out.update({'strike_call': float(rc['strike']), 'call_ask': p, 'call_bid': b, 'call_iv': iv})
        else:
            out.update({'strike_call': 'N/A', 'call_ask': 'N/A', 'call_bid': 'N/A', 'call_iv': 'N/A'})
        rp = elegir_fila_rango(chain.puts, precio, 'PUT', ticker)
        if rp is not None:
            p, b, iv = leer_fila_opcion(rp)
            out.update({'strike_put': float(rp['strike']), 'put_ask': p, 'put_bid': b, 'put_iv': iv})
        else:
            out.update({'strike_put': 'N/A', 'put_ask': 'N/A', 'put_bid': 'N/A', 'put_iv': 'N/A'})
        return out
    except Exception as e:
        registrar_error('obtener_datos_opciones', ticker, 'cadena de opciones', e)
        return None

def bid_de_cadena(chain, strike, lado):
    try:
        df = chain.calls if lado == 'CALL' else chain.puts
        m = df[df['strike'] == float(strike)]
        if m.empty:
            return 0.0
        r = m.iloc[0]
        bid = float(r['bid']) if pd.notna(r['bid']) else 0.0
        last = float(r['lastPrice']) if pd.notna(r['lastPrice']) else 0.0
        return round(bid if bid > 0 else last, 2)
    except Exception as e:
        registrar_error('bid_de_cadena', str(strike), 'lectura de bid', e)
        return 0.0

# ==========================================
# ESTRATEGIAS
# ==========================================

def estrategia_pm40(dd, dh):
    if len(dh) < 40 or len(dh) < 4:
        return False
    pm20_h = float(dh['SMA20'].iloc[-1])
    pm40_h = float(dh['SMA40'].iloc[-1])
    if pm20_h <= pm40_h:
        return False
    u = dh.iloc[-1]
    if not es_vela_verde(u):
        return False
    if float(u['Close']) <= float(dh['High'].iloc[-2]):
        return False
    recientes = dh['Close'].iloc[-4:-1]
    return any(abs(c - pm40_h) / pm40_h * 100 <= 2.0 for c in recientes)

def estrategia_caida(dd, dh):
    if len(dh) < 2: return False, ""
    p = float(dh['Close'].iloc[-1]); a = float(dh['Close'].iloc[-2])
    if p >= a: return False, ""
    pct = (a - p) / a * 100; pts = a - p
    if pct > 1.5 or pts >= 5: return True, "Caida Fuerte"
    if pct > 0: return True, "Caida Normal"
    return False, ""

def estrategia_ruptura_canal(dh):
    if len(dh) < 10: return False
    hay, techo = detectar_canal_bajista(dh)
    if not hay: return False
    u = dh.iloc[-1]
    return (es_vela_verde_fuerte(u) or es_martillo(u)) and float(u['Close']) > techo

def estrategia_gap_al_alza(dd, dh):
    hoy = velas_de_hoy(dh)
    if len(dd) < 2 or len(hoy) < 2: return False
    if float(hoy['Open'].iloc[0]) <= float(dd['Close'].iloc[-2]): return False
    v1, v2 = hoy.iloc[0], hoy.iloc[1]
    return (es_vela_verde(v1) and es_vela_verde(v2)) or (es_vela_roja(v1) and es_vela_verde_fuerte(v2))

def estrategia_gap_bajista_al_alza(dd, dh):
    hoy = velas_de_hoy(dh)
    if len(dd) < 2 or len(hoy) < 2: return False
    if float(hoy['Open'].iloc[0]) >= float(dd['Close'].iloc[-2]): return False
    v1, v2 = hoy.iloc[0], hoy.iloc[1]
    return (es_vela_verde(v1) and es_vela_verde(v2)) or (es_vela_roja(v1) and es_vela_verde_fuerte(v2))

def estrategia_piso_fuerte(dd, dh):
    if len(dd) < 200 or len(dh) < 10: return False
    p = float(dd['Close'].iloc[-1]); s100 = float(dd['SMA100'].iloc[-1]); s200 = float(dd['SMA200'].iloc[-1])
    if s100 <= s200: return False
    if not ((abs(p - s100) / s100 <= 0.02) or (abs(p - s200) / s200 <= 0.02)): return False
    hay, techo = detectar_canal_bajista(dh)
    if not hay: return False
    u = dh.iloc[-1]
    return es_vela_verde_fuerte(u) and float(u['Close']) > techo

def estrategia_primer_gap(dd, dh, ticker):
    hoy = velas_de_hoy(dh)
    if len(dd) < 200 or len(hoy) < 1:
        return False
    ultima = dh.index.date.max()
    antes = dh[dh.index.date < ultima]
    if antes.empty:
        return False
    cierre_ayer = float(antes['Close'].iloc[-1])
    open_hoy = float(hoy['Open'].iloc[0])
    if open_hoy <= cierre_ayer:
        return False
    p = float(dd['Close'].iloc[-1])
    if not ((p <= float(dd['SMA100'].iloc[-1]) * 1.05) or (p <= float(dd['SMA200'].iloc[-1]) * 1.03)):
        return False
    v1 = hoy.iloc[0]
    vol_min = 20000000 if ticker == 'SPY' else 1000000
    return es_vela_verde(v1) and float(v1['Volume']) >= vol_min

def estrategia_primera_vela_roja(d30, ahora):
    v = primera_vela_del_dia(d30, ahora)
    return v is not None and es_vela_roja(v)

def estrategia_ruptura_piso_gap(dh):
    hoy = velas_de_hoy(dh)
    if len(hoy) < 2: return False
    v1 = hoy.iloc[0]
    if not es_vela_verde(v1): return False
    return bool((hoy.iloc[1:]['Close'] < float(v1['Low'])).any())

def estrategia_modelo_4_pasos(dh):
    if len(dh) < 6: return False
    hay, techo = detectar_canal_bajista(dh)
    if not hay or not techo: return False
    vv, vb, vr = dh.iloc[-3], dh.iloc[-2], dh.iloc[-1]
    if not (es_vela_verde(vv) and es_vela_roja(vb) and es_vela_roja(vr)): return False
    if float(vb['Close']) >= float(vv['Close']): return False
    mn = dh.tail(10)['Low'].rolling(3).min().dropna()
    if len(mn) < 3: return False
    if float(vr['Close']) >= (techo + float(mn.iloc[-1])) / 2: return False
    subida = float(dh['High'].iloc[-6:-2].max())
    return subida >= techo * 0.99

def estrategia_hanger_diario(dd):
    if len(dd) < 20: return False
    if not es_hanger(dd.iloc[-1]): return False
    a = float(dd['SMA20'].iloc[-1])
    b = float(dd['SMA20'].iloc[-10]) if len(dd) >= 30 else a
    return a > b

# ==========================================
# REGLAS AUTOPILOTO
# ==========================================

def hora_entrada_ok(estrategia, ahora):
    if ahora.weekday() > 4:
        return False
    h = ahora.hour
    m = ahora.minute
    if h < 9 or h >= 16:
        return False
    if estrategia == 'Primera Vela Roja':
        return h == 10 and m <= 5
    if estrategia in ('Hanger en Diario', 'Primer Gap al Alza'):
        return h >= 15 and m >= 55
    return h >= 11

def es_hora_de_escaneo(ahora, tolerancia_min=9):
    if ahora.weekday() > 4:
        return False
    for h, m in OBJETIVOS_ESCANEO:
        objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (ahora - objetivo).total_seconds() / 60.0
        if 0 <= delta <= tolerancia_min:
            return True
    return False

def estrategias_pausadas(filas):
    stats = {}
    for f in filas:
        if str(f.get('Estado', '')) == 'CERRADA':
            e = str(f.get('Estrategia', ''))
            try: g = float(f.get('Ganancia $', 0) or 0)
            except Exception: g = 0.0
            w, t = stats.get(e, (0, 0))
            stats[e] = (w + (1 if g > 0 else 0), t + 1)
    return [e for e, (w, t) in stats.items() if t >= 5 and (w / t) < 0.4]

def cerrar_fila(row, bid, hoy_str, nota):
    try: compra = float(row['Precio Compra'])
    except Exception: compra = 0.0
    row['Estado'] = 'CERRADA'
    row['Fecha Venta'] = hoy_str
    row['Precio Venta'] = bid
    row['Total Venta'] = round(bid * MULTIPLICADOR, 2)
    row['Ganancia $'] = round((bid - compra) * MULTIPLICADOR, 2)
    row['Ganancia %'] = round((bid / compra - 1.0) * 100.0, 2) if compra > 0 else 0.0
    row['Notas'] = str(row.get('Notas', '')) + ' | ' + nota

def cerrar_si_vencida(row, ahora):
    if str(row.get('Estado', '')) != 'ABIERTA':
        return False
    raw = str(row.get('F. Exp', '')).strip()
    if not raw:
        registrar_error('cerrar_si_vencida', str(row.get('Simbolo', '')), 'F. Exp vacia: no se cierra automaticamente', ValueError('F. Exp vacia'))
        return False
    try:
        fexp = datetime.strptime(raw, '%Y-%m-%d').date()
    except Exception as e:
        registrar_error('cerrar_si_vencida', str(row.get('Simbolo', '')), 'F. Exp invalida: ' + raw, e)
        return False
    hoy = ahora.date()
    if hoy < fexp:
        return False
    if hoy == fexp and ahora.hour < 16:
        return False
    try: bid_residuo = float(row.get('Bid Actual', 0) or 0)
    except Exception: bid_residuo = 0.0
    precio = bid_residuo if bid_residuo > 0 else 0.0
    nota = 'vencida con valor residual' if precio > 0 else 'vencida sin valor'
    cerrar_fila(row, precio, str(hoy), nota)
    print('CIERRE POR VENCIMIENTO: ' + str(row.get('Simbolo', '')) + ' F.Exp ' + raw + ' -> ' + nota)
    return True

def decidir_venta(tag, lado, bid_usar, compra, maxbid, limit, hoy, fprog, hora, ia_vender):
    """v16: sin puerta 1 de cierre. Devuelve (cerrar, nota)."""
    if 'corredor' in tag:
        if compra > 0 and maxbid >= compra * 3.0 and bid_usar <= compra * 2.0 and bid_usar >= compra:
            return True, 'puerta 2: proteccion de ganancia'
        if ia_vender == 'VENDER':
            return True, 'puerta 3: IA'
        if hoy > fprog:
            return True, 'puerta 4: venta tardia'
        if hoy == fprog and hora >= 15:
            return True, 'puerta 4: viernes en la tarde'
        return False, ''
    else:
        if limit > 0 and bid_usar >= limit:
            return True, 'venta auto +100%'
        if hoy > fprog:
            return True, 'venta tardia'
        if hoy == fprog and hora >= 15:
            return True, 'venta viernes en la tarde'
        return False, ''

# ==========================================
# IA CON GUIA (prompt capado anti-stop-loss)
# ==========================================

def limpiar_json(txt):
    txt = txt.strip()
    fence = chr(96) * 3
    if txt.startswith(fence):
        txt = txt.replace(fence, '')
        if txt.startswith('json'):
            txt = txt[4:]
    return txt.strip()

def revision_ia(f, key):
    try: compra = float(f.get('Precio Compra', 0))
    except Exception: compra = 0.0
    try: bid = float(f.get('Bid Actual', 0))
    except Exception: bid = 0.0
    gan = round((bid / compra - 1) * 100, 1) if compra > 0 else 0.0
    prompt = ("GUIA OFICIAL DEL METODO CARDONA:\n" + GUIA_CARDONA + "\n\n" +
              "POSICION ABIERTA: " + str(f.get('Simbolo')) + " " + str(f.get('Call/Put')) +
              " strike " + str(f.get('Strike')) + ", compra " + str(compra) + ", bid " + str(bid) +
              ", ganancia " + str(gan) + "%, estrategia " + str(f.get('Estrategia')) +
              ". PROHIBIDO recomendar VENDER por porcentaje de perdida, stop loss, miedo o subjetividad." +
              " Solo puedes responder VENDER si la tesis de la estrategia se rompio segun la GUIA" +
              " (condiciones de la estrategia invalidadas) o si la posicion esta en ganancia y la GUIA ordena salir" +
              " (utilidad grande en apertura, viernes, antes de expiracion ITM, FOMC con ganancia)." +
              " Responde SOLO JSON: {\"decision\": \"VENDER\" o \"MANTENER\", \"razon\": \"frase corta\"}")
    for modelo in MODELOS:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/' + modelo + ':generateContent?key=' + key
        body = {'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.3, 'responseMimeType': 'application/json'}}
        try:
            r = requests.post(url, json=body, timeout=45)
            if r.status_code == 200:
                d = json.loads(limpiar_json(r.json()['candidates'][0]['content']['parts'][0]['text']))
                return str(d.get('decision', '')).upper(), str(d.get('razon', ''))
            registrar_error('revision_ia', str(f.get('Simbolo', '')), 'HTTP ' + str(r.status_code) + ' modelo ' + modelo, RuntimeError('HTTP ' + str(r.status_code)))
        except Exception as e:
            registrar_error('revision_ia', str(f.get('Simbolo', '')), 'llamada modelo ' + modelo, e)
        time.sleep(1)
    return None, ''

# ==========================================
# ANALISIS RADAR
# ==========================================

def analizar_activo(ticker, ahora):
    print("Analizando " + ticker + "...")
    dd, dh, d30, stock = obtener_datos(ticker)
    if dd is None or dh is None:
        return None
    precio = float(dh['Close'].iloc[-1])
    abierto = not velas_de_hoy(dh).empty
    sma40 = float(dh['SMA40'].iloc[-1]) if len(dh) >= 40 else precio
    tendencia = "Alcista" if precio > sma40 else "Bajista"
    dist = f"{((precio - sma40) / sma40 * 100):.2f}%" if sma40 > 0 else "N/A"
    dist40 = abs(precio - sma40) / sma40 * 100 if sma40 > 0 else 999.0
    s100 = float(dd['SMA100'].iloc[-1]); s200 = float(dd['SMA200'].iloc[-1])
    en_piso = (abs(precio - s100) / s100 <= 0.02) or (abs(precio - s200) / s200 <= 0.02)
    zona = "En Piso Fuerte" if en_piso else "Fuera de Piso"
    canal_h, _ = detectar_canal_bajista(dh)

    fired = {}
    if abierto and estrategia_primera_vela_roja(d30, ahora) and not en_piso and dist40 >= 0.5:
        fired["Primera Vela Roja"] = True
    if estrategia_ruptura_piso_gap(dh) and dist40 >= 0.5:
        fired["Ruptura Piso del Gap"] = True
    if estrategia_modelo_4_pasos(dh):
        fired["Modelo 4 Pasos"] = True
    if estrategia_hanger_diario(dd) and not en_piso:
        fired["Hanger en Diario"] = True
    if estrategia_piso_fuerte(dd, dh):
        fired["Piso Fuerte"] = True
    if estrategia_ruptura_canal(dh):
        fired["Ruptura Canal Bajista"] = True
    if estrategia_gap_bajista_al_alza(dd, dh):
        fired["Gap Bajista al Alza"] = True
    if estrategia_gap_al_alza(dd, dh) and not canal_h:
        fired["Gap al Alza"] = True
    if estrategia_pm40(dd, dh):
        fired["PM 40"] = True
    ca, tipo_caida = estrategia_caida(dd, dh)
    if ca:
        fired[tipo_caida] = True
    if estrategia_primer_gap(dd, dh, ticker):
        fired["Primer Gap al Alza"] = True

    estrategia_sel = "Sin Estrategia Clara"
    lado_sel = "NINGUNO"
    for nombre, lado in PRIORIDAD:
        if nombre not in fired:
            continue
        if lado == "CALL" and nombre != "Ruptura Canal Bajista" and tendencia != "Alcista":
            continue
        estrategia_sel = nombre
        lado_sel = lado
        break

    if estrategia_sel == "Primera Vela Roja":
        val = "10:00 - Entrada unica"
    elif estrategia_sel in ("Hanger en Diario", "Primer Gap al Alza"):
        val = "15:58 - Cerca del cierre"
    elif ahora.hour >= 15 and ahora.minute >= 55:
        val = "15:58 - Cerca del cierre"
    elif ahora.hour >= 11:
        val = "11:00+ - Verificar vela formada"
    else:
        val = "Esperar confirmacion"

    opc = obtener_datos_opciones(stock, precio, ticker) or {
        'venc': 'N/A', 'strike_call': 'N/A', 'call_ask': 'N/A', 'call_bid': 'N/A', 'call_iv': 'N/A',
        'strike_put': 'N/A', 'put_ask': 'N/A', 'put_bid': 'N/A', 'put_iv': 'N/A'}

    return {
        'Ticker': ticker,
        'Fecha_Hora_Escaneo': ahora.strftime('%Y-%m-%d %H:%M:%S'),
        'Precio Spot': round(precio, 2),
        'Tendencia 1H': tendencia,
        'SMA 40 (1H)': round(sma40, 2),
        'Estrategia Cardona': estrategia_sel,
        'Condicion 1: Tendencia': tendencia,
        'Condicion 2: Distancia PM40': dist,
        'Condicion 3: Zona Diario': zona,
        'Validación Humana': val,
        'Vencimiento': opc['venc'],
        'Strike Call OTM': opc['strike_call'],
        'Call Ask ($)': opc['call_ask'],
        'Call Bid ($)': opc['call_bid'],
        'Call Estado': "VIABLE" if lado_sel == "CALL" else "NO VIABLE",
        'Strike Put OTM': opc['strike_put'],
        'Put Ask ($)': opc['put_ask'],
        'Put Bid ($)': opc['put_bid'],
        'Put Estado': "VIABLE" if lado_sel == "PUT" else "NO VIABLE",
        'Lado Sugerido': lado_sel,
        'Mercado Abierto': abierto,
    }

# ==========================================
# SHEETS (escritura segura)
# ==========================================

def escribir_hoja_segura(ws, tabla, nombre):
    for intento in (1, 2, 3):
        try:
            ws.resize(rows=len(tabla), cols=len(tabla[0]))
            ws.update(tabla)
            print('Hoja ' + nombre + ' escrita: ' + str(len(tabla) - 1) + ' filas')
            return True
        except Exception as e:
            registrar_error('escribir_hoja_segura', nombre, 'intento ' + str(intento), e)
            time.sleep(2)
    return False

def guardar_radar(resultados):
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.sheet1
        headers = ['Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H', 'SMA 40 (1H)',
                   'Estrategia Cardona', 'Condicion 1: Tendencia', 'Condicion 2: Distancia PM40',
                   'Condicion 3: Zona Diario', 'Validación Humana', 'Vencimiento', 'Strike Call OTM',
                   'Call Ask ($)', 'Call Bid ($)', 'Call Estado', 'Strike Put OTM', 'Put Ask ($)',
                   'Put Bid ($)', 'Put Estado']
        tabla = [headers] + [[r[h] for h in headers] for r in resultados]
        if escribir_hoja_segura(ws, tabla, 'radar'):
            return sh
        return None
    except Exception as e:
        registrar_error('guardar_radar', 'sheet', 'apertura/escritura', e)
        return None

def abrir_simulador(sh):
    try:
        return sh.worksheet('SIMULADOR')
    except Exception:
        ws = sh.add_worksheet(title='SIMULADOR', rows="200", cols="30")
        ws.append_row(SIM_HEADERS)
        return ws

def leer_simulador(ws):
    try:
        return [dict(f) for f in ws.get_all_records()]
    except Exception as e:
        registrar_error('leer_simulador', 'SIMULADOR', 'lectura', e)
        return []

def escribir_simulador(ws, filas):
    tabla = [SIM_HEADERS] + [[str(f.get(h, '')) for h in SIM_HEADERS] for f in filas]
    return escribir_hoja_segura(ws, tabla, 'SIMULADOR')

# ==========================================
# MAIN
# ==========================================

def main():
    ahora = datetime.now(NY_TZ)
    print("=" * 60)
    print("METODO CARDONA " + VERSION + " - " + ahora.strftime('%Y-%m-%d %H:%M:%S') + " NY")
    print("MODO SIMULACION - NO SE ENVIAN ORDENES REALES")
    key_gemini = os.environ.get('GEMINI_API_KEY', '')
    dia_fomc = ahora.strftime('%Y-%m-%d') in FOMC_2026
    if dia_fomc:
        print("HOY ES REUNION FOMC: sin compras nuevas; ganancias se cierran (Regla 8)")
    fuerza = os.environ.get('FUERZA', '0') == '1'
    if not fuerza and not es_hora_de_escaneo(ahora):
        print("Fuera de ventana de escaneo NY; sin cambios en el Sheet.")
        print("=" * 60)
        return
    print("=" * 60)

    try:
        conectar_sheets()
    except Exception as e:
        registrar_error('main', 'sheets', 'autenticacion', e)
        raise

    sh0 = gc.open_by_key(SPREADSHEET_ID)
    ws_sim = abrir_simulador(sh0)
    filas = leer_simulador(ws_sim)
    pausadas = estrategias_pausadas(filas)
    if pausadas:
        print("Estrategias en pausa: " + ", ".join(pausadas))

    abiertas = [f for f in filas if str(f.get('Estado', '')) == 'ABIERTA']
    tickers_con_pos = set(str(f.get('Simbolo', '')) for f in abiertas)
    contratos = sum(int(float(f.get('Cantidad', 1) or 1)) for f in abiertas)

    lunes = ahora.date() - timedelta(days=ahora.weekday())
    compras_semana = set()
    for f in filas:
        if str(f.get('NOM', '')) == 'AUTOPILOTO':
            try:
                fd = datetime.strptime(str(f.get('Fecha', '')), '%Y-%m-%d').date()
            except Exception:
                continue
            if fd >= lunes:
                compras_semana.add((str(f.get('Fecha', '')), str(f.get('Hora', '')), str(f.get('Simbolo', ''))))

    resultados = []
    cache_cadenas = {}

    for ticker in TICKERS:
        r = analizar_activo(ticker, ahora)
        if r is not None:
            resultados.append(r)

        # ============ VENTAS (v16: cero cierres con perdida antes del viernes) ============
        for f in filas:
            if str(f.get('Estado', '')) != 'ABIERTA' or str(f.get('Simbolo', '')) != ticker:
                continue
            if cerrar_si_vencida(f, ahora):
                continue
            clave = (ticker, str(f.get('F. Exp', '')))
            if clave not in cache_cadenas:
                try:
                    cache_cadenas[clave] = yf.Ticker(ticker).option_chain(clave[1])
                except Exception as e:
                    registrar_error('main', ticker, 'cadena para venta ' + clave[1], e)
                    cache_cadenas[clave] = None
            chain = cache_cadenas[clave]
            bid = bid_de_cadena(chain, f.get('Strike'), str(f.get('Call/Put', ''))) if chain is not None else 0.0
            if bid > 0:
                f['Bid Actual'] = bid
            try: bid_usar = float(f.get('Bid Actual', 0) or 0)
            except Exception: bid_usar = 0.0
            if bid > 0:
                bid_usar = bid
            try: compra = float(f.get('Precio Compra', 0) or 0)
            except Exception: compra = 0.0
            try: maxbid = float(f.get('Max Bid', 0) or 0)
            except Exception: maxbid = 0.0
            if bid > 0:
                maxbid = max(maxbid, bid)
                f['Max Bid'] = maxbid
            try: limit = float(f.get('Precio Limit', 0) or 0)
            except Exception: limit = 0.0
            try: fprog = datetime.strptime(str(f.get('Fecha Venta Prog', '')), '%Y-%m-%d').date()
            except Exception: fprog = ahora.date()
            hoy = ahora.date()
            if compra <= 0:
                continue
            tag = str(f.get('Notas', ''))
            lado = str(f.get('Call/Put', ''))

            if dia_fomc and bid_usar > 0 and bid_usar >= compra:
                cerrar_fila(f, bid_usar, str(hoy), 'FOMC: ganancia cerrada antes de la reunion')
                print("VENTA FOMC ganancia: " + ticker)
                continue

            # Puerta 1 (v16): SOLO ALERTA registrada, nunca vende (Regla 3 y 1.a.viii)
            if 'corredor' in tag:
                reversion = False
                if r is not None:
                    if lado == 'CALL':
                        reversion = (r['Tendencia 1H'] == 'Bajista') or (r['Put Estado'] == 'VIABLE')
                    else:
                        reversion = (r['Tendencia 1H'] == 'Alcista') or (r['Call Estado'] == 'VIABLE')
                if reversion and 'alerta reversion' not in str(f.get('Notas', '')):
                    f['Notas'] = str(f.get('Notas', '')) + ' | alerta reversion: ' + ahora.strftime('%Y-%m-%d %H:%M')
                    print("ALERTA REVERSION (no se vende, Regla 3): " + ticker)

            # Puerta 3: IA solo con precio fresco; guard anti-stop-loss en codigo
            ia_vender, razon = (None, '')
            if 'corredor' in tag and chain is not None and bid > 0 and ahora.hour >= 15 and key_gemini:
                ia_vender, razon = revision_ia(f, key_gemini)
            if ia_vender == 'VENDER' and bid_usar < compra:
                print("IA IGNORADA: no puede cerrar con perdida | " + ticker + " | " + str(razon))
                f['Notas'] = str(f.get('Notas', '')) + ' | IA ignorada: intento de cierre con perdida'
                ia_vender = None

            cerrar, nota = decidir_venta(tag, lado, bid_usar, compra, maxbid, limit, hoy, fprog, ahora.hour, ia_vender)
            if cerrar:
                if nota == 'puerta 3: IA':
                    nota = 'puerta 3: IA (' + razon + ')'
                cerrar_fila(f, bid_usar, str(hoy), nota)
                print("VENTA (" + nota + "): " + ticker)

        # ============ COMPRAS ============
        if r is None:
            time.sleep(1); continue
        lado = r['Lado Sugerido'] if r['Lado Sugerido'] != 'NINGUNO' else None
        if lado == 'CALL':
            strike, ask, bid0 = r['Strike Call OTM'], r['Call Ask ($)'], r['Call Bid ($)']
        elif lado == 'PUT':
            strike, ask, bid0 = r['Strike Put OTM'], r['Put Ask ($)'], r['Put Bid ($)']
        else:
            strike, ask, bid0 = None, None, None

        if lado is None or ticker in tickers_con_pos:
            time.sleep(1); continue
        if not r['Mercado Abierto']:
            time.sleep(1); continue
        if dia_fomc:
            time.sleep(1); continue
        if len(compras_semana) >= MAX_TRADES_SEMANA:
            time.sleep(1); continue
        if str(r['Estrategia Cardona']) in pausadas:
            time.sleep(1); continue
        if not hora_entrada_ok(str(r['Estrategia Cardona']), ahora):
            time.sleep(1); continue
        if not isinstance(ask, (int, float)) or ask <= 0 or strike == 'N/A':
            time.sleep(1); continue
        if ask < MIN_ASK:
            print("AUTOPILOTO: " + ticker + " ask muy baja, no compra")
            time.sleep(1); continue
        if bid0 > 0 and bid0 < 0.4 * ask:
            print("AUTOPILOTO: " + ticker + " spread ancho, no compra")
            time.sleep(1); continue

        costo = ask * MULTIPLICADOR
        qty = 2 if costo <= 15.0 else (1 if costo <= MAX_INVERSION else 0)
        if qty == 0:
            print("AUTOPILOTO: " + ticker + " fuera de presupuesto del simulador")
            time.sleep(1); continue
        if contratos + qty > MAX_ABIERTAS:
            print("AUTOPILOTO: limite 5 contratos")
            time.sleep(1); continue

        venc = r['Vencimiento']
        try: dte = (datetime.strptime(venc, '%Y-%m-%d').date() - ahora.date()).days
        except Exception: dte = 0
        fprog = viernes_venta_prog(ahora.date())
        be = round(strike + ask, 2) if lado == 'CALL' else round(strike - ask, 2)

        lotes = [('lote meta +100%', round(ask * META_GAIN, 2)),
                 ('lote corredor (IA/reversion)', '')] if qty == 2 else \
                [('lote unico: meta o viernes', round(ask * META_GAIN, 2))]

        for nota, lim in lotes:
            filas.append({
                'NOM': 'AUTOPILOTO', 'Fecha': ahora.strftime('%Y-%m-%d'), 'Hora': ahora.strftime('%H:%M:%S'),
                'Simbolo': ticker, 'Strike': strike, 'F. Exp': venc, 'Call/Put': lado, 'Cantidad': 1,
                'Precio Compra': ask, 'Total Inv.': round(costo + COMISION, 2), 'Precio Limit': lim,
                'Fecha Venta Prog': fprog.strftime('%Y-%m-%d'), 'Fecha Venta': '', 'Precio Venta': '',
                'Total Venta': '', 'Ganancia $': '', 'Ganancia %': '', 'Bid Actual': bid0, 'Max Bid': bid0,
                'Estrategia': r['Estrategia Cardona'], 'Estado': 'ABIERTA', 'Notas': nota,
                'VI': '', 'DTE': dte, 'Break Even': be, 'Max Loss': round(costo + COMISION, 2)
            })
        contratos += qty
        tickers_con_pos.add(ticker)
        compras_semana.add((ahora.strftime('%Y-%m-%d'), ahora.strftime('%H:%M:%S'), ticker))
        print("AUTOPILOTO COMPRA: " + ticker + " " + lado + " (" + r['Estrategia Cardona'] + ") strike " + str(strike) + " x" + str(qty))
        time.sleep(1)

    sh = guardar_radar(resultados)
    if sh is not None:
        escribir_simulador(abrir_simulador(sh), filas)

    print("=" * 60)
    for r in resultados:
        print(r['Ticker'] + " | " + r['Estrategia Cardona'] + " | LADO " + r['Lado Sugerido'])
    print("MODO SIMULACION - NO SE ENVIAN ORDENES REALES")
    print("=" * 60)

if __name__ == '__main__':
    main()
