# -*- coding: utf-8 -*-
import requests
import pandas as pd
from datetime import datetime
import time

# ====== NASTAVENÍ ======
MAX_POKUSU = 5
CEKANI_MEZI_POKUSY = 300  # 5 minut
SOUBOR_CESTA = "ceny_ote.csv"

# ====== FUNKCE ======
def stahni_data():
    dnes = datetime.now()
    den = dnes.strftime("%d")
    mesic = dnes.strftime("%m")
    rok = dnes.strftime("%Y")
    url = f"http://www.ote-cr.cz/kratkodobe-trhy/elektrina/denni-trh/attached/{rok}/month{mesic}/day{den}/DT_{den}_{mesic}_{rok}_CZ.xls"

    print(f"⬇️ Pokus o stažení dat z: {url}")
    for pokus in range(1, MAX_POKUSU + 1):
        try:
            df = pd.read_excel(url, skiprows=23, usecols="A,B", engine="openpyxl")
            df.columns = ["Hodina", "Cena (EUR/MWh)"]
            df.dropna(inplace=True)
            df["Hodina"] = pd.to_numeric(df["Hodina"], errors="coerce").fillna(0).astype(int)
            df["Cena (EUR/MWh)"] = pd.to_numeric(df["Cena (EUR/MWh)"].astype(str).str.replace(",", "."), errors="coerce")
            df = df[df["Hodina"] >= 1]

            df.to_csv(SOUBOR_CESTA, index=False)
            print(f"✅ Data úěšěně uložena do {SOUBOR_CESTA}")
            return

        except Exception as e:
            print(f"❌ Pokus {pokus} selhal: {e}")
            if pokus < MAX_POKUSU:
                print(f"⏳ Čekám {CEKANI_MEZI_POKUSY // 60} minut a zkouším znovu...")
                time.sleep(CEKANI_MEZI_POKUSY)

    print("🛑 Nepodařilo se stáhnout data ani po všech pokusech.")

# ====== SPUŠTĚNÍ ======
if __name__ == "__main__":
    stahni_data()
