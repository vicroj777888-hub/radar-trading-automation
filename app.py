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
k1, k2, k3 = st.columns(3)
k1.metric("📈 CALLs VIABLES", calls_v)
k2.metric("📉 PUTs VIABLES", puts_v)
k3.metric("👀 LATENTES (Piso Fuerte)", latentes)

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

df_f = df[df['Estrategia Cardona'].isin(estr_filt) & df['Tendencia 1H'].isin(tend_filt)]

fila = df[df['Ticker'] == ticker_sel]
if not fila.empty:
    r = fila.iloc[0]
    st.subheader(f"📌 {ticker_sel}: {r['Estrategia Cardona']}")
    a, b, c, d = st.columns(4)
    a.write(f"**Cond 1:** {r['Condicion 1: Tendencia']}")
    b.write(f"**Cond 2:** {r['Condicion 2: Distancia PM40']}")
    c.write(f"**Cond 3:** {r['Condicion 3: Zona Diario']}")
    d.write(f"**✅ Validación:** {r['Validación Humana']}")

g1, g2 = st.columns(2)

df1h = serie(ticker_sel, "1h", "60d")
df1h['SMA40'] = df1h['Close'].rolling(40).mean()
fig1 = go.Figure()
fig1.add_trace(go.Candlestick(
    x=df1h.index, open=df1h['Open'], high=df1h['High'],
    low=df1h['Low'], close=df1h['Close'], name=ticker_sel))
fig1.add_trace(go.Scatter(x=df1h.index, y=df1h['SMA40'], name='SMA 40', line=dict(color='orange', width=2)))
fig1.update_layout(title=f"{ticker_sel} — Velas 1H + SMA 40", rangeslider_visible=False, height=420)
g1.plotly_chart(fig1, use_container_width=True)

df1d = serie(ticker_sel, "1d", "1y")
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
