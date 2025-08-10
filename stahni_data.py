# -*- coding: utf-8 -*-
import requests
import pandas as pd
from datetime import datetime
import os
import time
import io
import matplotlib.pyplot as plt

# ====== KONFIGURAČNÍ PROMĚNNÉ ======
LIMIT_EUR = 13.0  # Limitní cena v EUR/MWh

# Přístupové údaje z GitHub Secrets / .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ====== FUNKCE ======

def ziskej_data_z_ote(max_pokusu=5, cekani=300):
    """Stáhne dnešní SPOT ceny z OTE, opakuje při neúspěchu."""
    dnes = datetime.now()
    den = dnes.strftime("%d")
    mesic = dnes.strftime("%m")
    rok = dnes.strftime("%Y")
    url = f"http://www.ote-cr.cz/kratkodobe-trhy/elektrina/denni-trh/attached/{rok}/month{mesic}/day{den}/DT_{den}_{mesic}_{rok}_CZ.xls"

    for pokus in range(1, max_pokusu + 1):
        try:
            print(f"⬇️ Pokus {pokus}: stahuji data z {url}")
            df = pd.read_excel(url, skiprows=23, usecols="A,B", engine="openpyxl")
            df.columns = ["Hodina", "Cena (EUR/MWh)"]
            df.dropna(inplace=True)
            df["Hodina"] = pd.to_numeric(df["Hodina"], errors="coerce").fillna(0).astype(int)
            df["Cena (EUR/MWh)"] = pd.to_numeric(
                df["Cena (EUR/MWh)"].astype(str).str.replace(",", "."),
                errors="coerce"
            )
            df = df[df["Hodina"] >= 1]
            return df
        except Exception as e:
            print(f"⚠️ Chyba: {e}")
            if pokus < max_pokusu:
                print(f"⏳ Čekám {cekani} s před dalším pokusem…")
                time.sleep(cekani)
    raise Exception("❌ Nepodařilo se stáhnout data z OTE.")

def uloz_csv(df, soubor="ceny_ote.csv"):
    df.to_csv(soubor, index=False)
    print(f"💾 Data uložena do {soubor}")

def vytvor_graf(df):
    """Vytvoří graf cen a vrátí ho jako bytes."""
    fig, ax = plt.subplots(figsiz
