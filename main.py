# ==========================================
# METODO CARDONA - main.py v6 FINAL
# Autopiloto + 4 puertas de salida + aprendizaje
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
import pytz
import time
import requests

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

MAX_INVERSION = 30.0
MAX_ABIERTAS = 5
META_GAIN = 2.0
COMISION = 0.0
MODELOS = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-latest']

SIM_HEADERS = [
    'NOM', 'Fecha', 'Hora', 'Simbolo', 'Strike', 'F. Exp', 'Call/Put',
    'Cantidad', 'Precio Compra', 'Total Inv.', 'Precio Limit',
    'Fecha Venta Prog', 'Fecha Venta', 'Precio Venta', 'Total Venta',
    'Ganancia $', 'Ganancia %', 'Bid Actual', 'Max Bid', 'Estrategia',
    'Estado', 'Notas', 'VI', 'DTE', 'Break Even', 'Max Loss'
]

# ==========================================
# DATOS Y VELAS
# ==========================================

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
        datos_horarios['SMA20'] = datos_horarios['Close'].rolling(20).mean()
        datos_horarios['SMA40'] = datos_horarios['Close'].rolling(40).mean()
        return datos_diarios, datos_horarios, datos_30m, stock
    except Exception as e:
        print("Error datos " + ticker + ": " + str(e))
        return None, None, None, None

def velas_de_hoy(datos_horarios):
    h = datos_horarios.copy()
    h['fecha'] = h.index.date
    return h[h['fecha'] == h['fecha'].iloc[-1]]

def primera_vela_del_dia(datos_30m, ahora_ny):
    if datos_30m is None or len(datos_30m) == 0:
        return None
    df = datos_30m.copy()
    ts = df.index
    try:
        ts = ts.tz_localize(NY_TZ) if ts.tz is None else ts.tz_convert(NY_TZ)
    except Exception:
        return None
    df['t'] = ts
    dia = df[ts.date == ts.date.max()]
    if dia.empty:
        return None
    reg = dia[(dia['t'].dt.hour > 9) | ((dia['t'].dt.hour == 9) & (dia['t'].dt.minute >= 30))]
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

def obtener_datos_opciones(stock, precio):
    try:
        exps = stock.options
        if not exps:
            return None
        hoy = datetime.now().date()
        venc = None
        for e in exps:
            try:
                d = datetime.strptime(e, '%Y-%m-%d').date()
                if d >= hoy and d.weekday() == 4:
                    venc = e
                    break
            except Exception:
                continue
        if not venc:
            for e in exps:
                try:
                    d = datetime.strptime(e, '%Y-%m-%d').date()
                    if d >= hoy:
                        venc = e
                        break
                except Exception:
                    continue
        if not venc:
            return None
        chain = stock.option_chain(venc)
        out = {'venc': str(venc)}
        co = chain.calls[chain.calls['strike'] > precio]
        if not co.empty:
            r = co.iloc[0]
            p, b, iv = leer_fila_opcion(r)
            out.update({'strike_call': float(r['strike']), 'call_ask': p, 'call_bid': b, 'call_iv': iv})
        else:
            out.update({'strike_call': 'N/A', 'call_ask': 'N/A', 'call_bid': 'N/A', 'call_iv': 'N/A'})
        po = chain.puts[chain.puts['strike'] < precio]
        if not po.empty:
            r = po.iloc[-1]
            p, b, iv = leer_fila_opcion(r)
            out.update({'strike_put': float(r['strike']), 'put_ask': p, 'put_bid': b, 'put_iv': iv})
        else:
            out.update({'strike_put': 'N/A', 'put_ask': 'N/A', 'put_bid': 'N/A', 'put_iv': 'N/A'})
        return out
    except Exception as e:
        print("Error opciones: " + str(e))
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
    except Exception:
        return 0.0

# ==========================================
# ESTRATEGIAS
# ==========================================

def estrategia_pm40(dd, dh):
    if len(dd) < 40 or len(dh) < 2: return False
    if float(dd['SMA20'].iloc[-1]) <= float(dd['SMA40'].iloc[-1]): return False
    p = float(dh['Close'].iloc[-1])
    if p >= float(dh['Close'].iloc[-2]): return False
    return abs(p - float(dd['SMA40'].iloc[-1])) / float(dd['SMA40'].iloc[-1]) * 100 <= 2.0

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

def estrategia_primer_gap(dd, dh):
    hoy = velas_de_hoy(dh)
    if len(dd) < 200 or len(hoy) < 1: return False
    p = float(dd['Close'].iloc[-1])
    if not ((p <= float(dd['SMA100'].iloc[-1]) * 1.05) or (p <= float(dd['SMA200'].iloc[-1]) * 1.03)): return False
    v1 = hoy.iloc[0]
    return es_vela_verde(v1) and float(v1['Volume']) >= 1000000

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
    if len(dh) < 3: return False
    hay, techo = detectar_canal_bajista(dh)
    if not hay: return False
    vv, vb, vr = dh.iloc[-3], dh.iloc[-2], dh.iloc[-1]
    if not (es_vela_verde(vv) and es_vela_roja(vb) and es_vela_roja(vr)): return False
    if float(vb['Close']) >= float(vv['Close']): return False
    mn = dh.tail(10)['Low'].rolling(3).min().dropna()
    if len(mn) < 3: return False
    return float(vr['Close']) < (techo + float(mn.iloc[-1])) / 2

def estrategia_hanger_diario(dd):
    if len(dd) < 20: return False
    if not es_hanger(dd.iloc[-1]): return False
    a = float(dd['SMA20'].iloc[-1])
    b = float(dd['SMA20'].iloc[-10]) if len(dd) >= 30 else a
    return a > b

# ==========================================
# AUTOPILOTO: reglas
# ==========================================

def viernes_venta_prog(f):
    wd = f.weekday()
    delta = 4 - wd if wd <= 2 else (8 if wd == 3 else (7 if wd == 4 else (6 if wd == 5 else 5)))
    return f + timedelta(days=delta)

def hora_entrada_ok(estrategia, lado, ahora):
    if ahora.weekday() > 4: return False
    h = ahora.hour
    if h < 9 or h >= 16: return False
    if lado == 'PUT' and 'Primera Vela Roja' in estrategia: return h >= 10
    if 'Hanger' in estrategia: return h >= 15
    return h >= 11

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
    row['Total Venta'] = round(bid * 100.0, 2)
    row['Ganancia $'] = round((bid - compra) * 100.0, 2)
    row['Ganancia %'] = round((bid / compra - 1.0) * 100.0, 2) if compra > 0 else 0.0
    row['Notas'] = str(row.get('Notas', '')) + ' | ' + nota

# ==========================================
# IA: revision diaria del lote corredor
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
    """Puerta 3: Gemini decide VENDER o MANTENER el lote corredor"""
    try: compra = float(f.get('Precio Compra', 0))
    except Exception: compra = 0.0
    try: bid = float(f.get('Bid Actual', 0))
    except Exception: bid = 0.0
    gan = round((bid / compra - 1) * 100, 1) if compra > 0 else 0.0
    prompt = ("Eres analista del Metodo Cardona. Posicion: " + str(f.get('Simbolo')) + " " + str(f.get('Call/Put')) +
              " strike " + str(f.get('Strike')) + ", compra " + str(compra) + ", bid " + str(bid) +
              ", ganancia " + str(gan) + "%, estrategia " + str(f.get('Estrategia')) +
              ". Responde SOLO JSON: {\"decision\": \"VENDER\" o \"MANTENER\", \"razon\": \"frase corta\"}")
    for modelo in MODELOS:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/' + modelo + ':generateContent?key=' + key
        body = {'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.3, 'responseMimeType': 'application/json'}}
        try:
            r = requests.post(url, json=body, timeout=45)
            if r.status_code == 200:
                d = json.loads(limpiar_json(r.json()['candidates'][0]['content']['parts'][0]['text']))
                return str(d.get('decision', '')).upper(), str(d.get('razon', ''))
        except Exception:
            pass
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
    sc, sp = [], []
    if estrategia_pm40(dd, dh): sc.append("PM 40")
    ca, ti = estrategia_caida(dd, dh)
    if ca: sc.append(ti)
    if estrategia_ruptura_canal(dh): sc.append("Ruptura Canal Bajista")
    if estrategia_gap_al_alza(dd, dh): sc.append("Gap al Alza")
    if estrategia_gap_bajista_al_alza(dd, dh): sc.append("Gap Bajista al Alza")
    if estrategia_piso_fuerte(dd, dh): sc.append("Piso Fuerte")
    if estrategia_primer_gap(dd, dh): sc.append("Primer Gap al Alza")
    if estrategia_primera_vela_roja(d30, ahora): sp.append("Primera Vela Roja")
    if estrategia_ruptura_piso_gap(dh): sp.append("Ruptura Piso del Gap")
    if estrategia_modelo_4_pasos(dh): sp.append("Modelo 4 Pasos")
    if estrategia_hanger_diario(dd): sp.append("Hanger en Diario")

    if sc and not sp: epp = sc[0]
    elif sp and not sc: epp = sp[0]
    elif sc and sp: epp = sc[0] + " + " + sp[0]
    else: epp = "Sin Estrategia Clara"

    sma40 = float(dh['SMA40'].iloc[-1]) if len(dh) >= 40 else precio
    tendencia = "Alcista" if precio > sma40 else "Bajista"
    dist = f"{((precio - sma40) / sma40 * 100):.2f}%" if sma40 > 0 else "N/A"
    s100 = float(dd['SMA100'].iloc[-1]); s200 = float(dd['SMA200'].iloc[-1])
    en_piso = (abs(precio - s100) / s100 <= 0.02) or (abs(precio - s200) / s200 <= 0.02)
    zona = "En Piso Fuerte" if en_piso else "Fuera de Piso"

    if "Primera Vela Roja" in epp: val = "10:00 - Entrada unica"
    elif ahora.hour >= 15 and ahora.minute >= 55: val = "15:58 - Cerca del cierre"
    elif ahora.hour >= 11: val = "11:00+ - Verificar vela formada"
    else: val = "Esperar confirmacion"

    opc = obtener_datos_opciones(stock, precio) or {
        'venc': 'N/A', 'strike_call': 'N/A', 'call_ask': 'N/A', 'call_bid': 'N/A', 'call_iv': 'N/A',
        'strike_put': 'N/A', 'put_ask': 'N/A', 'put_bid': 'N/A', 'put_iv': 'N/A'}

    return {
        'Ticker': ticker,
        'Fecha_Hora_Escaneo': ahora.strftime('%Y-%m-%d %H:%M:%S'),
        'Precio Spot': round(precio, 2),
        'Tendencia 1H': tendencia,
        'SMA 40 (1H)': round(sma40, 2),
        'Estrategia Cardona': epp,
        'Condicion 1: Tendencia': tendencia,
        'Condicion 2: Distancia PM40': dist,
        'Condicion 3: Zona Diario': zona,
        'Validación Humana': val,
        'Vencimiento': opc['venc'],
        'Strike Call OTM': opc['strike_call'],
        'Call Ask ($)': opc['call_ask'],
        'Call Bid ($)': opc['call_bid'],
        'Call Estado': "VIABLE" if sc and tendencia == "Alcista" else "NO VIABLE",
        'Strike Put OTM': opc['strike_put'],
        'Put Ask ($)': opc['put_ask'],
        'Put Bid ($)': opc['put_bid'],
        'Put Estado': "VIABLE" if sp and tendencia == "Bajista" else "NO VIABLE",
    }

# ==========================================
# SHEETS
# ==========================================

def guardar_radar(resultados):
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.sheet1
        ws.clear()
        headers = ['Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H', 'SMA 40 (1H)',
                   'Estrategia Cardona', 'Condicion 1: Tendencia', 'Condicion 2: Distancia PM40',
                   'Condicion 3: Zona Diario', 'Validación Humana', 'Vencimiento', 'Strike Call OTM',
                   'Call Ask ($)', 'Call Bid ($)', 'Call Estado', 'Strike Put OTM', 'Put Ask ($)',
                   'Put Bid ($)', 'Put Estado']
        ws.append_row(headers)
        for r in resultados:
            ws.append_row([r[h] for h in headers])
        print("Radar guardado: " + str(len(resultados)))
        return sh
    except Exception as e:
        print("Error guardando radar: " + str(e))
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
    except Exception:
        return []

def escribir_simulador(ws, filas):
    try:
        ws.clear()
        ws.append_row(SIM_HEADERS)
        for f in filas:
            ws.append_row([str(f.get(h, '')) for h in SIM_HEADERS])
        print("Simulador guardado: " + str(len(filas)))
    except Exception as e:
        print("Error guardando simulador: " + str(e))

# ==========================================
# MAIN
# ==========================================

def main():
    ahora = datetime.now(NY_TZ)
    key_gemini = os.environ.get('GEMINI_API_KEY', '')
    print("=" * 60)
    print("METODO CARDONA v6 - " + ahora.strftime('%Y-%m-%d %H:%M:%S') + " NY")
    print("=" * 60)

    sh0 = gc.open_by_key(SPREADSHEET_ID)
    ws_sim = abrir_simulador(sh0)
    filas = leer_simulador(ws_sim)
    pausadas = estrategias_pausadas(filas)
    if pausadas:
        print("Estrategias en pausa: " + ", ".join(pausadas))

    abiertas = [f for f in filas if str(f.get('Estado', '')) == 'ABIERTA']
    tickers_con_pos = set(str(f.get('Simbolo', '')) for f in abiertas)
    contratos = sum(int(float(f.get('Cantidad', 1) or 1)) for f in abiertas)
    resultados = []
    cache_cadenas = {}

    for ticker in TICKERS:
        r = analizar_activo(ticker, ahora)
        if r is None:
            continue
        resultados.append(r)

        # ============ VENTAS (4 puertas) ============
        for f in filas:
            if str(f.get('Estado', '')) != 'ABIERTA' or str(f.get('Simbolo', '')) != ticker:
                continue
            clave = (ticker, str(f.get('F. Exp', '')))
            if clave not in cache_cadenas:
                try:
                    cache_cadenas[clave] = yf.Ticker(ticker).option_chain(clave[1])
                except Exception:
                    cache_cadenas[clave] = None
            chain = cache_cadenas[clave]
            if chain is None:
                continue
            bid = bid_de_cadena(chain, f.get('Strike'), str(f.get('Call/Put', '')))
            f['Bid Actual'] = bid
            try: compra = float(f.get('Precio Compra', 0) or 0)
            except Exception: compra = 0.0
            try: maxbid = float(f.get('Max Bid', 0) or 0)
            except Exception: maxbid = 0.0
            maxbid = max(maxbid, bid)
            f['Max Bid'] = maxbid
            if bid <= 0 or compra <= 0:
                continue
            try: limit = float(f.get('Precio Limit', 0) or 0)
            except Exception: limit = 0.0
            try: fprog = datetime.strptime(str(f.get('Fecha Venta Prog', '')), '%Y-%m-%d').date()
            except Exception: fprog = ahora.date()
            hoy = ahora.date()
            tag = str(f.get('Notas', ''))
            lado = str(f.get('Call/Put', ''))

            if 'corredor' in tag:
                # Puerta 1: reversion Cardona
                reversion = False
                if lado == 'CALL':
                    reversion = (r['Tendencia 1H'] == 'Bajista') or (r['Put Estado'] == 'VIABLE')
                else:
                    reversion = (r['Tendencia 1H'] == 'Alcista') or (r['Call Estado'] == 'VIABLE')
                # Puerta 2: proteccion de ganancia (subio a +200% y cayo bajo +100%)
                proteccion = (maxbid >= compra * 3.0) and (bid <= compra * 2.0)
                # Puerta 3: revision IA a las 15:00+
                ia_vender, razon = (None, '')
                if ahora.hour >= 15 and key_gemini:
                    ia_vender, razon = revision_ia(f, key_gemini)
                if reversion:
                    cerrar_fila(f, bid, str(hoy), 'puerta 1: reversion Cardona')
                    print("VENTA P1 reversion: " + ticker)
                elif proteccion:
                    cerrar_fila(f, bid, str(hoy), 'puerta 2: proteccion de ganancia')
                    print("VENTA P2 proteccion: " + ticker)
                elif ia_vender == 'VENDER':
                    cerrar_fila(f, bid, str(hoy), 'puerta 3: IA (' + razon + ')')
                    print("VENTA P3 IA: " + ticker)
                elif hoy >= fprog:
                    cerrar_fila(f, bid, str(hoy), 'puerta 4: viernes de vencimiento')
                    print("VENTA P4 viernes: " + ticker)
            else:
                # Lote meta / lote unico
                if limit > 0 and bid >= limit:
                    cerrar_fila(f, bid, str(hoy), 'venta auto +100%')
                    print("VENTA +100%: " + ticker)
                elif hoy >= fprog:
                    cerrar_fila(f, bid, str(hoy), 'venta programada viernes')
                    print("VENTA viernes: " + ticker)

        # ============ COMPRAS ============
        if r['Call Estado'] == 'VIABLE':
            lado, strike, ask, bid0 = 'CALL', r['Strike Call OTM'], r['Call Ask ($)'], r['Call Bid ($)']
        elif r['Put Estado'] == 'VIABLE':
            lado, strike, ask, bid0 = 'PUT', r['Strike Put OTM'], r['Put Ask ($)'], r['Put Bid ($)']
        else:
            lado = None
        if lado is None or ticker in tickers_con_pos:
            time.sleep(1); continue
        if str(r['Estrategia Cardona']) in pausadas:
            time.sleep(1); continue
        if not hora_entrada_ok(str(r['Estrategia Cardona']), lado, ahora):
            time.sleep(1); continue
        if not isinstance(ask, (int, float)) or ask <= 0 or strike == 'N/A':
            time.sleep(1); continue

        costo = ask * 100.0
        qty = 2 if costo <= 15.0 else (1 if costo <= MAX_INVERSION else 0)
        if qty == 0:
            print("AUTOPILOTO: " + ticker + " muy cara, no compra")
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
                'Precio Compra': ask, 'Total Inv.': round(costo, 2), 'Precio Limit': lim,
                'Fecha Venta Prog': fprog.strftime('%Y-%m-%d'), 'Fecha Venta': '', 'Precio Venta': '',
                'Total Venta': '', 'Ganancia $': '', 'Ganancia %': '', 'Bid Actual': bid0, 'Max Bid': bid0,
                'Estrategia': r['Estrategia Cardona'], 'Estado': 'ABIERTA', 'Notas': nota,
                'VI': '', 'DTE': dte, 'Break Even': be, 'Max Loss': round(costo + COMISION, 2)
            })
        contratos += qty
        tickers_con_pos.add(ticker)
        print("AUTOPILOTO COMPRA: " + ticker + " " + lado + " strike " + str(strike) + " x" + str(qty))
        time.sleep(1)

    sh = guardar_radar(resultados)
    if sh is not None:
        escribir_simulador(abrir_simulador(sh), filas)

    print("=" * 60)
    for r in resultados:
        print(r['Ticker'] + " | " + r['Estrategia Cardona'] + " | CALL " + r['Call Estado'] + " | PUT " + r['Put Estado'])
    print("=" * 60)

if __name__ == '__main__':
    main()
