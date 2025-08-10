# ovladani_rele.py
# -*- coding: utf-8 -*-
import os
import sys
import requests
import pandas as pd
from datetime import datetime
import time
from tuyapy2 import TuyaApi

# ====== KONFIGURAČNÍ PROMĚNNÉ ======
LIMIT_EUR = float(os.getenv("LIMIT_EUR", "13.0"))  # EUR/MWh, lze přepsat v secrets
CENY_PATH = os.getenv("CENY_PATH", "ceny_ote.csv")  # cesta k souboru s cenami

# Přístupové údaje (z GitHub Secrets nebo .env)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("TUYA_ACCESS_ID")
API_SECRET = os.getenv("TUYA_ACCESS_SECRET")
EMAIL = os.getenv("TUYA_EMAIL")
PASSWORD = os.getenv("TUYA_PASSWORD")
DEVICE_NAME = os.getenv("DEVICE_NAME")  # Název zařízení v aplikaci Smart Life

# potvrzovací parametry
POKUSY = int(os.getenv("POKUSY", "3"))
CEKANI = int(os.getenv("CEKANI", "60"))  # v sekundách

# ====== FUNKCE ======

def odesli_telegram_zpravu(zprava):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven – přeskočeno")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": zprava,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ Telegram API chyba: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram výjimka: {e}")

def nacti_ceny(cesta):
    if not os.path.exists(cesta):
        raise FileNotFoundError(f"Nenalezen soubor s cenami: {cesta}")
    df = pd.read_csv(cesta)
    # očekáváme sloupce Hodina,Cena (EUR/MWh)
    if "Hodina" not in df.columns or "Cena (EUR/MWh)" not in df.columns:
        raise ValueError("Neplatný formát souboru s cenami.")
    return df

def je_cena_pod_limitem(df):
    now = datetime.now()
    # režim: vyhodnocovat jen mezi 9 a 18 hodinou (včetně)
    if not (9 <= now.hour <= 18):
        print(f"ℹ️ Aktuální hodina {now.hour} mimo rozsah 9–18. Skript končí bez akce.")
        return None  # indikuje: bez akce

    aktualni_hodina = now.hour + 1  # cena platí DO této hodiny
    row = df[df["Hodina"] == aktualni_hodina]
    if row.empty:
        raise LookupError(f"Nelze najít cenu pro hodinu {aktualni_hodina} v souboru.")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    print(f"🔍 Cena pro {aktualni_hodina - 1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh (limit {LIMIT_EUR})")
    return cena < LIMIT_EUR

def najdi_zarizeni(api):
    devices = api.get_all_devices()
    for d in devices:
        try:
            if DEVICE_NAME.lower() in d.name().lower():
                return d
        except Exception:
            continue
    return None

def ovladej_rele(pod_limitem):
    print("🔌 Připojuji se k Tuya API…")
    api = TuyaApi()
    api.init(API_KEY, API_SECRET)
    api.login(EMAIL, PASSWORD)

    device = najdi_zarizeni(api)
    if device is None:
        raise RuntimeError("Zařízení nebylo nalezeno v Tuya účtu. Zkontrolujte propojení Smart Life <-> Tuya IoT.")

    pozadovany_stav = bool(pod_limitem)  # True = ON, False = OFF
    akce_text = "ZAPNUTO" if pozadovany_stav else "VYPNUTO"

    for pokus in range(1, POKUSY + 1):
        print(f"🧪 Pokus {pokus}/{POKUSY} nastavovat stav {akce_text}…")
        try:
            if pozadovany_stav:
                device.turn_on()
            else:
                device.turn_off()
        except Exception as e:
            print(f"⚠️ Chyba při posílání příkazu: {e}")

        print(f"⏳ Čekám {CEKANI} s pro potvrzení...")
        time.sleep(CEKANI)

        # Kontrola stavu
        try:
            status = device.status()
            # knihovny mohou mít různé klíče; nejběžnější je "is_on"
            aktualni = None
            if isinstance(status, dict):
                if "is_on" in status:
                    aktualni = bool(status.get("is_on"))
                else:
                    # fallback: prohledej hodnoty
                    for v in status.values():
                        if isinstance(v, bool):
                            aktualni = v
                            break
            print(f"ℹ️ Stav zařízení (report): {status} ; interpretováno jako: {aktualni}")
            if aktualni == pozadovany_stav:
                cas = datetime.now().strftime("%H:%M")
                zpr = f"✅ <b>Relé {akce_text}</b> ({cas}) – potvrzeno (pokus {pokus})."
                odesli_telegram_zpravu(zpr)
                print("✅ Potvrzeno, končím.")
                return
            else:
                print("⚠️ Stav se neshoduje s požadovaným. Pokračuji v opakování.")
        except Exception as e:
            print(f"⚠️ Chyba při čtení stavu zařízení: {e}")

    # pokud se nedosáhlo požadovaného stavu
    cas = datetime.now().strftime("%H:%M")
    zpr = f"❌ <b>Relé NEREAGUJE</b> ({cas}) – nepodařilo se přepnout na {akce_text} po {POKUSY} pokusech."
    odesli_telegram_zpravu(zpr)
    raise RuntimeError("Nedošlo k potvrzení přepnutí zařízení po více pokusech.")

# ====== HLAVNÍ BĚH ======
def main():
    try:
        df = nacti_ceny(CENY_PATH)
    except Exception as e:
        print(f"❌ Chyba při načítání cen: {e}")
        sys.exit(2)

    try:
        pod_limitem = je_cena_pod_limitem(df)
        if pod_limitem is None:
            # mimo provozní hodiny
            sys.exit(0)
        ovladej_rele(pod_limitem)
        print("🏁 Hotovo.")
    except Exception as e:
        print(f"🛑 Chyba ve skriptu: {e}")
        # poslat telegram o chybě (volitelné)
        try:
            odesli_telegram_zpravu(f"🛑 Chyba ve skriptu ovladani_rele: {e}")
        except Exception:
            pass
        sys.exit(3)

if __name__ == "__main__":
    main()
