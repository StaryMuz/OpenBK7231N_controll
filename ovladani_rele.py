# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes MQTT (Maqiatto) podle cen z ceny_ote.csv.
- 3 pokusy, 60 s čekání mezi pokusy
- potvrzení stavu přes topic /get
- český čas (Europe/Prague)
"""

import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import paho.mqtt.client as mqtt

# ====== KONFIGURACE ======
LIMIT_EUR = 13.0               # limit v EUR/MWh
CENY_SOUBOR = "ceny_ote.csv"   # soubor s cenami

# MQTT (z GitHub secrets)
MQTT_BROKER   = os.getenv("MQTT_BROKER")   # např. maqiatto.com
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER")     # tvůj login (např. email)
MQTT_PASS     = os.getenv("MQTT_PASS")     # heslo
MQTT_BASE     = os.getenv("MQTT_BASE")     # např. starymuz@centrum.cz/rele

# Telegram (volitelné)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Pokusy / čekání
POKUSY = 3
CEKANI_SEKUND = 60

# ====== HELPERS ======
def send_telegram(text: str):
    """Odešle text na Telegram (HTML parse_mode)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven — přeskočeno.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

def nacti_ceny():
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"Soubor {CENY_SOUBOR} nenalezen.")
    return pd.read_csv(CENY_SOUBOR)

def je_cena_pod_limitem(df):
    prg_now = datetime.now(ZoneInfo("Europe/Prague"))
    aktualni_hodina = prg_now.hour + 1  # cena platí DO této hodiny
    row = df[df["Hodina"] == aktualni_hodina]
    if row.empty:
        raise Exception(f"Nenalezena cena pro hodinu {aktualni_hodina}.")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    print(f"🔍 Cena {aktualni_hodina-1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh")
    return (cena < LIMIT_EUR, cena)

# ====== MQTT ovládání ======
class MqttRelaisController:
    def __init__(self, broker, port, username, password, base_topic):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.base = base_topic.rstrip("/")  # bez koncové /
        self.topic_set = f"{self.base}/set"
        self.topic_get = f"{self.base}/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ MQTT připojeno {self.broker}:{self.port}")
            client.subscribe(self.topic_get)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT chyba rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        print("ℹ️ MQTT odpojeno")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()
        print(f"📥 MQTT {msg.topic}: {payload}")
        with self._lock:
            val = payload.lower()
            if val in ("on", "off", "1", "0", "true", "false"):
                norm = "ON" if val in ("on", "1", "true") else "OFF"
                self._last_payload = norm
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru.")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        desired_state = desired_state.upper()
        if desired_state not in ("ON", "OFF"):
            raise ValueError("Stav musí být 'ON' nebo 'OFF'.")

        self._confirm_event.clear()
        with self._lock:
            self._last_payload = None

        print(f"➡️ Publikuji {desired_state} na {self.topic_set}")
        self.client.publish(self.topic_set, desired_state)

        if not self._confirm_event.wait(timeout_seconds):
            print("⏱ Timeout — žádné potvrzení.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"🔎 Potvrzeno: {self._last_payload} (oček.: {desired_state})")
            return confirmed

# ====== HLAVNÍ LOGIKA ======
def main():
    try:
        prg_now = datetime.now(ZoneInfo("Europe/Prague"))
        hod = prg_now.hour
        if hod < 9 or hod > 24:
            print(f"⏸ Mimo 9–19 h ({hod}). Konec.")
            return

        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "ON" if pod_limitem else "OFF"
        akce_text = "ZAPNOUT" if pod_limitem else "VYPNOUT"
        print(f"ℹ️ Rozhodnutí: {akce_text} relé ({cena:.2f} EUR/MWh).")

        ctl = MqttRelaisController(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE)
        ctl.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            if ctl.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND):
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                msg = f"✅ <b>Relé {akce_text}</b> ({cas}) – potvrzeno."
                send_telegram(msg)
                success = True
                break
            else:
                print(f"❗ Nepotvrzeno, pokus {pokus}")
                if pokus < POKUSY:
                    time.sleep(5)
        if not success:
            cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
            send_telegram(f"❌ <b>Relé nereaguje</b> ({cas}).")
    except Exception as e:
        print(f"🛑 Chyba: {e}")
        send_telegram(f"🛑 Chyba v ovladani_rele.py: {e}")
    finally:
        try:
            ctl.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()
