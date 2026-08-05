import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="Radar DSS Trading", page_icon="🎯", layout="wide")

SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'
URL_CSV = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid=0"

@st.cache_data(ttl=300)
def cargar_datos():
    return pd.read_csv(URL_CSV)

@st.cache_data(ttl=900)
def serie(ticker, intervalo, periodo):
    df = yf.Ticker(ticker).history(period=periodo, interval=intervalo)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def requisitos_cardona(df1h, df1d):
    d = df1d.copy()
    d['SMA100'] = d['Close'].rolling(100).mean()
    d['SMA200'] = d['Close'].rolling(200).mean()
    close_d = float(d['Close'].iloc[-1])
    o_d, h_d, c_d = float(d['Open'].iloc[-1]), float(d['High'].iloc[-1]), float(d['Close'].iloc[-1])
    sma100 = float(d['SMA100'].iloc[-1])
    sma200 = float(d['SMA200'].iloc[-1])
    piso_fuerte = (abs(close_d - sma100)/sma100 <= 0.02) or (abs(close_d - sma200)/sma200 <= 0.02)
    lejos_pisos = (abs(close_d - sma100)/sma100 > 0.03) and (abs(close_d - sma200)/sma200 > 0.03)
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
    dist_pm40 = abs(close_h - sma40h)/sma40h
    cerca_pm40 = dist_pm40 <= 0.015
    canal_bajista = (sma40h > sma20) and (sma40h < float(h['SMA40'].iloc[-4]))

    h['fecha'] = h.index.date
    ultima_fecha = h['fecha'].iloc[-1]
    hoy = h[h['fecha'] == ultima_fecha]
    antes = h[h['fecha'] < ultima_fecha]
    prev_close = float(antes['Close'].iloc[-1]) if len(antes) > 0 else float(h['Close'].iloc[-2])

    gap_alza, gap_bajista = False, False
    primera_roja, primera_verde = False, False
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
        'entrada': '⏰ Entrada a partir de las 11:00',
        'checks': [
            ('Diario: en piso fuerte (PM100/PM200 ±2%)', piso_fuerte),
            ('Hora: tendencia alcista (precio > PM40)', alcista_h),
            ('Vela verde rompe techo / línea bajista', vela_verde and ruptura_techo),
        ],
        'humana': '👁 Verifica: vela verde FORMADA a partir de las 11:00 rompiendo la línea bajista. La subida suele durar 2 a 4 días.'
    })
    estrats.append({
        'nombre': 'CALL 2: Rebote PM40 / Caída Normal',
        'entrada': '⏰ Entrada a partir de las 11:00',
        'checks': [
            ('Tendencia alcista en hora (precio > PM40)', alcista_h),
            ('Caída que se acercó al PM40 (≤1.5%)', cerca_pm40),
            ('Vela verde rompe la línea bajista de la caída', vela_verde and ruptura_techo),
        ],
        'humana': '👁 Verifica: traza la línea bajista de la caída y espera la vela verde final formada desde las 11:00.'
    })
    estrats.append({
        'nombre': 'CALL 3: Gap Bajista al Alza',
        'entrada': '⏰ Entrada a las 11:00',
        'checks': [
            ('Abrió abajo vs cierre anterior (gap bajista)', gap_bajista),
            ('Primera vela de hora verde', primera_verde),
            ('Tendencia alcista en hora', alcista_h),
        ],
        'humana': '👁 Verifica: dos velas verdes sólidas hasta las 11:00. NO comprar dentro de canales bajistas.'
    })
    estrats.append({
        'nombre': 'PUT 1: Primera Vela Roja de Apertura',
        'entrada': '⏰ ÚNICA que entra a las 10:00 en punto',
        'checks': [
            ('Primera vela de hora roja (martillo rojo también vale)', primera_roja),
            ('NO está en piso fuerte', not piso_fuerte),
            ('NO está en zona barata (lejos de PM100/200)', lejos_pisos),
        ],
        'humana': '👁 Verifica: vela formada a las 10:00. Si aparece sobre piso fuerte o zona barata, tiende a fallar: NO aplicar.'
    })
    estrats.append({
        'nombre': 'PUT 2: Ruptura del Piso del Gap',
        'entrada': '⏰ Entrada desde las 11:00',
        'checks': [
            ('Abrió con gap y primera vela verde', primera_verde and (gap_alza or gap_bajista)),
            ('Vela roja rompe el piso del gap', ruptura_piso_gap),
            ('Lejos del PM40 (mayor probabilidad de éxito)', not cerca_pm40),
        ],
        'humana': '👁 Verifica: ruptura con vela roja FORMADA desde las 11:00 en adelante. Puede dar el 100% el mismo día o al siguiente.'
    })
    estrats.append({
        'nombre': 'PUT 3: Canal Bajista (Modelo 4 Pasos)',
        'entrada': '⏰ Entrada desde las 11:00',
        'checks': [
            ('Paso 1: canal bajista (PM40 sobre PM20, descendente)', canal_bajista),
            ('Paso 2: zona cara / techo', lejos_pisos or dist_pm40 > 0.015),
            ('Paso 3: intento de subida borrado por velas rojas', ruptura_piso),
            ('Paso 4: vela roja rompe la línea de piso trazada', ruptura_piso),
        ],
        'humana': '👁 Verifica: traza la línea de piso siguiendo la subida; entra cuando una vela roja la rompa.'
    })
    estrats.append({
        'nombre': 'PUT 4: Hanger en Diario',
        'entrada': '⏰ Compra cerca del cierre (4:00 PM / SPY 4:14 PM)',
        'checks': [
            ('Hanger en diario (cola superior mayor al cuerpo)', hanger_diario),
            ('Zona cara o lejos de pisos fuertes', lejos_pisos),
        ],
        'humana': '👁 Verifica: la vela puede cambiar durante el día; confirma cerca del cierre. El color no importa.'
    })
    return estrats

st.title("🎯 RADAR DE FRANCOTIRADOR - DSS TRADING")
try:
    df = cargar_datos()
except Exception as e:
    st.error(f"No se pudo leer el Google Sheet: {e}")
    st.stop()

fecha = df['Fecha_Hora_Escaneo'].iloc[0] if not df.empty else "—"
st.caption(f"🕒 Último escaneo: {fecha}")

calls_v = int(df['CALL Estado'].astype(str).str.contains('VIABLE', na=False).sum())
puts_v = int(df['PUT Estado'].astype(str).str.contains('VIABLE', na=False).sum())
latentes = int((df['Condicion 3: Zona Diario'] == 'En Piso Fuerte').sum())
total = len(df)
k1, k2, k3, k4 = st.columns(4)
k1.metric("📈 CALLs VIABLES", calls_v)
k2.metric("📉 PUTs VIABLES", puts_v)
k3.metric("👀 LATENTES (Piso Fuerte)", latentes)
k4.metric("📡 ACTIVOS ESCANEADOS", total)

st.divider()

st.sidebar.header("🎛️ Panel de Control")
ticker_sel = st.sidebar.selectbox("📊 Elige empresa para la gráfica", df['Ticker'].tolist())
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
    "⏰ Hora de entrada (Validación Humana)",
    options=sorted(df['Validación Humana'].unique().tolist()),
    default=sorted(df['Validación Humana'].unique().tolist())
)

df_f = df[
    df['Estrategia Cardona'].isin(estr_filt) &
    df['Tendencia 1H'].isin(tend_filt) &
    df['Validación Humana'].isin(val_filt)
]

fila = df[df['Ticker'] == ticker_sel]
if not fila.empty:
    r = fila.iloc[0]
    st.subheader(f"📌 {ticker_sel}: {r['Estrategia Cardona']}")
    a, b, c, d = st.columns(4)
    a.write(f"**Cond 1:** {r['Condicion 1: Tendencia']}")
    b.write(f"**Cond 2:** {r['Condicion 2: Distancia PM40']}")
    c.write(f"**Cond 3:** {r['Condicion 3: Zona Diario']}")
    d.write(f"**✅ Validación:** {r['Validación Humana']}")

df1h = serie(ticker_sel, "1h", "60d")
df1d = serie(ticker_sel, "1d", "1y")

st.subheader("📋 Verificación de Estrategias (Método Cardona)")
for e in requisitos_cardona(df1h, df1d):
    cumplidos = sum(1 for _, ok in e['checks'] if ok)
    total_e = len(e['checks'])
    estado = "🔥 LISTA PARA VERIFICAR" if cumplidos == total_e else f"{cumplidos}/{total_e} requisitos"
    with st.expander(f"{e['nombre']}  —  {estado}"):
        for texto, ok in e['checks']:
            st.markdown(f"{'✅' if ok else '❌'} {texto}")
        st.markdown(f"**{e['entrada']}**")
        st.info(e['humana'])
        st.checkbox(f"Lo verifiqué en el gráfico de {ticker_sel}", key=e['nombre'])

g1, g2 = st.columns(2)

df1h['SMA40'] = df1h['Close'].rolling(40).mean()
fig1 = go.Figure()
fig1.add_trace(go.Candlestick(
    x=df1h.index, open=df1h['Open'], high=df1h['High'],
    low=df1h['Low'], close=df1h['Close'], name=ticker_sel))
fig1.add_trace(go.Scatter(x=df1h.index, y=df1h['SMA40'], name='SMA 40', line=dict(color='orange', width=2)))
fig1.update_layout(title=f"{ticker_sel} — Velas 1H + SMA 40", xaxis_rangeslider_visible=False, height=420)
g1.plotly_chart(fig1, use_container_width=True)

df1d['SMA100'] = df1d['Close'].rolling(100).mean()
df1d['SMA200'] = df1d['Close'].rolling(200).mean()
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['Close'], name='Precio', line=dict(color='blue', width=2)))
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['SMA100'], name='SMA 100', line=dict(color='green', width=1.5)))
fig2.add_trace(go.Scatter(x=df1d.index, y=df1d['SMA200'], name='SMA 200', line=dict(color='red', width=1.5)))
fig2.update_layout(title=f"{ticker_sel} — Diario: Piso 100/200", height=420)
g2.plotly_chart(fig2, use_container_width=True)

st.divider()

st.subheader("📡 Radar de Activos")
df_show = df_f.copy()
df_show['Tendencia 1H'] = df_show['Tendencia 1H'].map(lambda x: f"🟢 {x}" if x == 'Alcista' else f"🔴 {x}")
cols = ['Ticker', 'Precio Spot', 'Tendencia 1H', 'SMA 40 (1H)', 'Estrategia Cardona',
        'Validación Humana', 'CALL Ask ($)', 'CALL Estado', 'PUT Ask ($)', 'PUT Estado']
st.dataframe(df_show[[c for c in cols if c in df_show.columns]], use_container_width=True, hide_index=True)
