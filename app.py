# ==========================================
# RADAR DE FRANCOTIRADOR - DSS TRADING
# app.py - VERSION v9 (Estadisticas, validacion manual y fusion anti-borrado)
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
ALERTA = "\u26A0\uFE0F"
ALTA = "\u25B2"
BAJA = "\u25BC"

st.set_page_config(page_title="Radar DSS Trading", layout="wide")
st_autorefresh(interval=300000, key="autorefresh_global")

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
URL_CSV = 'https://docs.google.com/spreadsheets/d/' + SPREADSHEET_ID + '/export?format=csv&gid=0'
ZONA_NY = ZoneInfo('America/New_York')
REPO = 'vicroj777888-hub/radar-trading-automation'

MAX_INVERSION = 30.0
META_GAIN = 2.0
COMISION = 0.0
MODELOS = ['gemini-3.6-flash', 'gemini-3.6-flash', 'gemini-flash-latest', 'gemini-flash-latest']

# Ventanas de escaneo (incluye 15:31 para alinearse con main.py v18)
SCAN_TIMES = [(9, 31), (10, 1), (11, 1), (12, 1), (13, 1), (14, 1), (15, 1), (15, 31), (15, 58)]

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

if 'esperando' not in st.session_state: st.session_state['esperando'] = False
if 'aviso_listo' not in st.session_state: st.session_state['aviso_listo'] = False
if 'auto_dispatch_hecho' not in st.session_state: st.session_state['auto_dispatch_hecho'] = ''

# ==========================================
# CONEXIONES Y HOJAS
# ==========================================
CRED_ERROR = ''

@st.cache_resource
def conectar_sheet():
    global CRED_ERROR
    try:
        info = json.loads(st.secrets['GOOGLE_CREDENTIALS'])
        creds = Credentials.from_service_account_info(info, scopes=[
            'https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'
        ])
        return gspread.authorize(creds)
    except Exception as e:
        CRED_ERROR = str(e)
        return None

def obtener_key_gemini():
    try: return st.secrets['GEMINI_API_KEY']
    except Exception: return ''

def abrir_sim(ws_ok):
    if ws_ok is None: return None, None
    try:
        sh = ws_ok.open_by_key(SPREADSHEET_ID)
        try: return sh, sh.worksheet('SIMULADOR')
        except Exception:
            ws = sh.add_worksheet(title='SIMULADOR', rows="200", cols="30")
            ws.append_row(SIM_HEADERS)
            return sh, ws
    except Exception: return None, None

def abrir_informe_ia(sh):
    if sh is None: return None
    try: return sh.worksheet('INFORME_IA')
    except Exception:
        ws = sh.add_worksheet(title='INFORME_IA', rows="100", cols="10")
        ws.append_row(IA_HEADERS)
        return ws

def leer_sim(ws):
    try: return [dict(f) for f in ws.get_all_records()]
    except Exception: return []

# ==========================================
# ESCRITURA SEGURA (FUSION ANTI-BORRADO)
# ==========================================
def clave_fila(f):
    return (str(f.get('NOM', '')), str(f.get('Fecha', '')), str(f.get('Hora', '')),
            str(f.get('Simbolo', '')), str(f.get('Strike', '')), str(f.get('Call/Put', '')),
            str(f.get('Precio Limit', '')))

def fusionar_filas(existentes, nuevas):
    mapa = {}
    for f in existentes: mapa[clave_fila(f)] = f
    for f in nuevas: mapa[clave_fila(f)] = f
    orden, vistas = [], set()
    for f in existentes:
        k = clave_fila(f)
        if k not in vistas: orden.append(mapa[k]); vistas.add(k)
    for f in nuevas:
        k = clave_fila(f)
        if k not in vistas: orden.append(mapa[k]); vistas.add(k)
    return orden

def escribir_simulador_seguro(ws, filas_propuestas):
    for intento in (1, 2, 3):
        try:
            existentes = leer_sim(ws)
            fusion = fusionar_filas(existentes, filas_propuestas)
            tabla = [SIM_HEADERS] + [[str(f.get(h, '')) for h in SIM_HEADERS] for f in fusion]
            ws.resize(rows=len(tabla), cols=len(tabla[0]))
            ws.update(tabla)
            return True
        except Exception:
            time.sleep(2)
    return False

# ==========================================
# HELPERS Y VALIDACIONES
# ==========================================
def viernes_venta_prog(fecha_ny):
    wd = fecha_ny.weekday()
    delta = 4 - wd if wd <= 2 else (8 if wd == 3 else (7 if wd == 4 else (6 if wd == 5 else 5)))
    return fecha_ny + timedelta(days=delta)

def bid_de_cadena(chain, strike, lado):
    try:
        df = chain.calls if lado == 'CALL' else chain.puts
        m = df[df['strike'] == float(strike)]
        if m.empty: return 0.0
        r = m.iloc[0]
        bid = float(r['bid']) if pd.notna(r['bid']) else 0.0
        last = float(r['lastPrice']) if pd.notna(r['lastPrice']) else 0.0
        return round(bid if bid > 0 else last, 2)
    except Exception: return 0.0

def bid_actual_posicion(simbolo, exp, strike, lado):
    try: return bid_de_cadena(yf.Ticker(simbolo).option_chain(exp), strike, lado)
    except Exception: return 0.0

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

def gan_pct_viva(row):
    try:
        compra = float(row['Precio Compra'])
        bid = float(row.get('Bid Actual', 0) or 0)
        if compra > 0 and bid > 0: return (bid / compra - 1.0) * 100.0
    except Exception: pass
    return 0.0

def validar_horario_manual(estrategia, ahora):
    h, m = ahora.hour, ahora.minute
    if ahora.weekday() > 4: return False, "Fin de semana"
    if h < 9 or h >= 16: return False, "Fuera de horario de mercado (9:30-16:00)"
    if estrategia == 'Primera Vela Roja':
        if not (h == 10 and m <= 5): return False, "PVR solo es valida entre 10:00 y 10:05"
    elif estrategia in ('Hanger en Diario', 'Primer Gap al Alza'):
        if not (h >= 15 and m >= 55): return False, "Hanger/Primer Gap solo cerca del cierre (15:55+)"
    else:
        if h < 11: return False, "Estrategias generales solo a partir de las 11:00"
    return True, "Horario valido"

# ==========================================
# ESTADISTICAS Y EXPECTATIVA MATEMATICA
# ==========================================
def calcular_estadisticas(cerradas):
    stats = {}
    for f in cerradas:
        est = str(f.get('Estrategia', 'Desconocida'))
        notas = str(f.get('Notas', ''))
        es_limpia = (str(f.get('NOM', '')) == 'AUTOPILOTO' and
                     '+' not in est and
                     'entrada valida' in notas and
                     'falla de gestion' not in notas and
                     'vencida sin valor' not in notas and
                     'vencida con valor residual' not in notas)
        cubo = 'Limpia (v15+)' if es_limpia else 'Historica / Contaminada'
        if est not in stats:
            stats[est] = {'Limpia (v15+)': {'n':0, 'w':0, 'l':0, 'sum_w':0, 'sum_l':0},
                          'Historica / Contaminada': {'n':0, 'w':0, 'l':0, 'sum_w':0, 'sum_l':0}}
        try: gan = float(f.get('Ganancia $', 0) or 0)
        except: gan = 0.0
        stats[est][cubo]['n'] += 1
        if gan > 0:
            stats[est][cubo]['w'] += 1
            stats[est][cubo]['sum_w'] += gan
        else:
            stats[est][cubo]['l'] += 1
            stats[est][cubo]['sum_l'] += abs(gan)
    return stats

# ==========================================
# GEMINI (INFORME CON REINTENTOS Y DIAGNOSTICO)
# ==========================================
def limpiar_json(txt):
    txt = txt.strip()
    fence = chr(96) * 3
    if txt.startswith(fence):
        txt = txt.replace(fence, '')
        if txt.startswith('json'): txt = txt[4:]
    return txt.strip()

def sanear(txt, key):
    out = str(txt)
    if key: out = out.replace(key, '***')
    return out[:300]

def informe_gemini(cerradas, lecciones_previas, key):
    diag = []
    if not key: return {'ok': False, 'motivo': 'Falta GEMINI_API_KEY en Secrets', 'diagnosticos': diag, 'datos': None}
    lineas = []
    for f in cerradas:
        partes = [
            'entrada ' + str(f.get('Fecha', '')) + ' ' + str(f.get('Hora', '')),
            'cierre ' + str(f.get('Fecha Venta', '')),
            str(f.get('Simbolo')) + ' ' + str(f.get('Call/Put')),
            'strike ' + str(f.get('Strike')), 'exp ' + str(f.get('F. Exp')),
            'estrategia ' + str(f.get('Estrategia')),
            'compra ' + str(f.get('Precio Compra')), 'venta ' + str(f.get('Precio Venta')),
            'bid ' + str(f.get('Bid Actual')), 'bid_max ' + str(f.get('Max Bid')),
            'gan$ ' + str(f.get('Ganancia $')), 'gan% ' + str(f.get('Ganancia %')),
            'motivo_cierre ' + str(f.get('Notas', '')), 'estado ' + str(f.get('Estado', '')),
        ]
        lineas.append(' | '.join(partes))
    prompt = "GUIA OFICIAL DEL METODO CARDONA:\n" + GUIA_CARDONA + "\n\n"
    prompt += "OPERACIONES CERRADAS DEL PAPER TRADING:\n" + "\n".join(lineas) + "\n\n"
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
    
    # Reintentos con espera exponencial para errores 429/503
    for modelo in MODELOS:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/' + modelo + ':generateContent?key=' + key
        body = {'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.4, 'responseMimeType': 'application/json'}}
        for intento in (1, 2):
            try:
                r = requests.post(url, json=body, timeout=90)
            except Exception as e:
                diag.append(modelo + ': error de red ' + type(e).__name__)
                break
            if r.status_code in (429, 503):
                diag.append(modelo + ': HTTP ' + str(r.status_code) + ' (saturacion/limite), reintentando...')
                time.sleep(30 if intento == 1 else 60)
                continue
            if r.status_code in (400, 401, 403):
                diag.append(modelo + ': HTTP ' + str(r.status_code) + ' (clave invalida) ' + sanear(r.text, key))
                break
            if r.status_code != 200:
                diag.append(modelo + ': HTTP ' + str(r.status_code) + ' ' + sanear(r.text, key))
                break
            try: txt = r.json()['candidates'][0]['content']['parts'][0]['text']
            except Exception as e:
                diag.append(modelo + ': respuesta sin contenido util (' + type(e).__name__ + ')')
                break
            try: ia = json.loads(limpiar_json(txt))
            except Exception as e:
                diag.append(modelo + ': JSON invalido (' + type(e).__name__ + ')')
                break
            faltan = [k for k in ('resumen', 'win_rate_por_estrategia', 'reglas_violadas', 'lecciones', 'recomendaciones') if k not in ia]
            if faltan:
                diag.append(modelo + ': JSON sin claves obligatorias: ' + ', '.join(faltan))
                break
            return {'ok': True, 'motivo': '', 'diagnosticos': diag, 'datos': ia}
        time.sleep(2)
    motivo = ('Gemini fallo. ' + ' || '.join(diag)) if diag else 'Gemini fallo.'
    return {'ok': False, 'motivo': motivo, 'diagnosticos': diag, 'datos': None}

# ==========================================
# CARGA DE DATOS Y DISPATCH
# ==========================================
@st.cache_data(ttl=60)
def cargar_datos():
    return pd.read_csv(URL_CSV + '&t=' + str(datetime.now().timestamp()))

def leer_fecha_sheet():
    try: return str(pd.read_csv(URL_CSV, nrows=1)['Fecha_Hora_Escaneo'].iloc[0])
    except Exception: return None

def estado_robot():
    try:
        url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/actualizar_radar.yml/runs?per_page=1'
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        run = data['workflow_runs'][0]
        return run['status'], run['conclusion']
    except Exception: return None, None

def lanzar_dispatch():
    token = st.secrets["GH_TOKEN"]
    url = "https://api.github.com/repos/" + REPO + "/actions/workflows/actualizar_radar.yml/dispatches"
    req = urllib.request.Request(url, data=json.dumps({"ref": "main"}).encode("utf-8"),
        headers={"Authorization": "token " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)

def auto_dispatch_faltante():
    ahora = datetime.now(ZONA_NY)
    if ahora.weekday() > 4: return
    pendientes = [t for t in SCAN_TIMES if (ahora.hour, ahora.minute) >= t]
    if not pendientes: return
    obj = pendientes[-1]
    obj_dt = datetime(ahora.year, ahora.month, ahora.day, obj[0], obj[1])
    fecha_sheet = leer_fecha_sheet()
    if fecha_sheet:
        try:
            if datetime.strptime(fecha_sheet, '%Y-%m-%d %H:%M:%S') >= obj_dt: return
        except Exception: pass
    st_status, _ = estado_robot()
    if st_status in ('in_progress', 'queued'): return
    clave = ahora.strftime('%Y-%m-%d') + '-' + str(obj[0]) + ':' + str(obj[1])
    if st.session_state.get('auto_dispatch_hecho') == clave: return
    st.session_state['auto_dispatch_hecho'] = clave
    try:
        lanzar_dispatch()
        st.session_state['esperando'] = True
        st.session_state['hora_lanzamiento'] = ahora.strftime('%Y-%m-%d %H:%M:%S')
        st.rerun()
    except Exception: pass

@st.cache_data(ttl=300)
def serie(ticker, intervalo, periodo):
    df = yf.Ticker(ticker).history(period=periodo, interval=intervalo)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    return df

# ==========================================
# VERIFICACION DE ESTRATEGIAS (FUEGO)
# ==========================================
def requisitos_cardona(df1h, df1d):
    d = df1d.copy()
    d['SMA100'] = d['Close'].rolling(100).mean()
    d['SMA200'] = d['Close'].rolling(200).mean()
    close_d, o_d, h_d, c_d = float(d['Close'].iloc[-1]), float(d['Open'].iloc[-1]), float(d['High'].iloc[-1]), float(d['Close'].iloc[-1])
    sma100, sma200 = float(d['SMA100'].iloc[-1]), float(d['SMA200'].iloc[-1])
    piso_fuerte = (abs(close_d - sma100) / sma100 <= 0.02) or (abs(close_d - sma200) / sma200 <= 0.02)
    lejos_pisos = (abs(close_d - sma100) / sma100 > 0.03) and (abs(close_d - sma200) / sma200 > 0.03)
    hanger_diario = (h_d - max(o_d, c_d)) > abs(c_d - o_d)

    h = df1h.copy()
    h['SMA20'] = h['Close'].rolling(20).mean()
    h['SMA40'] = h['Close'].rolling(40).mean()
    close_h, sma40h = float(h['Close'].iloc[-1]), float(h['SMA40'].iloc[-1])
    alcista_h, cerca_pm40 = close_h > sma40h, abs(close_h - sma40h) / sma40h <= 0.015
    canal_bajista = (float(h['SMA40'].iloc[-1]) > float(h['SMA20'].iloc[-1])) and (float(h['SMA40'].iloc[-1]) < float(h['SMA40'].iloc[-4]))

    h['fecha'] = h.index.date
    hoy = h[h['fecha'] == h['fecha'].iloc[-1]]
    antes = h[h['fecha'] < h['fecha'].iloc[-1]]
    prev_close = float(antes['Close'].iloc[-1]) if len(antes) > 0 else float(h['Close'].iloc[-2])
    gap_bajista = float(hoy['Open'].iloc[0]) < prev_close if len(hoy) >= 1 else False
    primera_verde = float(hoy.iloc[0]['Close']) > float(hoy.iloc[0]['Open']) if len(hoy) >= 1 else False
    primera_roja = float(hoy.iloc[0]['Close']) < float(hoy.iloc[0]['Open']) if len(hoy) >= 1 else False
    ruptura_piso_gap = bool((hoy.iloc[1:]['Close'] < float(hoy.iloc[0]['Low'])).any()) if primera_verde and len(hoy) > 1 else False

    ultima_vela = h.iloc[-1]
    vela_verde = float(ultima_vela['Close']) > float(ultima_vela['Open'])
    ruptura_techo = close_h > float(h['High'].iloc[-21:-1].max())

    estrats = [
        {'nombre': 'CALL 1: Piso Fuerte + Ruptura', 'entrada': 'A partir de las 11:00', 'checks': [('En piso fuerte', piso_fuerte), ('Tendencia alcista', alcista_h), ('Vela verde rompe techo', vela_verde and ruptura_techo)], 'humana': 'Vela verde FORMADA a partir de las 11:00.'},
        {'nombre': 'CALL 2: Rebote PM40', 'entrada': 'A partir de las 11:00', 'checks': [('Tendencia alcista', alcista_h), ('Cerca al PM40', cerca_pm40), ('Vela verde rompe linea bajista', vela_verde and ruptura_techo)], 'humana': 'Traza la linea bajista y espera la vela verde final.'},
        {'nombre': 'CALL 3: Gap Bajista al Alza', 'entrada': 'A las 11:00', 'checks': [('Gap bajista', gap_bajista), ('Primera vela verde', primera_verde), ('Tendencia alcista', alcista_h)], 'humana': 'NO comprar dentro de canales bajistas.'},
        {'nombre': 'PUT 1: Primera Vela Roja', 'entrada': 'UNICA a las 10:00 en punto', 'checks': [('Primera vela roja', primera_roja), ('NO en piso fuerte', not piso_fuerte), ('Lejos de pisos', lejos_pisos)], 'humana': 'Si aparece sobre piso fuerte, tiende a fallar.'},
        {'nombre': 'PUT 2: Ruptura Piso del Gap', 'entrada': 'Desde las 11:00', 'checks': [('Gap y primera verde', primera_verde), ('Rompe piso del gap', ruptura_piso_gap), ('Lejos del PM40', not cerca_pm40)], 'humana': 'Ruptura con vela roja FORMADA desde las 11:00.'},
        {'nombre': 'PUT 3: Canal Bajista (4 Pasos)', 'entrada': 'Desde las 11:00', 'checks': [('Canal bajista', canal_bajista), ('Zona cara', lejos_pisos or not cerca_pm40), ('Intento borrado', True), ('Rompe linea de piso', True)], 'humana': 'Traza la linea de piso; entra cuando vela roja la rompa.'},
        {'nombre': 'PUT 4: Hanger en Diario', 'entrada': 'Cerca del cierre (15:55+)', 'checks': [('Hanger en diario', hanger_diario), ('Zona cara', lejos_pisos)], 'humana': 'Confirma cerca del cierre. El color no importa.'}
    ]
    return estrats

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================
st.title("RADAR DE FRANCOTIRADOR - DSS TRADING")
st.caption("MODO SIMULACIÓN — NO SE ENVÍAN ÓRDENES REALES")

GC = conectar_sheet()
KEY_G = obtener_key_gemini()
sh_sim, ws_sim = abrir_sim(GC)
filas_sim = leer_sim(ws_sim) if ws_sim else []

try: df = cargar_datos()
except Exception as e: st.error("No se pudo leer el Sheet: " + str(e)); st.stop()
if df.empty: st.error("Sheet vacio."); st.stop()

df['Call Estado'] = df['Call Estado'].astype(str).str.strip()
df['Put Estado'] = df['Put Estado'].astype(str).str.strip()
st.caption("Ultimo escaneo: " + str(df['Fecha_Hora_Escaneo'].iloc[0]))

auto_dispatch_faltante()
if st.session_state['aviso_listo']:
    st.success("LISTO. Datos actualizados.")
    st.session_state['aviso_listo'] = False

if st.session_state['esperando']:
    st_autorefresh(interval=30000, key="autorefresh_radar")
    try: minutos = max(0.0, (datetime.now(ZONA_NY) - datetime.strptime(st.session_state.get('hora_lanzamiento', ''), '%Y-%m-%d %H:%M:%S')).total_seconds() / 60.0)
    except: minutos = 0.0
    fecha_sheet = leer_fecha_sheet()
    listo = False
    if fecha_sheet and st.session_state.get('hora_lanzamiento'):
        try: listo = datetime.strptime(fecha_sheet, '%Y-%m-%d %H:%M:%S') > datetime.strptime(st.session_state['hora_lanzamiento'], '%Y-%m-%d %H:%M:%S')
        except: pass
    if listo:
        st.session_state['esperando'] = False; st.session_state['aviso_listo'] = True; cargar_datos.clear(); st.rerun()
    else:
        st.warning("Escaneo en curso...")
        st.progress(min(minutos / 15.0, 1.0))
        if st.button("Cancelar espera"): st.session_state['esperando'] = False; st.rerun()

# Contadores
total = len(df)
calls_v = int((df['Call Estado'] == 'VIABLE').sum())
puts_v = int((df['Put Estado'] == 'VIABLE').sum())
k1, k2, k3, k4 = st.columns(4)
k1.metric("CALLs VIABLES", calls_v); k2.metric("PUTs VIABLES", puts_v)
k3.metric("LATENTES", max(0, total - calls_v - puts_v)); k4.metric("ACTIVOS", total)
st.divider()

# Tablero OTM
st.subheader("Tablero OTM")
df_ops = df.copy()
for c in ['Precio Spot', 'Strike Call OTM', 'Strike Put OTM', 'Call Ask ($)', 'Put Ask ($)']:
    df_ops[c + ' Num'] = pd.to_numeric(df_ops[c], errors='coerce')
df_ops['Lado'] = df_ops.apply(lambda r: 'CALL' if r['Call Estado'] == 'VIABLE' else ('PUT' if r['Put Estado'] == 'VIABLE' else 'LATENTE'), axis=1)
df_ops['Strike OTM'] = df_ops.apply(lambda r: r['Strike Call OTM Num'] if r['Lado'] == 'CALL' else (r['Strike Put OTM Num'] if r['Lado'] == 'PUT' else None), axis=1)
df_ops['Ask Num'] = df_ops.apply(lambda r: r['Call Ask ($) Num'] if r['Lado'] == 'CALL' else (r['Put Ask ($) Num'] if r['Lado'] == 'PUT' else None), axis=1)
df_viables = df_ops[df_ops['Lado'] != 'LATENTE'].copy()
if not df_viables.empty:
    tabla_v = df_viables[['Ticker', 'Lado', 'Estrategia Cardona', 'Strike OTM', 'Ask Num', 'Vencimiento', 'Validación Humana']].rename(columns={'Ask Num': 'Ask ($)'})
    st.dataframe(tabla_v, width='stretch', hide_index=True)
st.divider()

# Sidebar
st.sidebar.header("Panel de Control")
ticker_sel = st.sidebar.selectbox("Empresa", df['Ticker'].tolist())
st.sidebar.markdown("---")
st.sidebar.header("SIMULADOR")
abiertas = [f for f in filas_sim if str(f.get('Estado', '')) == 'ABIERTA']
cerradas = [f for f in filas_sim if str(f.get('Estado', '')) == 'CERRADA']
pnl_total = sum(float(f.get('Ganancia $', 0) or 0) for f in cerradas)
wins = sum(1 for f in cerradas if float(f.get('Ganancia $', 0) or 0) > 0)
m1, m2 = st.sidebar.columns(2)
m1.metric("P&L TOTAL", f"{pnl_total:+.2f}"); m2.metric("ACIERTOS", f"{round(wins/len(cerradas)*100,1) if cerradas else 0}%")

st.sidebar.subheader("Senales de hoy")
tickers_con_pos = set(str(f.get('Simbolo', '')) for f in abiertas)
senales = df_ops[df_ops['Lado'] != 'LATENTE']
if ws_sim is not None and not senales.empty:
    for _, s in senales.iterrows():
        tk = s['Ticker']
        if tk in tickers_con_pos: continue
        ask_v, strike_v = s['Ask Num'], s['Strike OTM']
        if pd.isna(ask_v) or ask_v <= 0 or pd.isna(strike_v) or ask_v * 100 > MAX_INVERSION: continue
        st.sidebar.caption(tk + " " + s['Lado'] + " | " + str(strike_v) + " | $" + f"{ask_v:.2f}")
        forzar = st.sidebar.checkbox("Forzar fuera de horario", key="f_" + tk)
        if st.sidebar.button("COMPRAR " + tk, key="b_" + tk):
            ahora = datetime.now(ZONA_NY)
            ok, msg = validar_horario_manual(s['Estrategia Cardona'], ahora)
            if not ok and not forzar:
                st.sidebar.error("Bloqueado: " + msg)
            else:
                nota = 'compra manual' if ok else 'compra manual FORZADA (entrada INVALIDA)'
                venc = str(s['Vencimiento'])
                f_prog = viernes_venta_prog(ahora.date())
                be = round(strike_v + ask_v, 2) if s['Lado'] == 'CALL' else round(strike_v - ask_v, 2)
                filas_sim.append({
                    'NOM': 'MANUAL', 'Fecha': ahora.strftime('%Y-%m-%d'), 'Hora': ahora.strftime('%H:%M:%S'),
                    'Simbolo': tk, 'Strike': strike_v, 'F. Exp': venc, 'Call/Put': s['Lado'], 'Cantidad': 1,
                    'Precio Compra': ask_v, 'Total Inv.': round(ask_v*100, 2), 'Precio Limit': round(ask_v*META_GAIN, 2),
                    'Fecha Venta Prog': f_prog.strftime('%Y-%m-%d'), 'Fecha Venta': '', 'Precio Venta': '', 'Total Venta': '',
                    'Ganancia $': '', 'Ganancia %': '', 'Bid Actual': '', 'Max Bid': '', 'Estrategia': s['Estrategia Cardona'],
                    'Estado': 'ABIERTA', 'Notas': nota + ' | entrada valida (horario Cardona)' if ok else nota,
                    'VI': '', 'DTE': 0, 'Break Even': be, 'Max Loss': round(ask_v*100, 2)
                })
                if escribir_simulador_seguro(ws_sim, filas_sim): st.sidebar.success("Registrada"); st.rerun()

st.sidebar.subheader("Posiciones abiertas")
if ws_sim is not None and abiertas:
    for i, f in enumerate(filas_sim):
        if str(f.get('Estado', '')) != 'ABIERTA': continue
        st.sidebar.caption(str(f.get('Simbolo')) + " " + str(f.get('Call/Put')) + " | " + str(f.get('Strike')) + " | " + f"{gan_pct_viva(f):+.0f}%")
        if st.sidebar.button("VENDER " + str(f.get('Simbolo')), key="s_" + str(i)):
            bid = bid_actual_posicion(str(f.get('Simbolo')), str(f.get('F. Exp')), f.get('Strike'), str(f.get('Call/Put')))
            if bid <= 0: st.sidebar.error("Sin Bid.")
            else:
                cerrar_fila(f, bid, datetime.now(ZONA_NY).strftime('%Y-%m-%d'), 'venta manual')
                if escribir_simulador_seguro(ws_sim, filas_sim): st.sidebar.success("Vendida"); st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("Lanzar escaneo"):
    try: lanzar_dispatch(); st.session_state['esperando'] = True; st.session_state['hora_lanzamiento'] = datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M:%S'); st.rerun()
    except Exception as e: st.sidebar.error(str(e))
if st.sidebar.button("Recargar"): cargar_datos.clear(); st.rerun()

# Detalle y Graficos
fila = df_ops[df_ops['Ticker'] == ticker_sel]
if not fila.empty:
    r = fila.iloc[0]
    st.subheader(ticker_sel + ": " + str(r['Estrategia Cardona']))
    if r['Lado'] == 'CALL': st.success(FUEGO + " RECOMENDACION: CALL " + str(r['Strike Call OTM']) + " | $" + str(r['Call Ask ($)']))
    elif r['Lado'] == 'PUT': st.success(FUEGO + " RECOMENDACION: PUT " + str(r['Strike Put OTM']) + " | $" + str(r['Put Ask ($)']))
    else: st.warning("LATENTE")

df1h, df1d = serie(ticker_sel, "1h", "60d"), serie(ticker_sel, "1d", "1y")
st.subheader("Verificacion (FUEGO = Lista)")
for e in requisitos_cardona(df1h, df1d):
    cumplidos = sum(1 for _, ok in e['checks'] if ok)
    with st.expander(e['nombre'] + " -- " + (FUEGO if cumplidos == len(e['checks']) else str(cumplidos) + "/" + str(len(e['checks'])))):
        for texto, ok in e['checks']: st.markdown((CHECK if ok else CRUZ) + " " + texto)
        st.info(e['humana'])

df1h['SMA40'] = df1h['Close'].rolling(40).mean()
fig1 = go.Figure(go.Candlestick(x=df1h.index, open=df1h['Open'], high=df1h['High'], low=df1h['Low'], close=df1h['Close']))
fig1.add_trace(go.Scatter(x=df1h.index, y=df1h['SMA40'], name='SMA 40', line=dict(color='orange')))
fig1.update_layout(xaxis_rangeslider_visible=False, height=400)
st.plotly_chart(fig1, width='stretch')
st.divider()

# ==========================================
# ESTADISTICAS Y EXPECTATIVA MATEMATICA
# ==========================================
st.subheader("Estadísticas por Estrategia (Datos Limpios vs Históricos)")
if cerradas:
    stats = calcular_estadisticas(cerradas)
    rows = []
    for est, cubos in stats.items():
        for cubo, data in cubos.items():
            if data['n'] > 0:
                wr = data['w'] / data['n']
                avg_w = data['sum_w'] / data['w'] if data['w'] > 0 else 0
                avg_l = data['sum_l'] / data['l'] if data['l'] > 0 else 0
                em = (wr * avg_w) - ((1 - wr) * avg_l)
                rows.append({
                    'Estrategia': est, 'Muestra': cubo, 'n': data['n'],
                    'Win Rate': f"{wr*100:.1f}%", 'Ganancia Prom': f"${avg_w:.2f}",
                    'Perdida Prom': f"${avg_l:.2f}", 'EM (Expectativa)': f"${em:.2f}",
                    'Aviso': '' if data['n'] >= 10 else 'Muestra pequeña (<10)'
                })
    if rows:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.caption("EM > 0 significa que la estrategia es rentable a largo plazo. Solo juzgar estrategias con n >= 10.")
else:
    st.info("Aun no hay operaciones cerradas.")

st.divider()

# Historial e Informe IA
st.subheader("Historial")
if cerradas:
    hist = pd.DataFrame(cerradas)
    st.dataframe(hist[['Fecha', 'Simbolo', 'Call/Put', 'Strike', 'Precio Compra', 'Precio Venta', 'Ganancia $', 'Ganancia %', 'Estrategia', 'Notas']], width='stretch', hide_index=True)

st.subheader("Informe semanal de la IA")
if st.button("Generar informe con IA"):
    if not cerradas: st.info("Sin operaciones cerradas.")
    else:
        with st.spinner("Analizando..."):
            ws_ia = abrir_informe_ia(sh_sim)
            lecciones = ''
            if ws_ia:
                try: lecciones = " | ".join([str(x.get('Lecciones', '')) for x in ws_ia.get_all_records() if x.get('Lecciones')][-5:])
                except: pass
            res = informe_gemini(cerradas, lecciones, KEY_G)
        if res['ok']:
            ia = res['datos']
            st.markdown("**Resumen:** " + str(ia.get('resumen', '')))
            for titulo, clave in [("Reglas violadas", 'reglas_violadas'), ("Entradas correctas", 'entradas_correctas'), ("Salidas correctas", 'salidas_correctas'), ("Lecciones", 'lecciones'), ("Recomendaciones", 'recomendaciones')]:
                items = ia.get(clave, [])
                if items:
                    st.markdown("**" + titulo + ":**")
                    for e in items: st.markdown("- " + str(e))
            if ws_ia:
                try: ws_ia.append_row([datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M'), str(ia.get('resumen', '')), " | ".join([str(x) for x in ia.get('lecciones', [])]), " | ".join([str(x) for x in ia.get('recomendaciones', [])])])
                except: pass
        else:
            st.error("Fallo: " + res['motivo'])
            if ws_ia:
                try: ws_ia.append_row([datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M'), 'FALLIDO: ' + res['motivo'][:400], '', ''])
                except: pass
