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
