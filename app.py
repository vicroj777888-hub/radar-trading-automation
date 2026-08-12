# ==========================================
# RADAR DE FRANCOTIRADOR - DSS TRADING
# app.py - Version FINAL corregida
# 13 de agosto de 2026
# SIN EMOJIS: evita errores de codificacion al copiar y pegar
# ==========================================

import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh

# ==========================================
# CONFIGURACION INICIAL
# ==========================================
st.set_page_config(page_title="Radar DSS Trading", layout="wide")

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
URL_CSV = 'https://docs.google.com/spreadsheets/d/' + SPREADSHEET_ID + '/export?format=csv&gid=0'
ZONA_NY = ZoneInfo('America/New_York')
REPO = 'vicroj777888-hub/radar-trading-automation'

if 'esperando' not in st.session_state:
    st.session_state['esperando'] = False
if 'aviso_listo' not in st.session_state:
    st.session_state['aviso_listo'] = False

# ==========================================
# CARGA DE DATOS
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

try:
    df = cargar_datos()
except Exception as e:
    st.error("No se pudo leer el Google Sheet: " + str(e))
    st.stop()

if df.empty:
    st.error("El Google Sheet esta vacio. Ejecuta el robot primero.")
    st.stop()

# Columnas con nombres EXACTOS (alineados con main.py)
columnas_necesarias = [
    'Ticker', 'Fecha_Hora_Escaneo', 'Precio Spot', 'Tendencia 1H',
    'SMA 40 (1H)', 'Estrategia Cardona', 'Condicion 1: Tendencia',
    'Condicion 2: Distancia PM40', 'Condicion 3: Zona Diario',
    'Validación Humana', 'Call Estado', 'Put Estado',
    'Call Ask ($)', 'Put Ask ($)', 'Strike Call OTM', 'Strike Put OTM'
]

columnas_faltantes = [c for c in columnas_necesarias if c not in df.columns]
if columnas_faltantes:
    st.error("Faltan columnas en el Sheet: " + str(columnas_faltantes))
    st.info("Columnas encontradas: " + ", ".join(df.columns.tolist()))
    st.stop()

fecha = df['Fecha_Hora_Escaneo'].iloc[0]
st.caption("Ultimo escaneo (hora Nueva York): " + str(fecha))

if st.session_state['aviso_listo']:
    st.success("LISTO. El escaneo llego: los datos ya estan actualizados.")
    st.session_state['aviso_listo'] = False

# ==========================================
# PANEL DE ESPERA DEL ROBOT
# ==========================================

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
                st.info("El robot YA termino de escanear y esta escribiendo el Sheet. En menos de 1 minuto veras el aviso verde.")
            else:
                st.error("El robot fallo en esta ejecucion. Revisa GitHub Actions para ver el detalle.")
        elif st_status in ('in_progress', 'queued'):
            st.caption("Estado en GitHub Actions: trabajando. Todo en orden, solo falta que termine.")
        if st.button("Cancelar espera"):
            st.session_state['esperando'] = False
            st.rerun()

# ==========================================
# METRICAS PRINCIPALES
# ==========================================

calls_v = int(df['Call Estado'].astype(str).str.contains('VIABLE', na=False).sum())
puts_v = int(df['Put Estado'].astype(str).str.contains('VIABLE', na=False).sum())
latentes = int((df['Condicion 3: Zona Diario'] == 'En Piso Fuerte').sum())
total = len(df)

k1, k2, k3, k4 = st.columns(4)
k1.metric("CALLs VIABLES", calls_v)
k2.metric("PUTs VIABLES", puts_v)
k3.metric("LATENTES (Piso Fuerte)", latentes)
k4.metric("ACTIVOS ESCANEADOS", total)

st.divider()

# ==========================================
# PANEL LATERAL
# ==========================================

st.sidebar.header("Panel de Control")
ticker_sel = st.sidebar.selectbox("Elige empresa para la grafica", df['Ticker'].tolist())

estr_filt = st.sidebar.multiselect(
    "Estrategia Cardona",
    options=sorted(df['Estrategia Cardona'].unique().tolist()),
    default=sorted(df['Estrategia Cardona'].unique().tolist())
)

tend_filt = st.sidebar.multiselect(
    "Tendencia 1H",
    options=['Alcista', 'Bajista'],
    default=['Alcista', 'Bajista']
)

val_filt = st.sidebar.multiselect(
    "Hora de entrada (Validacion Humana)",
    options=sorted(df['Validación Humana'].unique().tolist()),
    default=sorted(df['Validación Humana'].unique().tolist())
)

st.sidebar.markdown("---")
st.sidebar.header("Actualizacion manual")

if st.sidebar.button("Lanzar escaneo ahora"):
    try:
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
        st.session_state['esperando'] = True
        st.session_state['hora_lanzamiento'] = datetime.now(ZONA_NY).strftime('%Y-%m-%d %H:%M:%S')
        st.sidebar.success("Escaneo lanzado. Te aviso cuando lleguen los datos.")
        st.rerun()
    except Exception as e:
        st.sidebar.error("No se pudo lanzar el escaneo: " + str(e))

if st.sidebar.button("Recargar datos del Sheet"):
    cargar_datos.clear()
    st.rerun()

# ==========================================
# FILTROS Y DETALLE DEL ACTIVO
# ==========================================

df_f = df[
    df['Estrategia Cardona'].isin(estr_filt) &
    df['Tendencia 1H'].isin(tend_filt) &
    df['Validación Humana'].isin(val_filt)
]

fila = df[df['Ticker'] == ticker_sel]
if not fila.empty:
    r = fila.iloc[0]
    st.subheader(ticker_sel + ": " + str(r['Estrategia Cardona']))
    a, b, c, d = st.columns(4)
    a.write("**Cond 1:** " + str(r['Condicion 1: Tendencia']))
    b.write("**Cond 2:** " + str(r['Condicion 2: Distancia PM40']))
    c.write("**Cond 3:** " + str(r['Condicion 3: Zona Diario']))
    d.write("**Validacion:** " + str(r['Validación Humana']))

# ==========================================
# GRAFICOS
# ==========================================

df1h = serie(ticker_sel, "1h", "60d")
df1d = serie(ticker_sel, "1d", "1y")

st.subheader("Verificacion de Estrategias (Metodo Cardona)")
for e in requisitos_cardona(df1h, df1d):
    cumplidos = sum(1 for _, ok in e['checks'] if ok)
    total_e = len(e['checks'])
    if cumplidos == total_e:
        estado = "LISTA PARA VERIFICAR"
    else:
        estado = str(cumplidos) + "/" + str(total_e) + " requisitos"
    with st.expander(e['nombre'] + "  --  " + estado):
        for texto, ok in e['checks']:
            if ok:
                st.markdown("[OK] " + texto)
            else:
                st.markdown("[X] " + texto)
        st.markdown("**" + e['entrada'] + "**")
        st.info(e['humana'])
        st.checkbox("Lo verifique en el grafico de " + ticker_sel, key=e['nombre'])

df1h['SMA40'] = df1h['Close'].rolling(40).mean()
fig1 = go.Figure()
fig1.add_trace(go.Candlestick(
    x=df1h.index,
    open=df1h['Open'],
    high=df1h['High'],
    low=df1h['Low'],
    close=df1h['Close'],
    name=ticker_sel
))
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
    st.plotly_chart(fig1, use_container_width=True)
with g2:
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ==========================================
# TABLA RADAR DE ACTIVOS
# ==========================================

st.subheader("Radar de Activos")
df_show = df_f.copy()

cols = [
    'Ticker', 'Precio Spot', 'Tendencia 1H', 'SMA 40 (1H)', 'Estrategia Cardona',
    'Validación Humana', 'Call Ask ($)', 'Call Estado', 'Put Ask ($)', 'Put Estado'
]

st.dataframe(
    df_show[[c for c in cols if c in df_show.columns]],
    use_container_width=True,
    hide_index=True
)
