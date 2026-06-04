# -*- coding: utf-8 -*-
import requests
import pandas as pd
import os
import time
import io

import matplotlib
matplotlib.use("Agg")  # headless backend pro GitHub Actions
import matplotlib.pyplot as plt

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

# ====== KONFIGURAČNÍ PROMĚNNÉ ======
LIMIT_EUR = float(os.getenv("LIMIT_EUR", "13.0"))
dnes = datetime.now(ZoneInfo("Europe/Prague"))
zitra = dnes + timedelta(days=1)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HTTP_TIMEOUT = 30  # sekundy

# ====== FUNKCE ======

def ziskej_data_z_ote(max_pokusu=5, cekani=300):
    """Stáhne zítřejší SPOT ceny z OTE, opakuje při neúspěchu."""
    den = zitra.strftime("%d")
    mesic = zitra.strftime("%m")
    rok = zitra.strftime("%Y")
    url = f"https://www.ote-cr.cz/pubweb/attachments/01/{rok}/month{mesic}/day{den}/DT_15MIN_{den}_{mesic}_{rok}_CZ.xlsx"

    for pokus in range(1, max_pokusu + 1):
        try:
            print(f"⬇️ Pokus {pokus}: stahuji data z {url}")
            df = pd.read_excel(
                url,
                skiprows=22,
                usecols="A,C",
                engine="openpyxl",
                storage_options=None
            )
            df.columns = ["Ctvrthodina", "Cena (EUR/MWh)"]
            df.dropna(inplace=True)
            df["Ctvrthodina"] = pd.to_numeric(df["Ctvrthodina"], errors="coerce").fillna(0).astype(int)
            df["Cena (EUR/MWh)"] = pd.to_numeric(
                df["Cena (EUR/MWh)"].astype(str).str.replace(",", "."),
                errors="coerce"
            )
            df = df[df["Ctvrthodina"] >= 1]
            return df
        except Exception as e:
            print(f"⚠️ Chyba: {e}")
            if pokus < max_pokusu:
                print(f"⏳ Čekám {cekani} s před dalším pokusem…")
                time.sleep(cekani)
    raise Exception("❌ Nepodařilo se stáhnout data z OTE.")

def uloz_csv(df, soubor="ceny_ote.csv"):
    tmp = soubor + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, soubor)  # atomický přepis
    print(f"💾 Data uložena do {soubor}")

def vytvor_graf(df):
    """Vytvoří graf cen a vrátí ho jako bytes."""
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(df["Ctvrthodina"], df["Cena (EUR/MWh)"], marker="o")
    ax.axhline(LIMIT_EUR, linestyle="--", label=f"Limit {LIMIT_EUR} EUR/MWh")
    ax.set_xlabel("Hodina")
    ax.set_ylabel("Cena (EUR/MWh)")
    ax.set_title(f"Ceny elektřiny {zitra.strftime('%d.%m.%Y')}")
    ax.grid(True)
    ax.legend()

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close(fig)
    return buf

def zjisti_intervaly_pod_limitem(df):
    """Vrátí seznam intervalů čtvrthodin, kdy je cena < LIMIT_EUR."""
    pod = df[df["Cena (EUR/MWh)"] < LIMIT_EUR]["Ctvrthodina"].tolist()
    intervaly = []

    def ctvrthodina_na_cas(index):
        start = (index - 1) * 15
        hod = start // 60
        minuty = start % 60
        return f"{hod:02d}:{minuty:02d}"

    if pod:
        start = pod[0]
        prev = pod[0]
        for i in pod[1:]:
            if i == prev + 1:
                prev = i
            else:
                intervaly.append(f"{ctvrthodina_na_cas(start)}–{ctvrthodina_na_cas(prev + 1)}")
                start = i
                prev = i
        intervaly.append(f"{ctvrthodina_na_cas(start)}–{ctvrthodina_na_cas(prev + 1)}")
    return intervaly

def odesli_telegram_text(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven – přeskočeno")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, data=data, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            print(f"⚠️ Telegram API chyba: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram výjimka: {e}")

def odesli_telegram_graf(buf, intervaly):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven – přeskočeno")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    popis = "Cena zítra pod limitem v časech:\n" + "\n".join(intervaly)
    files = {"photo": ("graf.png", buf, "image/png")}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": popis}
    try:
        resp = requests.post(url, files=files, data=data, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            print(f"⚠️ Telegram API chyba: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram výjimka: {e}")

# ====== HLAVNÍ BĚH ======

def stahni_data():
    df = ziskej_data_z_ote()
    uloz_csv(df)
    intervaly = zjisti_intervaly_pod_limitem(df)

    if intervaly:
        graf_buf = vytvor_graf(df)
        odesli_telegram_graf(graf_buf, intervaly)
    else:
        odesli_telegram_text("ℹ️ Zítra žádné ceny pod limitem.")

    print("🏁 Hotovo.")


if __name__ == "__main__":
    stahni_data()
