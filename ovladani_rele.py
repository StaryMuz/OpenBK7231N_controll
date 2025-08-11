# -*- coding: utf-8 -*-
import pandas as pd
from datetime import datetime
import os
import time
import requests
from tuya_connector import TuyaOpenAPI

# ====== KONFIGURAČNÍ PROMĚNNÉ ======
LIMIT_EUR = 13.0  # Limitní cena v EUR/MWh

# Přístupové údaje z GitHub Secrets / .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("TUYA_ACCESS_ID")
API_SECRET = os.getenv("TUYA_ACCESS_SECRET")
DEVICE_ID = os.getenv("TUYA_DEVICE_ID")  # ID zařízení z Tuya IoT Platform

CENY_SOUBOR = "ceny_ote.csv"  # Ranní CSV se staženými cenami
TUYA_ENDPOINT = "https://openapi.tuyaeu.com"  # EU datacentrum

# ====== FUNKCE ======

def nacti_ceny():
    """Načte lokální CSV s cenami."""
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"❌ Soubor {CENY_SOUBOR} nebyl nalezen!")
    df = pd.read_csv(CENY_SOUBOR)
    return df

def je_cena_aktualni_pod_limitem(df):
    """Z lokálních dat zjistí, zda je cena pro aktuální hodinu pod limitem."""
    aktualni_hodina = datetime.now().hour + 2 + 1  # Cena platí DO této hodiny
    cena_radek = df[df["Hodina"] == aktualni_hodina]
    if cena_radek.empty:
        raise Exception(f"❌ Nenalezena cena pro hodinu {aktualni_hodina}!")
    cena = cena_radek.iloc[0]["Cena (EUR/MWh)"]
    print(f"🔍 Cena pro {aktualni_hodina-1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh")
    return cena < LIMIT_EUR

def odesli_telegram_zpravu(zprava):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven – přeskočeno")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": zprava, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data)
        if resp.status_code != 200:
            print(f"⚠️ Telegram API chyba: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram výjimka: {e}")

def ovladej_rele(pod_limitem, pokusy=3, cekani=60):
    """Opakované pokusy o přepnutí relé s potvrzením stavu (přes Tuya Connector)."""
    print("🔌 Připojuji se k Tuya API…")
    openapi = TuyaOpenAPI(TUYA_ENDPOINT, API_KEY, API_SECRET)
    openapi.connect()

    pozadovany_stav = pod_limitem  # True = ON, False = OFF
    akce_text = "ZAPNUTO" if pozadovany_stav else "VYPNUTO"
    command = [{"code": "switch_1", "value": pozadovany_stav}]

    for pokus in range(1, pokusy + 1):
        print(f"🧪 Pokus {pokus}: nastavování stavu {akce_text}…")
        openapi.post(f"/v1.0/devices/{DEVICE_ID}/commands", {"commands": command})

        time.sleep(cekani)  # čekáme mezi pokusy

        status_data = openapi.get(f"/v1.0/devices/{DEVICE_ID}/status")
        aktualni_stav = None
        for item in status_data.get("result", []):
            if item["code"] == "switch_1":
                aktualni_stav = item["value"]
                break

        if aktualni_stav == pozadovany_stav:
            print(f"✅ Relé úspěšně přepnuto ({akce_text}) na pokus {pokus}")
            cas = datetime.now().strftime("%H:%M")
            odesli_telegram_zpravu(f"✅ <b>Relé {akce_text}</b> ({cas}) – potvrzeno (pokus {pokus})")
            return
        else:
            print(f"⚠️ Nepodařilo se potvrdit stav. Zkusím znovu za {cekani} sekund…")

    # Po neúspěchu všech pokusů
    print(f"❌ Nepodařilo se přepnout relé na požadovaný stav ({akce_text}) po {pokusy} pokusech.")
    cas = datetime.now().strftime("%H:%M")
    odesli_telegram_zpravu(f"❌ <b>Relé NEREAGUJE</b> ({cas}) – nepodařilo se přepnout na {akce_text} po {pokusy} pokusech.")

# ====== HLAVNÍ BĚH ======
if __name__ == "__main__":
    try:
        # ⏱ Omezení času provozu
        hodina = datetime.now().hour + 2
        if hodina < 9 or hodina > 19:
            print(f"⏸ Mimo pracovní interval 9–19 h, skript nic neprovádí (aktuálně {hodina} h).")
        else:
            df = nacti_ceny()
            pod_limitem = je_cena_aktualni_pod_limitem(df)
            ovladej_rele(pod_limitem)
        print("🏁 Hotovo.")
    except Exception as e:
        print(f"🛑 Chyba ve skriptu: {e}")
