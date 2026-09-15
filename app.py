# ==========================================
# RADAR DE FRANCOTIRADOR - DSS TRADING
# app.py - VERSION v8 (informe IA con diagnostico real)
# MODO SIMULACION - NO SE ENVIAN ORDENES REALES
# Repo unico: radar-trading-automation
# ==========================================

import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import json
import urllib.request
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh
import time

FUEGO = "\U0001F525"
CHECK = "\u2705"
CRUZ = "\u274C"
ALTA = "\u25B2"
BAJA = "\u25BC"
NEU = "\u25CF"

st.set_page_config(page_title="Radar DSS Trading", layout="wide")
st_autorefresh(interval=300000, key="autorefresh_global")

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
URL_CSV = 'https://docs.google.com/spreadsheets/d/' + SPREADSHEET_ID + '/export?format=csv&gid=0'
ZONA_NY = ZoneInfo('America/New_York')
REPO = 'vicroj777888-hub/radar-trading-automation'

MAX_INVERSION = 30.0
MAX_ABIERTAS = 5
META_GAIN = 2.0
COMISION = 0.0
MODELOS = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-latest']

SCAN_TIMES = [(9, 31), (10, 1), (11, 1), (12, 1), (13, 1), (14, 1), (15, 1), (15, 58)]

GUIA_CARDONA = """
REGLAS GENERALES: decisiones en marco HORA; diario solo para pisos, techos y hanger. Comprar solo en vela formada desde las 11:00 (12, 13, 14, 15 y 15:58). Unica excepcion: Primera Vela Roja a las 10:00 en punto. No salirse ni poner stop si se entro cumpliendo el metodo; solo se pierde lo que costo la opcion. Limit al 100% las primeras semanas. No operar en reuniones de la FED (FOMC); vender antes si hay ganancia.
CALLS: PM40: en hora PM20 sobre PM40, caida que toca o se acerca al PM40, ruptura de linea bajista con vela verde desde las 11. Caida normal menor a 1.5% o fuerte mayor a 1.5% o 5-6 puntos, siempre en tendencia alcista. Ruptura canal bajista: vela verde fuerte o martillo rompe el techo desde las 11; NUNCA comprar CALL dentro del canal. Gap al alza: abre arriba, dos velas verdes o primera roja y segunda verde fuerte; no dentro de canales. Gap bajista al alza: abre abajo con dos velas verdes o primera roja y segunda verde fuerte; cautela extrema dentro de canal bajista. Piso fuerte: diario PM100 sobre PM200 con caida que toca; en hora vela verde rompe el canal; aparece cada 2-5 meses; subida de 2 a 4 dias. Primer gap al alza: caida previa en zona piso fuerte, primera vela verde obligatoria, volumen alto, compra cerca del cierre 15:58.
PUTS: Primera vela roja: unica a las 10:00; vela roja o martillo rojo de 9:30-10:00; funciona tambien en tendencia alcista; evitar zonas baratas y pisos fuertes; preferible lejos de PM20 y PM40. Ruptura piso del gap: primera vela verde, se traza el piso, vela roja lo rompe desde las 11; lejos del PM40 tiene mas exito; da 100% el mismo dia o al siguiente. Modelo 4 pasos: canal bajista, zona de techo, subida borrada por vela roja, vela roja rompe la linea de piso trazada. Hanger en diario: cola superior mayor al cuerpo en zona cara o lejos de pisos; compra cerca del cierre 3:55-4:00; el color no importa.
SALIDAS: vender en la apertura si hay utilidad grande; la venta parcial es sana; el viernes se vende en la tarde para dar tiempo a reversion.
"""

SIM_HEADERS = [
    'NOM', 'Fecha', 'Hora', 'Simbolo', 'Strike', 'F. Exp', 'Call/Put',
    'Cantidad', 'Precio Compra', 'Total Inv.', 'Precio Limit',
    'Fecha Venta Prog', 'Fecha Venta', 'Precio Venta', 'Total Venta',
    'Ganancia $', 'Ganancia %', 'Bid Actual', 'Max Bid', 'Estrategia',
    'Estado', 'Notas', 'VI', 'DTE', 'Break Even', 'Max Loss'
]

IA_HEADERS = ['Fecha', 'Resumen', 'Lecciones', 'Recomendaciones']

if 'esperando' not in st.session_state:
    st.session_state['esperando'] = False
if 'aviso_listo' not in st.session_state:
    st.session_state['aviso_listo'] = False
if 'auto_dispatch_hecho' not in st.session_state:
    st.session_state['auto_dispatch_hecho'] = ''

# ==========================================
# CONEXIONES CON DIAGNOSTICO VISIBLE
# ==========================================

CRED_ERROR = ''

@st.cache_resource
def conectar_sheet():
    global CRED_ERROR
    try:
        info = json.loads(st.secrets['GOOGLE_CREDENTIALS'])
        creds = Credentials.from_service_account_info(info, scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ])
        return gspread.authorize(creds)
    except Exception as e:
        CRED_ERROR = str(e)
        return None

def obtener_key_gemini():
    try:
        k = st.secrets['GEMINI_API_KEY']
        if k:
            return k
    except Exception:
        pass
    return ''

def abrir_sim(ws_ok):
    if ws_ok is None:
        return None, None
    try:
        sh = ws_ok.open_by_key(SPREADSHEET_ID)
        try:
            return sh, sh.worksheet('SIMULADOR')
        except Exception:
            ws = sh.add_worksheet(title='SIMULADOR', rows="200", cols="30")
            ws.append_row(SIM_HEADERS)
            return sh, ws
    except Exception:
        return None, None

def abrir_informe_ia(sh):
    if sh is None:
        return None
    try:
        return sh.worksheet('INFORME_IA')
    except Exception:
        ws = sh.add_worksheet(title='INFORME_IA', rows="100", cols="10")
        ws.append_row(IA_HEADERS)
        return ws

def leer_sim(ws):
    try:
        return [dict(f) for f in ws.get_all_records()]
    except Exception:
        return []

def escribir_sim(ws, filas):
    try:
        ws.clear()
        ws.append_row(SIM_HEADERS)
        for f in filas:
            ws.append_row([str(f.get(h, '')) for h in SIM_HEADERS])
        return True
    except Exception:
        return False

# ==========================================
# HELPERS SIMULADOR
# ==========================================

def viernes_venta_prog(fecha_ny):
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

def bid_actual_posicion(simbolo, exp, strike, lado):
    try:
        chain = yf.Ticker(simbolo).option_chain(exp)
        return bid_de_cadena(chain, strike, lado)
    except Exception:
        return 0.0

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

def gan_pct_viva(row):
    try:
        compra = float(row['Precio Compra'])
        bid = float(row.get('Bid Actual', 0) or 0)
        if compra > 0 and bid > 0:
            return (bid / compra - 1.0) * 100.0
    except Exception:
        pass
    return 0.0

# ==========================================
# GEMINI: INFORME CON DIAGNOSTICO REAL
# ==========================================

def limpiar_json(txt):
    txt = txt.strip()
    fence = chr(96) * 3
    if txt.startswith(fence):
        txt = txt.replace(fence, '')
        if txt.startswith('json'):
            txt = txt[4:]
    return txt.strip()

def sanear(txt, key):
    """Quita la clave de cualquier mensaje antes de mostrarlo."""
    out = str(txt)
    if key:
        out = out.replace(key, '***')
    return out[:300]

def informe_gemini(cerradas, lecciones_previas, key):
    """Devuelve {'ok', 'motivo', 'diagnosticos', 'datos'}. Nunca imprime la clave."""
    diag = []
    if not key:
        return {'ok': False, 'motivo': 'Falta GEMINI_API_KEY en Secrets de Streamlit', 'diagnosticos': diag, 'datos': None}

    lineas = []
    for f in cerradas:
        partes = [
            'entrada ' + str(f.get('Fecha', '')) + ' ' + str(f.get('Hora', '')),
            'cierre ' + str(f.get('Fecha Venta', '')),
            str(f.get('Simbolo')) + ' ' + str(f.get('Call/Put')),
            'strike ' + str(f.get('Strike')),
            'exp ' + str(f.get('F. Exp')),
            'estrategia ' + str(f.get('Estrategia')),
            'compra ' + str(f.get('Precio Compra')),
            'venta ' + str(f.get('Precio Venta')),
            'bid ' + str(f.get('Bid Actual')),
            'bid_max ' + str(f.get('Max Bid')),
            'gan$ ' + str(f.get('Ganancia $')),
            'gan% ' + str(f.get('Ganancia %')),
            'motivo_cierre ' + str(f.get('Notas', '')),
            'estado ' + str(f.get('Estado', '')),
        ]
        lineas.append(' | '.join(partes))

    prompt = "GUIA OFICIAL DEL METODO CARDONA:\n" + GUIA_CARDONA + "\n\n"
    prompt += "OPERACIONES CERRADAS DEL PAPER TRADING (todos los campos disponibles):\n" + "\n".join(lineas) + "\n\n"
    prompt += "LECCIONES DE SEMANAS ANTERIORES:\n" + (lecciones_previas or 'ninguna aun') + "\n\n"
    prompt += """Analiza CADA operacion y el conjunto contra la GUIA. Determina:
- que estrategias funcionaron y cuales fallaron;
- que reglas de la GUIA se cumplieron y cuales se incumplieron;
- si la entrada fue correcta (horario, zona, tendencia, vela formada);
- si la salida fue correcta (limit +100%, viernes en la tarde, puertas, vencimiento);
- si hubo vencimientos sin valor;
- que condiciones existian al abrir la posicion y que ocurrio durante la operacion;
- que debe verificarse antes de repetir cada estrategia.
NO modifiques estrategias: produce unicamente recomendaciones para revision humana.
Responde UNICAMENTE con este JSON valido, en espanol:
{
 "resumen": "...",
 "win_rate_por_estrategia": {"estrategia": "porcentaje"},
 "reglas_violadas": ["..."],
 "lecciones": ["..."],
 "recomendaciones": ["..."],
 "entradas_correctas": ["..."],
 "salidas_correctas": ["..."],
 "vencimientos_sin_valor": ["..."],
 "verificar_antes_de_repetir": ["..."]
}"""

    for modelo in MODELOS:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/' + modelo + ':generateContent?key=' + key
        body = {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'temperature': 0.4, 'responseMimeType': 'application/json'}
        }
        try:
            r = requests.post(url, json=body, timeout=90)
        except Exception as e:
            diag.append(modelo + ': error de red ' + type(e).__name__ + ' ' + sanear(e, key))
            time.sleep(2)
            continue
        if r.status_code == 429:
            diag.append(modelo + ': HTTP 429 limite de uso alcanzado, espera unos minutos')
            time.sleep(2)
            continue
        if r.status_code in (400, 401, 403):
            diag.append(modelo + ': HTTP ' + str(r.status_code) + ' (clave invalida o sin permiso) ' + sanear(r.text, key))
            time.sleep(2)
            continue
        if r.status_code != 200:
            diag.append(modelo + ': HTTP ' + str(r.status_code) + ' ' + sanear(r.text, key))
            time.sleep(2)
            continue
        try:
            txt = r.json()['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            diag.append(modelo + ': respuesta sin contenido util (' + type(e).__name__ + ')')
            time.sleep(2)
            continue
        try:
            ia = json.loads(limpiar_json(txt))
        except Exception as e:
            diag.append(modelo + ': JSON invalido (' + type(e).__name__ + ') inicio: ' + sanear(limpiar_json(txt)[:200], key))
            time.sleep(2)
            continue
        faltan = [k for k in ('resumen', 'win_rate_por_estrategia', 'reglas_violadas', 'lecciones', 'recomendaciones') if k not in ia]
        if faltan:
            diag.append(modelo + ': JSON sin claves obligatorias: ' + ', '.join(faltan))
            time.sleep(2)
            continue
        return {'ok': True, 'motivo': '', 'diagnosticos': diag, 'datos': ia}

    motivo = ('Gemini fallo en todos los modelos. ' + ' || '.join(diag)) if diag else 'Gemini fallo en todos los modelos.'
    return {'ok': False, 'motivo': motivo, 'diagnosticos': diag, 'datos': None}

# ==========================================
# CARGA DE DATOS DEL RADAR
# ==========================================

@st.cache_data(ttl=60)
def cargar_datos():
    url_forzada = URL_CSV + '&t=' + str(datetime.now().timestamp())
    return pd.read_csv(url_forzada)

def leer_fecha_sheet():
    try:
        df_tmp = pd.read_csv(URL_CSV, nrows=1)
        return str(df_tmp['Fecha_Hora_Escaneo'].iloc[0])
    except Exception:
        return None

def estado_robot():
    try:
        url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/actualizar_radar.yml/runs?per_page=1'
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        run = data['workflow_runs'][0]
        return run['status'], run['conclusion']
    except Exception:
        return None, None

def lanzar_dispatch():
    token = st.secrets["GH_TOKEN"]
    url = "https://api.github.com/repos/" + REPO + "/actions/workflows/actualizar_radar.yml/dispatches"
    req = urllib.request.Request(
        url,
        data=json.dumps({"ref": "main"}).encode("utf-8"),
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    urllib.request.urlopen(req)

def auto_dispatch_faltante():
    ahora = datetime.now(ZONA_NY)
    if ahora.weekday() > 4:
        return
    pendientes = [t for t in SCAN_TIMES if (ahora.hour, ahora.minute) >= t]
    if not pendientes:
        return
    obj = pendientes[-1]
    obj_dt = datetime(ahora.year, ahora.month, ahora.day, obj[0], obj[1])
    fecha_sheet = leer_fecha_sheet()
    if fecha_sheet:
        try:
            f_sheet = datetime.strptime(fecha_sheet, '%Y-%m-%d %H:%M:%S')
            if f_sheet >= obj_dt:
                return
        except Exception:
            pass
    st_status, _ = estado_robot()
    if st_status in ('in_progress', 'queued'):
        return
    clave = ahora.strftime('%Y-%m-%d') + '-' + str(obj[0]) + ':' + str(obj[1])
    if st.session_state.get('auto_dispatch_hecho') == clave:
        return
    st.session_state['auto_dispatch_hecho'] = clave
    try:
        lanzar_dispatch()
        st.session_state['esperando'] = True
        st.session_state['hora_lanzamiento'] = ahora.strftime('%Y-%m-%d %H:%M:%S')
        st.rerun()
    except Exception:
        pass

@st.cache_data(ttl=300)
def serie(ticker, intervalo, periodo):
    df = yf.Ticker(ticker).history(period=periodo, interval=intervalo)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# ==========================================
# VERIFICACION DE ESTRATEGIAS (METODO CARDONA)
# ==========================================

def requisitos_cardona(df1h, df1d):
    d = df1d.copy()
    d['SMA100'] = d['Close'].rolling(100).mean()
    d['SMA200'] = d['Close'].rolling(200).mean()
    close_d = float(d['Close'].iloc[-1])
    o_d = float(d['Open'].iloc[-1])
    h_d = float(d['High'].iloc[-1])
    c_d = float(d['Close'].iloc[-1])
    sma100 = float(d['SMA100'].iloc[-1])
    sma200 = float(d['SMA200'].iloc[-1])
    piso_fuerte = (abs(close_d - sma100) / sma100 <= 0.02) or (abs(close_d - sma200) / sma200 <= 0.02)
    lejos_pisos = (abs(close_d - sma100) / sma100 > 0.03) and (abs(close_d - sma200) / sma200 > 0.03)
    cuerpo_d = abs(c_d - o_d)
    sombra_d = h_d - max(o_d, c_d)
    hanger_diario = sombra_d > cuerpo_d

    h = df1h.copy()
    h['SMA20'] = h['Close'].rolling(20).mean()
    h['SMA40'] = h['Close'].rolling(40).mean()
    close_h = float(h['Close'].iloc[-1])
    sma20 = float(h['SMA20'].iloc[-1])
    sma40h = float(h['SMA40'].iloc[-1])
    alcista_h = close_h > sma40h
    dist_pm40 = abs(close_h - sma40h) / sma40h
    cerca_pm40 = dist_pm40 <= 0.015
    canal_bajista = (sma40h > sma20) and (sma40h < float(h['SMA40'].iloc[-4]))

    h['fecha'] = h.index.date
    ultima_fecha = h['fecha'].iloc[-1]
    hoy = h[h['fecha'] == ultima_fecha]
    antes = h[h['fecha'] < ultima_fecha]
    if len(antes) > 0:
        prev_close = float(antes['Close'].iloc[-1])
    else:
        prev_close = float(h['Close'].iloc[-2])

    gap_alza = False
    gap_bajista = False
    primera_roja = False
    primera_verde = False
    piso_gap = None
    if len(hoy) >= 1:
        open_hoy = float(hoy['Open'].iloc[0])
        gap_alza = open_hoy > prev_close
        gap_bajista = open_hoy < prev_close
        primera = hoy.iloc[0]
        primera_roja = float(primera['Close']) < float(primera['Open'])
        primera_verde = float(primera['Close']) > float(primera['Open'])
        piso_gap = float(primera['Low'])

    ruptura_piso_gap = False
    if primera_verde and piso_gap is not None and len(hoy) > 1:
        ruptura_piso_gap = bool((hoy.iloc[1:]['Close'] < piso_gap).any())

    ultima_vela = h.iloc[-1]
    vela_verde = float(ultima_vela['Close']) > float(ultima_vela['Open'])
    vela_roja = float(ultima_vela['Close']) < float(ultima_vela['Open'])
    techo_previo = float(h['High'].iloc[-21:-1].max())
    ruptura_techo = close_h > techo_previo
    piso_linea = float(h['Low'].iloc[-6:-1].min())
    ruptura_piso = vela_roja and close_h < piso_linea

    estrats = []
    estrats.append({
        'nombre': 'CALL 1: Piso Fuerte (PM100/200) + Ruptura',
        'entrada': 'Entrada a partir de las 11:00',
        'checks': [
            ('Diario: en piso fuerte (PM100/PM200 +-2%)', piso_fuerte),
            ('Hora: tendencia alcista (precio > PM40)', alcista_h),
            ('Vela verde rompe techo / linea bajista', vela_verde and ruptura_techo),
        ],
        'humana': 'Verifica: vela verde FORMADA a partir de las 11:00 rompiendo la linea bajista. La subida suele durar 2 a 4 dias.'
    })
    estrats.append({
        'nombre': 'CALL 2: Rebote PM40 / Caida Normal',
        'entrada': 'Entrada a partir de las 11:00',
        'checks': [
            ('Tendencia alcista en hora (precio > PM40)', alcista_h),
            ('Caida que se acerco al PM40 (1.5% o menos)', cerca_pm40),
            ('Vela verde rompe la linea bajista de la caida', vela_verde and ruptura_techo),
        ],
        'humana': 'Verifica: traza la linea bajista de la caida y espera la vela verde final formada desde las 11:00.'
    })
    estrats.append({
        'nombre': 'CALL 3: Gap Bajista al Alza',
        'entrada': 'Entrada a las 11:00',
        'checks': [
            ('Abrio abajo vs cierre anterior (gap bajista)', gap_bajista),
            ('Primera vela de hora verde', primera_verde),
            ('Tendencia alcista en hora', alcista_h),
        ],
        'humana': 'Verifica: dos velas verdes solidas hasta las 11:00. NO comprar dentro de canales bajistas.'
    })
    estrats.append({
        'nombre': 'PUT 1: Primera Vela Roja de Apertura',
        'entrada': 'UNICA que entra a las 10:00 en punto',
        'checks': [
            ('Primera vela de hora roja (martillo rojo tambien vale)', primera_roja),
            ('NO esta en piso fuerte', not piso_fuerte),
            ('NO esta en zona barata (lejos de PM100/200)', lejos_pisos),
        ],
        'humana': 'Verifica: vela formada a las 10:00. Si aparece sobre piso fuerte o zona barata, tiende a fallar: NO aplicar.'
    })
    estrats.append({
        'nombre': 'PUT 2: Ruptura del Piso del Gap',
        'entrada': 'Entrada desde las 11:00',
        'checks': [
            ('Abrio con gap y primera vela verde', primera_verde and (gap_alza or gap_bajista)),
            ('Vela roja rompe el piso del gap', ruptura_piso_gap),
            ('Lejos del PM40 (mayor probabilidad de exito)', not cerca_pm40),
        ],
        'humana': 'Verifica: ruptura con vela roja FORMADA desde las 11:00 en adelante. Puede dar el 100% el mismo dia o al siguiente.'
    })
    estrats.append({
        'nombre': 'PUT 3: Canal Bajista (Modelo 4 Pasos)',
        'entrada': 'Entrada desde las 11:00',
        'checks': [
            ('Paso 1: canal bajista (PM40 sobre PM20, descendente)', canal_bajista),
            ('Paso 2: zona cara / techo', lejos_pisos or dist_pm40 > 0.015),
            ('Paso 3: intento de subida borrado por velas rojas', ruptura_piso),
            ('Paso 4: vela roja rompe la linea de piso trazada', ruptura_piso),
        ],
        'humana': 'Verifica: traza la linea de piso siguiendo la subida; entra cuando una vela roja la rompa.'
    })
    estrats.append({
        'nombre': 'PUT 4: Hanger en Diario',
        'entrada': 'Compra cerca del cierre (4:00 PM / SPY 4:14 PM)',
        'checks': [
            ('Hanger en diario (cola superior mayor al cuerpo)', hanger_diario),
            ('Zona cara o lejos de pisos fuertes', lejos_pisos),
        ],
        'humana': 'Verifica: la vela puede cambiar durante el dia; confirma cerca del cierre. El color no importa.'
    })
    return estrats

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================

st.title("RADAR DE FRANCOTIRADOR - DSS TRADING")

GC = conectar_sheet()
KEY_G = obtener_key_gemini()
sh_sim, ws_sim = abrir_sim(GC)
filas_sim = leer_sim(ws_sim) if ws_sim else []

try:
    df = cargar_datos()
except Exception as e:
    st.error("No se pudo leer el Google Sheet: " + str(e))
    st.stop()

if df.empty:
    st.error("El Google Sheet esta vacio. Ejecuta el robot primero.")
    st.stop()

columnas_necesarias = [
    'Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H',
    'SMA 40 (1H)', 'Estrategia Cardona', 'Condicion 1: Tendencia',
    'Condicion 2: Distancia PM40', 'Condicion 3: Zona Diario',
    'Validación Humana', 'Call Estado', 'Put Estado',
    'Call Ask ($)', 'Put Ask ($)', 'Strike Call OTM', 'Strike Put OTM', 'Vencimiento'
]

columnas_faltantes = [c for c in columnas_necesarias if c not in df.columns]
if columnas_faltantes:
    st.error("Faltan columnas en el Sheet: " + str(columnas_faltantes))
    st.stop()

df['Call Estado'] = df['Call Estado'].astype(str).str.strip()
df['Put Estado'] = df['Put Estado'].astype(str).str.strip()

fecha = df['Fecha_Hora_Escaneo'].iloc[0]
st.caption("Ultimo escaneo (hora Nueva York): " + str(fecha))

auto_dispatch_faltante()

if st.session_state['aviso_listo']:
    st.success("LISTO. El escaneo llego: los datos ya estan actualizados.")
    st.session_state['aviso_listo'] = False

if st.session_state['esperando']:
    st_autorefresh(interval=30000, key="autorefresh_radar")
    try:
        lanz_dt = datetime.strptime(st.session_state.get('hora_lanzamiento', ''), '%Y-%m-%d %H:%M:%S')
        minutos = max(0.0, (datetime.now(ZONA_NY) - lanz_dt).total_seconds() / 60.0)
    except Exception:
        minutos = 0.0

    fecha_sheet = leer_fecha_sheet()
    listo = False
    if fecha_sheet and st.session_state.get('hora_lanzamiento'):
        try:
            listo = datetime.strptime(fecha_sheet, '%Y-%m-%d %H:%M:%S') > datetime.strptime(st.session_state['hora_lanzamiento'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            listo = False

    if listo:
        st.session_state['esperando'] = False
        st.session_state['aviso_listo'] = True
        cargar_datos.clear()
        st.rerun()
    else:
        st.warning("Escaneo en curso... reviso cada 30 segundos y te aviso aqui mismo.")
        st.progress(min(minutos / 15.0, 1.0), text="Robot trabajando... minuto " + str(int(minutos)) + " de 15")
        st_status, st_conclusion = estado_robot()
        if st_status == 'completed':
            if st_conclusion == 'success':
                st.info("El robot YA termino. En menos de 1 minuto veras el aviso verde.")
            else:
                st.error("El robot fallo. Revisa GitHub Actions.")
        elif st_status in ('in_progress', 'queued'):
            st.caption("Estado en GitHub Actions: trabajando.")
        if st.button("Cancelar espera"):
            st.session_state['esperando'] = False
            st.rerun()

# ==========================================
# CONTADORES GLOBALES
# ==========================================

total = len(df)
calls_v = int((df['Call Estado'] == 'VIABLE').sum())
puts_v = int((df['Put Estado'] == 'VIABLE').sum())
latentes = max(0, total - calls_v - puts_v)

k1, k2, k3, k4 = st.columns(4)
k1.metric("CALLs VIABLES", calls_v)
k2.metric("PUTs VIABLES", puts_v)
k3.metric("LATENTES (En espera)", latentes)
k4.metric("ACTIVOS ESCANEADOS", total)
st.caption("CALL + PUT + LATENTES = " + str(total) + " empresas.")

st.divider()

# ==========================================
# TABLERO OTM
# ==========================================

st.subheader("Tablero de Inversion OTM (Metodo Cardona)")

df_ops = df.copy()
df_ops['Spot Num'] = pd.to_numeric(df_ops['Precio Spot'], errors='coerce')
df_ops['Strike Call Num'] = pd.to_numeric(df_ops['Strike Call OTM'], errors='coerce')
df_ops['Strike Put Num'] = pd.to_numeric(df_ops['Strike Put OTM'], errors='coerce')
df_ops['Call Ask Num'] = pd.to_numeric(df_ops['Call Ask ($)'], errors='coerce')
df_ops['Put Ask Num'] = pd.to_numeric(df_ops['Put Ask ($)'], errors='coerce')

def lado_fila(r):
    if r['Call Estado'] == 'VIABLE':
        return 'CALL'
    if r['Put Estado'] == 'VIABLE':
        return 'PUT'
    return 'LATENTE'

df_ops['Lado'] = df_ops.apply(lado_fila, axis=1)

def strike_fila(r):
    if r['Lado'] == 'CALL':
        return r['Strike Call Num']
    if r['Lado'] == 'PUT':
        return r['Strike Put Num']
    return None

df_ops['Strike OTM'] = df_ops.apply(strike_fila, axis=1)

def ask_fila(r):
    if r['Lado'] == 'CALL':
        return r['Call Ask Num']
    if r['Lado'] == 'PUT':
        return r['Put Ask Num']
    return None

df_ops['Ask Num'] = df_ops.apply(ask_fila, axis=1)
df_ops['Costo x contrato'] = df_ops['Ask Num'] * 100.0

def dist_otm(r):
    spot = r['Spot Num']
    strike = r['Strike OTM']
    if pd.isna(spot) or pd.isna(strike) or spot == 0:
        return None
    if r['Lado'] == 'CALL':
        return round((strike - spot) / spot * 100.0, 2)
    if r['Lado'] == 'PUT':
        return round((spot - strike) / spot * 100.0, 2)
    return None

df_ops['Dist OTM %'] = df_ops.apply(dist_otm, axis=1)

df_viables = df_ops[df_ops['Lado'] != 'LATENTE'].copy()

if not df_viables.empty:
    df_viables['Ask Formato'] = df_viables['Ask Num'].map(lambda v: f"{v:.2f}" if pd.notna(v) else "N/A")
    df_viables['Costo Formato'] = df_viables['Costo x contrato'].map(lambda v: f"{v:.2f}" if pd.notna(v) else "N/A")
    df_viables['Dist Formato'] = df_viables['Dist OTM %'].map(lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A")

    tabla_viables = df_viables[[
        'Ticker', 'Lado', 'Estrategia Cardona', 'Strike OTM',
        'Ask Formato', 'Costo Formato', 'Dist Formato', 'Vencimiento', 'Validación Humana'
    ]].rename(columns={
        'Ask Formato': 'Ask ($)',
        'Costo Formato': 'Costo por contrato ($)',
        'Dist Formato': 'Distancia OTM'
    })
    st.dataframe(tabla_viables, width='stretch', hide_index=True)
    st.caption("Costo por contrato = Ask x 100. Solo compra con vela FORMADA en el horario de Validacion Humana.")
else:
    st.info("Hoy no hay senales activas. Revisa las LATENTES abajo.")

df_lat = df_ops[df_ops['Lado'] == 'LATENTE'].copy()
if not df_lat.empty:
    with st.expander("Empresas LATENTES (esperar senal, NO comprar aun)"):
        st.dataframe(
            df_lat[['Ticker', 'Precio Spot', 'Condicion 3: Zona Diario', 'Estrategia Cardona']],
            width='stretch', hide_index=True
        )

st.divider()

# ==========================================
# SIDEBAR: CONTROL + SIMULADOR
# ==========================================

st.sidebar.header("Panel de Control")
ticker_sel = st.sidebar.selectbox("Empresa para grafica", df['Ticker'].tolist())
st.sidebar.caption("Modo automatico: la app se refresca cada 5 min y lanza sola el escaneo faltante del horario Cardona.")

st.sidebar.markdown("---")
st.sidebar.header("SIMULADOR - AUTOPILOTO")

if ws_sim is None:
    st.sidebar.warning("Simulador sin conexion de escritura. Causa: " + (CRED_ERROR or "agrega GOOGLE_CREDENTIALS en Secrets de Streamlit Cloud."))

abiertas = [f for f in filas_sim if str(f.get('Estado', '')) == 'ABIERTA']
cerradas = [f for f in filas_sim if str(f.get('Estado', '')) == 'CERRADA']

pnl_total = 0.0
wins = 0
for f in cerradas:
    try:
        g = float(f.get('Ganancia $', 0) or 0)
        pnl_total += g
        if g > 0:
            wins += 1
    except Exception:
        pass
win_rate = round(wins / len(cerradas) * 100.0, 1) if cerradas else 0.0

m1, m2 = st.sidebar.columns(2)
m1.metric("P&L TOTAL", f"{pnl_total:+.2f}")
m2.metric("ACIERTOS", f"{win_rate}%")
m3, m4 = st.sidebar.columns(2)
m3.metric("ABIERTAS", len(abiertas))
m4.metric("CERRADAS", len(cerradas))

st.sidebar.subheader("Senales de hoy")
tickers_con_posicion = set(str(f.get('Simbolo', '')) for f in abiertas)

senales = df_ops[df_ops['Lado'] != 'LATENTE']
if ws_sim is not None and not senales.empty:
    for _, s in senales.iterrows():
        tk = s['Ticker']
        if tk in tickers_con_posicion:
            continue
        ask_v = s['Ask Num']
        strike_v = s['Strike OTM']
        if pd.isna(ask_v) or ask_v <= 0 or pd.isna(strike_v):
            continue
        costo = ask_v * 100.0
        if costo > MAX_INVERSION:
            continue
        qty = 2 if costo <= 15.0 else 1
        st.sidebar.caption(tk + " " + s['Lado'] + " | Strike " + str(strike_v) + " | Ask " + f"{ask_v:.2f}" + " | " + str(qty) + " contrato(s)")
        if st.sidebar.button("COMPRAR " + tk, key="buy_" + tk):
            ahora = datetime.now(ZONA_NY)
            venc = str(s['Vencimiento'])
            try:
                dte = (datetime.strptime(venc, '%Y-%m-%d').date() - ahora.date()).days
            except Exception:
                dte = 0
            f_prog = viernes_venta_prog(ahora.date())
            be = round(strike_v + ask_v, 2) if s['Lado'] == 'CALL' else round(strike_v - ask_v, 2)
            lotes = ['lote meta +100%', 'lote corredor (IA/reversion)'] if qty == 2 else ['lote unico: meta o viernes']
            for nota_lote in lotes:
                lim = round(ask_v * META_GAIN, 2) if 'meta' in nota_lote or 'unico' in nota_lote else ''
                filas_sim.append({
                    'NOM': 'MANUAL', 'Fecha': ahora.strftime('%Y-%m-%d'), 'Hora': ahora.strftime('%H:%M:%S'),
                    'Simbolo': tk, 'Strike': strike_v, 'F. Exp': venc, 'Call/Put': s['Lado'],
                    'Cantidad': 1, 'Precio Compra': ask_v, 'Total Inv.': round(costo, 2),
                    'Precio Limit': lim, 'Fecha Venta Prog': f_prog.strftime('%Y-%m-%d'),
                    'Fecha Venta': '', 'Precio Venta': '', 'Total Venta': '', 'Ganancia $': '', 'Ganancia %': '',
                    'Bid Actual': '', 'Max Bid': '', 'Estrategia': s['Estrategia Cardona'], 'Estado': 'ABIERTA',
                    'Notas': nota_lote + ' | compra manual', 'VI': '', 'DTE': dte, 'Break Even': be,
                    'Max Loss': round(costo + COMISION, 2)
                })
            if escribir_sim(ws_sim, filas_sim):
                st.sidebar.success("Compra MANUAL registrada: " + tk)
                st.rerun()
elif ws_sim is not None:
    st.sidebar.caption("Sin senales comprables hoy (regla de $30 o ya con posicion).")

st.sidebar.subheader("Posiciones abiertas")
if ws_sim is not None and abiertas:
    for i, f in enumerate(filas_sim):
        if str(f.get('Estado', '')) != 'ABIERTA':
            continue
        g = gan_pct_viva(f)
        st.sidebar.caption(
            str(f.get('Simbolo')) + " " + str(f.get('Call/Put')) + " | Strike " + str(f.get('Strike')) +
            " | compro " + str(f.get('Precio Compra')) + " | Bid " + str(f.get('Bid Actual', '-')) +
            " | " + f"{g:+.0f}%" + " | venta " + str(f.get('Fecha Venta Prog'))
        )
        if st.sidebar.button("VENDER " + str(f.get('Simbolo')) + " " + str(f.get('Strike')), key="sell_" + str(i)):
            bid = bid_actual_posicion(str(f.get('Simbolo')), str(f.get('F. Exp')), f.get('Strike'), str(f.get('Call/Put')))
            if bid <= 0:
                st.sidebar.error("No hay Bid disponible ahora; intenta en horario de mercado.")
            else:
                cerrar_fila(f, bid, datetime.now(ZONA_NY).strftime('%Y-%m-%d'), 'venta manual')
                if escribir_sim(ws_sim, filas_sim):
                    st.sidebar.success("Venta MANUAL registrada.")
                    st.rerun()
else:
    st.sidebar.caption("Sin posiciones abiertas.")

st.sidebar.markdown("---")
st.sidebar.header("Actualizacion")
if st.sidebar.button("Lanzar escaneo ahora"):
    try:
        lanzar_dispatch()
        st.session_state['esperando'] = True
        st.session_state['hora_lanzamiento'] = datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M:%S')
        st.sidebar.success("Escaneo lanzado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error("No se pudo lanzar el escaneo: " + str(e))

if st.sidebar.button("Recargar datos"):
    cargar_datos.clear()
    st.rerun()

# ==========================================
# DETALLE Y TARJETA OTM
# ==========================================

fila = df_ops[df_ops['Ticker'] == ticker_sel]
if not fila.empty:
    r = fila.iloc[0]
    st.subheader(ticker_sel + ": " + str(r['Estrategia Cardona']))
    a, b, c, d = st.columns(4)
    a.write("**Cond 1:** " + str(r['Condicion 1: Tendencia']))
    b.write("**Cond 2:** " + str(r['Condicion 2: Distancia PM40']))
    c.write("**Cond 3:** " + str(r['Condicion 3: Zona Diario']))
    d.write("**Validacion:** " + str(r['Validación Humana']))

    st.markdown("**Tarjeta OTM de " + ticker_sel + "**")
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Opcion CALL (strike arriba)**")
        st.write("Strike OTM: " + str(r['Strike Call OTM']))
        st.write("Ask ($): " + str(r['Call Ask ($)']))
        if r['Call Estado'] == 'VIABLE':
            st.success(CHECK + " CALL habilitado por el metodo")
        else:
            st.warning(CRUZ + " CALL no viable")
    with t2:
        st.markdown("**Opcion PUT (strike abajo)**")
        st.write("Strike OTM: " + str(r['Strike Put OTM']))
        st.write("Ask ($): " + str(r['Put Ask ($)']))
        if r['Put Estado'] == 'VIABLE':
            st.success(CHECK + " PUT habilitado por el metodo")
        else:
            st.warning(CRUZ + " PUT no viable")

    if r['Lado'] == 'CALL':
        st.success(FUEGO + " RECOMENDACION CARDONA: comprar CALL strike " + str(r['Strike Call OTM']) +
                   " | Ask $" + str(r['Call Ask ($)']) + " | Vence " + str(r['Vencimiento']) +
                   " | Entrada: " + str(r['Validación Humana']))
    elif r['Lado'] == 'PUT':
        st.success(FUEGO + " RECOMENDACION CARDONA: comprar PUT strike " + str(r['Strike Put OTM']) +
                   " | Ask $" + str(r['Put Ask ($)']) + " | Vence " + str(r['Vencimiento']) +
                   " | Entrada: " + str(r['Validación Humana']))
    else:
        st.warning("Sin senal viable hoy: LATENTE. Espera a que se forme la estrategia.")

# ==========================================
# GRAFICOS Y VERIFICACION CON FUEGO
# ==========================================

df1h = serie(ticker_sel, "1h", "60d")
df1d = serie(ticker_sel, "1d", "1y")

st.subheader("Verificacion de Estrategias (Metodo Cardona)")
st.caption(FUEGO + " = estrategia con TODOS los requisitos cumplidos.")

for e in requisitos_cardona(df1h, df1d):
    cumplidos = sum(1 for _, ok in e['checks'] if ok)
    total_e = len(e['checks'])
    if cumplidos == total_e:
        estado = FUEGO + " LISTA PARA VERIFICAR"
    else:
        estado = str(cumplidos) + "/" + str(total_e) + " requisitos"
    with st.expander(e['nombre'] + "  --  " + estado):
        for texto, ok in e['checks']:
            if ok:
                st.markdown(CHECK + " " + texto)
            else:
                st.markdown(CRUZ + " " + texto)
        st.markdown("**" + e['entrada'] + "**")
        st.info(e['humana'])
        st.checkbox("Lo verifique en el grafico de " + ticker_sel, key=e['nombre'])

df1h['SMA40'] = df1h['Close'].rolling(40).mean()
fig1 = go.Figure()
fig1.add_trace(go.Candlestick(
    x=df1h.index, open=df1h['Open'], high=df1h['High'],
    low=df1h['Low'], close=df1h['Close'], name=ticker_sel))
fig1.add_trace(go.Scatter(x=df1h.index, y=df1h['SMA40'], name='SMA 40', line=dict(color='orange', width=2)))
fig1.update_layout(title=ticker_sel + " - Velas 1H + SMA 40", xaxis_rangeslider_visible=False, height=420)

df1d['SMA100'] = df1d['Close'].rolling(100).mean()
df1d['SMA200'] = df1d['Close'].rolling(200).mean()
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['Close'], name='Precio', line=dict(color='blue', width=2)))
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['SMA100'], name='SMA 100', line=dict(color='green', width=1.5)))
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['SMA200'], name='SMA 200', line=dict(color='red', width=1.5)))
fig2.update_layout(title=ticker_sel + " - Diario: Piso 100/200", height=420)

g1, g2 = st.columns(2)
with g1:
    st.plotly_chart(fig1, width='stretch')
with g2:
    st.plotly_chart(fig2, width='stretch')

st.divider()

# ==========================================
# RADAR DE ACTIVOS
# ==========================================

st.subheader("Radar de Activos")
df_show = df.copy()
df_show['Tendencia 1H'] = df_show['Tendencia 1H'].map(
    lambda x: ALTA + " Alcista" if x == 'Alcista' else BAJA + " Bajista"
)
cols = [
    'Ticker', 'Precio Spot', 'Tendencia 1H', 'SMA 40 (1H)', 'Estrategia Cardona',
    'Validación Humana', 'Call Ask ($)', 'Call Estado', 'Put Ask ($)', 'Put Estado'
]
st.dataframe(
    df_show[[c for c in cols if c in df_show.columns]],
    width='stretch', hide_index=True
)

st.divider()

# ==========================================
# HISTORIAL + INFORME SEMANAL IA
# ==========================================

st.subheader("Historial de operaciones (SIMULADOR)")
if cerradas:
    hist = pd.DataFrame(cerradas)
    hist_show = hist[[c for c in ['Fecha', 'Simbolo', 'Call/Put', 'Strike', 'Precio Compra', 'Precio Venta', 'Ganancia $', 'Ganancia %', 'Estrategia', 'Notas'] if c in hist.columns]]
    st.dataframe(hist_show, width='stretch', hide_index=True)
else:
    st.caption("Aun no hay operaciones cerradas.")

st.subheader("Informe semanal de la IA (aprendizaje con la GUIA)")
if st.button("Generar informe semanal con IA"):
    if not cerradas:
        st.info("Aun no hay operaciones cerradas para analizar.")
    else:
        with st.spinner("Gemini analizando el historial contra la GUIA..."):
            ws_ia = abrir_informe_ia(sh_sim)
            lecciones_previas = ''
            if ws_ia is not None:
                try:
                    regs = ws_ia.get_all_records()
                    lecciones_previas = " | ".join([str(x.get('Lecciones', '')) for x in regs if x.get('Lecciones')][-5:])
                except Exception:
                    lecciones_previas = ''
            res = informe_gemini(cerradas, lecciones_previas, KEY_G)
        if res['ok']:
            ia = res['datos']
            st.markdown("**Resumen:** " + str(ia.get('resumen', '')))
            st.markdown("**Win rate por estrategia:**")
            st.json(ia.get('win_rate_por_estrategia', {}))
            secciones = [
                ("Reglas de la GUIA violadas", 'reglas_violadas'),
                ("Entradas correctas", 'entradas_correctas'),
                ("Salidas correctas", 'salidas_correctas'),
                ("Vencimientos sin valor", 'vencimientos_sin_valor'),
                ("Verificar antes de repetir", 'verificar_antes_de_repetir'),
                ("Lecciones (memoria del sistema)", 'lecciones'),
                ("Recomendaciones para revision humana", 'recomendaciones'),
            ]
            for titulo, clave in secciones:
                items = ia.get(clave, [])
                if items:
                    st.markdown("**" + titulo + ":**")
                    for e in items:
                        st.markdown("- " + str(e))
            if ws_ia is not None:
                try:
                    ws_ia.append_row([
                        datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M'),
                        str(ia.get('resumen', '')),
                        " | ".join([str(x) for x in ia.get('lecciones', [])]),
                        " | ".join([str(x) for x in ia.get('recomendaciones', [])])
                    ])
                    st.caption("Informe guardado en INFORME_IA (solo se agrega; nada se borra).")
                except Exception as e:
                    st.warning("No se pudo guardar el informe en el Sheet: " + str(e))
        else:
            st.error("El informe fallo: " + res['motivo'])
            for d in res['diagnosticos']:
                st.caption(d)
            if ws_ia is not None:
                try:
                    ws_ia.append_row([
                        datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M'),
                        'INFORME FALLIDO: ' + res['motivo'][:400],
                        '', ''
                    ])
                    st.caption("Fallo registrado en INFORME_IA; los informes anteriores quedan intactos.")
                except Exception:
                    pass
