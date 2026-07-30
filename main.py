# ==========================================
# CÓDIGO MAESTRO DEFINITIVO: DSS TRADING CARDONA (CON REGLA PUT)
# ==========================================

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import gspread
from google.colab import auth
from google.auth import default
from gspread_dataframe import set_with_dataframe

# 1. AUTENTICACIÓN
print("🔗 Conectando con Google Drive...")
auth.authenticate_user()
creds, _ = default()
gc = gspread.authorize(creds)
print("✅ Autenticación exitosa.")

# 2. CONFIGURACIÓN
watchlist = [
    'SPY', 'QQQ', 'BAC', 'PFE', 'F', 'SOFI', 'CCL', 'AAL', 'SNAP', 'PLTR', 'HOOD', 'VALE', 'T',
    'SLV', 'USO', 'AAPL', 'META', 'AMZN', 'TNA', 'GLD', 'XOM', 'CVX', 'NVDA', 'NFLX', 'MRNA', 'TSLA'
]

rangos_cardona = {
    'BAC': (0.10, 0.20), 'SLV': (0.10, 0.20), 'USO': (0.10, 0.20),
    'SPY': (0.25, 0.30), 'QQQ': (0.25, 0.30),
    'AAPL': (0.45, 0.80), 'META': (0.45, 0.80), 'AMZN': (0.60, 0.80),
    'TNA': (0.60, 0.80), 'GLD': (0.60, 0.80), 'XOM': (0.60, 0.80), 'CVX': (0.60, 0.80), 'NVDA': (0.60, 0.80),
    'NFLX': (1.50, 2.50), 'MRNA': (2.00, 2.50), 'TSLA': (2.50, 3.00)
}
RANGO_DEFAULT = (0.10, 0.25)

resultados = []
fecha_escaneo = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

print(f"🕒 Escaneo iniciado: {fecha_escaneo}")
print(f"📡 Descargando datos de {len(watchlist)} activos...")

# 3. BUCLE PRINCIPAL
for ticker in watchlist:
    try:
        ticker_obj = yf.Ticker(ticker)
        df_1d = ticker_obj.history(period="1y", interval="1d")
        df_1h = ticker_obj.history(period="60d", interval="1h")

        if df_1d.empty or df_1h.empty:
            continue

        if isinstance(df_1d.columns, pd.MultiIndex):
            df_1d.columns = df_1d.columns.get_level_values(0)
        if isinstance(df_1h.columns, pd.MultiIndex):
            df_1h.columns = df_1h.columns.get_level_values(0)

        df_1d['SMA_100'] = df_1d['Close'].rolling(window=100).mean()
        df_1d['SMA_200'] = df_1d['Close'].rolling(window=200).mean()
        df_1h['SMA_20'] = df_1h['Close'].rolling(window=20).mean()
        df_1h['SMA_40'] = df_1h['Close'].rolling(window=40).mean()

        precio_spot = float(df_1h['Close'].iloc[-1])
        sma_40_1h = float(df_1h['SMA_40'].iloc[-1])
        sma_100_1d = float(df_1d['SMA_100'].iloc[-1])
        sma_200_1d = float(df_1d['SMA_200'].iloc[-1])

        tendencia_1h = "Alcista" if precio_spot > sma_40_1h else "Bajista"

        # Escáner de Opciones
        min_ask, max_ask = rangos_cardona.get(ticker, RANGO_DEFAULT)
        call_strike, call_ask, call_estado = "N/A", 999, "Sin Opciones"
        put_strike, put_ask, put_estado = "N/A", 999, "Sin Opciones"
        vencimiento = "N/A"

        if hasattr(ticker_obj, 'options') and ticker_obj.options:
            today = datetime.now().date()
            fechas_disp = [datetime.strptime(d, "%Y-%m-%d").date() for d in ticker_obj.options]
            fechas_futuras = sorted([d for d in fechas_disp if d >= today])

            if fechas_futuras:
                vencimiento = fechas_futuras[0].strftime("%Y-%m-%d")
                chain = ticker_obj.option_chain(vencimiento)

                calls_otm = chain.calls[chain.calls['strike'] > precio_spot].sort_values('strike')
                if not calls_otm.empty:
                    mejor_call = calls_otm.iloc[0]
                    call_strike = mejor_call['strike']
                    call_ask_val = mejor_call['ask'] if pd.notna(mejor_call['ask']) else mejor_call['lastPrice']
                    call_ask = round(float(call_ask_val), 2) if pd.notna(call_ask_val) else 0

                    if 0 < call_ask <= max_ask: call_estado = "VIABLE (Rango Cardona)"
                    elif call_ask > max_ask: call_estado = "MUY CARO (Fuera de Rango)"
                    else: call_estado = "Sin Datos"

                puts_otm = chain.puts[chain.puts['strike'] < precio_spot].sort_values('strike', ascending=False)
                if not puts_otm.empty:
                    mejor_put = puts_otm.iloc[0]
                    put_strike = mejor_put['strike']
                    put_ask_val = mejor_put['ask'] if pd.notna(mejor_put['ask']) else mejor_put['lastPrice']
                    put_ask = round(float(put_ask_val), 2) if pd.notna(put_ask_val) else 0

                    if 0 < put_ask <= max_ask: put_estado = "VIABLE (Rango Cardona)"
                    elif put_ask > max_ask: put_estado = "MUY CARO (Fuera de Rango)"
                    else: put_estado = "Sin Datos"

        # Lógica de Estrategias y CONDICIONES
        es_alcista = precio_spot > sma_40_1h
        es_piso_fuerte = (abs(precio_spot - sma_100_1d)/sma_100_1d <= 0.02) or (abs(precio_spot - sma_200_1d)/sma_200_1d <= 0.02)
        distancia_pm40 = abs(precio_spot - sma_40_1h) / sma_40_1h

        cond1 = "Alcista" if es_alcista else "Bajista"
        cond2 = "Cerca PM40" if distancia_pm40 <= 0.015 else "Lejos PM40"
        cond3 = "En Piso Fuerte" if es_piso_fuerte else "Zona Cara"

        if es_piso_fuerte and es_alcista: estrategia = "CALL: Piso Fuerte + Ruptura"
        elif es_piso_fuerte and not es_alcista: estrategia = "CALL: Esperar Rebote en Piso"
        elif es_alcista and distancia_pm40 <= 0.015: estrategia = "CALL: Rebote PM 40 / Caída Normal"
        elif es_alcista and distancia_pm40 > 0.015: estrategia = "CALL: Tendencia Fuerte (Esperar Caída)"
        elif not es_alcista and distancia_pm40 <= 0.015: estrategia = "PUT: Rechazo en PM 40"
        else: estrategia = "PUT: Canal Bajista / Hanger"

        # VALIDACIÓN HUMANA (Diferenciando CALL y PUT)
        if "PUT" in estrategia:
            validacion = "1ra Vela Roja (10 AM)"
        else:
            validacion = "Vela 11 AM"

        resultados.append({
            'Fecha_Hora_Escaneo': fecha_escaneo,
            'Ticker': ticker,
            'Precio Spot': round(precio_spot, 2),
            'Tendencia 1H': tendencia_1h,
            'Estrategia Cardona': estrategia,
            'Condicion 1: Tendencia': cond1,
            'Condicion 2: Distancia PM40': cond2,
            'Condicion 3: Zona Diario': cond3,
            'Validación Humana': validacion,
            'Vencimiento_Op': vencimiento,
            'CALL Strike': call_strike,
            'CALL Ask ($)': call_ask if call_ask != 999 else 0,
            'CALL Estado': call_estado,
            'PUT Strike': put_strike,
            'PUT Ask ($)': put_ask if put_ask != 999 else 0,
            'PUT Estado': put_estado,
            'SMA 40 (1H)': round(sma_40_1h, 2),
            'SMA 100 (1D)': round(sma_100_1d, 2)
        })

    except Exception as e:
        print(f"️ Error procesando {ticker}: {e}")

# 4. EXPORTAR
df_resultado = pd.DataFrame(resultados)
print("\n✅ Análisis completado. Enviando a Google Sheets...")

sheet_url = "https://docs.google.com/spreadsheets/d/17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U/edit?gid=0#gid=0"

try:
    sh = gc.open_by_url(sheet_url)
    worksheet = sh.worksheet("Radar_Senales")
    worksheet.clear()
    set_with_dataframe(worksheet, df_resultado)
    print("🚀 ¡ÉXITO TOTAL! Tu Google Sheet y Power BI han sido actualizados con la regla de la Vela Roja para PUTs.")
except Exception as e:
    print(f"❌ Error al exportar: {e}")