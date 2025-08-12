# -*- coding: utf-8 -*-
import os
import time
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import paho.mqtt.client as mqtt

# ====== KONFIGURAČNÍ PROMĚNNÉ ======
LIMIT_EUR = 13.0  # Limitní cena v EUR/MWh
CENY_SOUBOR = "ceny_ote.csv"  # Ranní CSV se staženými cenami

# Přístupové údaje z GitHub Secrets / .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
AIO_FEED = os.getenv("AIO_FEED")

# ====== FUNKCE ======

def nacti_ceny():
    """Načte lokální CSV s cenami."""
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"❌ Soubor {CENY_SOUBOR} nebyl nalezen!")
    return pd.read_csv(CENY_SOUBOR)

def je_cena_aktualni_pod_limitem(df):
    """Zjistí, zda je cena pro aktuální hodinu (ČR) pod limitem."""
    prague_time = datetime.now(ZoneInfo("Europe/Prague"))
    aktualni_hodina = prague_time.hour + 1  # Cena platí DO této hodiny
    cena_radek = df[df["Hodina"] == aktualni_hodina]
    if cena_radek.empty:
        raise Exception(f"❌ Nenalezena cena pro hodinu {aktualni_hodina}!")
    cena = cena_radek.iloc[0]["Cena (EUR/MWh)"]
    print(f"🔍 Cena pro {aktualni_hodina-1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh")
    return cena < LIMIT_EUR

def odesli_telegram_zpravu(zprava):
    """Odešle zprávu na Telegram."""
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
    """Ovládá relé přes Adafruit IO MQTT."""
    pozadovany_stav = "ON" if pod_limitem else "OFF"
    akce_text = "ZAPNUTO" if pod_limitem else "VYPNUTO"

    broker = "io.adafruit.com"
    port = 1883
    topic = f"{AIO_USERNAME}/feeds/{AIO_FEED}"

    client = mqtt.Client()
    client.username_pw_set(AIO_USERNAME, AIO_KEY)
    client.connect(broker, port, 60)

    for pokus in range(1, pokusy + 1):
        print(f"🧪 Pokus {pokus}: nastavování stavu {akce_text}…")
        client.publish(topic, pozadovany_stav)
        time.sleep(2)  # krátké čekání pro odeslání

        # V tomto testovacím režimu nepotvrzujeme zpětně stav, protože Adafruit IO
        # standardně neposílá aktuální stav bez subscribe.
        cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
        odesli_telegram_zpravu(f"✅ <b>Relé {akce_text}</b> ({cas} ČR) – odesláno na MQTT (pokus {pokus})")
        return  # po prvním úspěšném odeslání končíme

    # Pokud se nepodaří
    cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
    odesli_telegram_zpravu(f"❌ <b>Relé NEREAGUJE</b> ({cas} ČR) – nepodařilo se odeslat MQTT příkaz.")

# ====== HLAVNÍ BĚH ======
if __name__ == "__main__":
    try:
        hodina = datetime.now(ZoneInfo("Europe/Prague")).hour
        if hodina < 9 or hodina > 19:
            print(f"⏸ Mimo pracovní interval 9–19 h, skript nic neprovádí (aktuálně {hodina} h ČR).")
        else:
            df = nacti_ceny()
            pod_limitem = je_cena_aktualni_pod_limitem(df)
            ovladej_rele(pod_limitem)
        print("🏁 Hotovo.")
    except Exception as e:
        print(f"🛑 Chyba ve skriptu: {e}")
