# stahni_data.py
# -*- coding: utf-8 -*-
import requests
import pandas as pd
from datetime import datetime
import time
import sys
import os

# konfigurace retry
MAX_POKUSY = 5
CEKANI_SEC = 300  # 5 minut

def stahni_xls():
    dnes = datetime.now()
    den = dnes.strftime("%d")
    mesic = dnes.strftime("%m")
    rok = dnes.strftime("%Y")
    url = f"http://www.ote-cr.cz/kratkodobe-trhy/elektrina/denni-trh/attached/{rok}/month{mesic}/day{den}/DT_{den}_{mesic}_{rok}_CZ.xls"
    print(f"⬇️ Pokouším se stáhnout OTE data z: {url}")
    try:
        df = pd.read_excel(url, skiprows=23, usecols="A,B", engine="openpyxl")
    except Exception as e:
        raise RuntimeError(f"Chyba při čtení XLS: {e}")
    return df

def zpracuj_df(df):
    df.columns = ["Hodina", "Cena (EUR/MWh)"]
    df.dropna(inplace=True)
    df["Hodina"] = pd.to_numeric(df["Hodina"], errors="coerce").fillna(0).astype(int)
    df["Cena (EUR/MWh)"] = pd.to_numeric(df["Cena (EUR/MWh)"].astype(str).str.replace(",", "."), errors="coerce")
    df = df[df["Hodina"] >= 1]
    return df[["Hodina", "Cena (EUR/MWh)"]]

def uloz_csv(df, cesta="ceny_ote.csv"):
    df.to_csv(cesta, index=False, encoding="utf-8")
    print(f"✅ Uloženo {cesta}")

def main():
    for pokus in range(1, MAX_POKUSY + 1):
        try:
            print(f"🔁 Pokus {pokus} z {MAX_POKUSY}")
            df = stahni_xls()
            df = zpracuj_df(df)
            uloz_csv(df)
            print("🏁 Stažení a uložení úspěšné.")
            return 0
        except Exception as e:
            print(f"⚠️ Pokus {pokus} selhal: {e}")
            if pokus < MAX_POKUSY:
                print(f"⏳ Čekám {CEKANI_SEC} s před dalším pokusem...")
                time.sleep(CEKANI_SEC)
            else:
                print("❌ Vyčerpány pokusy. Nepodařilo se stáhnout data z OTE.")
                return 2

if __name__ == "__main__":
    sys.exit(main())
