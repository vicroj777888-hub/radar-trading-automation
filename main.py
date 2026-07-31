# ==========================================
# CÓDIGO MAESTRO DEFINITIVO: DSS TRADING CARDONA (VERSIÓN GITHUB ACTIONS)
# ==========================================

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import json
import os

# ==========================================
# AUTENTICACIÓN CON GOOGLE SHEETS (GitHub Actions)
# ==========================================
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Leer credenciales desde el secreto de GitHub
credentials_json = os.environ['GOOGLE_CREDENTIALS']
credentials_info = json.loads(credentials_json)
creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)

gc = gspread.authorize(creds)

# ==========================================
# CONFIGURACIÓN: ID DE TU GOOGLE SHEET
# ==========================================
SPREADSHEET_ID = '17cu_GUSQl5CWR1UXONrLPyaKD-0l0OdlwWMmg_e-G0U'

# ==========================================
# LISTA DE ACTIVOS A ANALIZAR
# ==========================================
tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'AMD', 'NFLX', 'SPY']

# ==========================================
# PARÁMETROS DEL RADAR
# ==========================================
VOLUMEN_MINIMO = 1000
DELTA_MINIMO = 0.10
EXPIRACION_DIAS = 30

# ==========================================
# FUNCIÓN PRINCIPAL
# ==========================================
def analizar_activo(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        
        if hist.empty:
            return None
        
        precio_actual = hist['Close'].iloc[-1]
        volumen = hist['Volume'].iloc[-1]
        
        expiraciones = stock.options
        if not expiraciones:
            return None
        
        expiracion = expiraciones[0]
        opciones = stock.option_chain(expiracion)
        calls = opciones.calls
        puts = opciones.puts
        
        calls_viables = calls[calls['volume'] >= VOLUMEN_MINIMO]
        puts_viables = puts[puts['volume'] >= VOLUMEN_MINIMO]
        
        return {
            'ticker': ticker,
            'precio': precio_actual,
            'volumen': volumen,
            'calls_viables': len(calls_viables),
            'puts_viables': len(puts_viables),
            'expiracion': expiracion
        }
    except Exception as e:
        print(f"Error analizando {ticker}: {e}")
        return None

def main():
    print(f"Iniciando análisis - {datetime.now()}")
    
    resultados = []
    for ticker in tickers:
        print(f"Analizando {ticker}...")
        resultado = analizar_activo(ticker)
        if resultado:
            resultados.append(resultado)
    
    df = pd.DataFrame(resultados)
    
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.sheet1
        
        worksheet.clear()
        
        headers = ['Ticker', 'Precio', 'Volumen', 'Calls Viables', 'Puts Viables', 'Expiración', 'Fecha Actualización']
        worksheet.append_row(headers)
        
        for _, row in df.iterrows():
            worksheet.append_row([
                row['ticker'],
                row['precio'],
                row['volumen'],
                row['calls_viables'],
                row['puts_viables'],
                row['expiracion'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        print(f"✅ Datos actualizados en Google Sheets. Total: {len(df)} activos")
    except Exception as e:
        print(f"❌ Error al escribir en Google Sheets: {e}")

if __name__ == '__main__':
    main()
