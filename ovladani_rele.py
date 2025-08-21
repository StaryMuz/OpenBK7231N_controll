# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes MQTT (Maqiatto) podle cen z ceny_ote.csv.
- 3 pokusy, 60 s čekání mezi pokusy
- potvrzení stavu přes topic /1/get
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
CAS_OD = 9
CAS_DO = 19
LIMIT_EUR = 13.0
CENY_SOUBOR = "ceny_ote.csv"
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

MQTT_BROKER   = os.getenv("MQTT_BROKER")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER")
MQTT_PASS     = os.getenv("MQTT_PASS")
MQTT_BASE     = os.getenv("MQTT_BASE")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

POKUSY = 3
CEKANI_SEKUND = 60   # delší timeout kvůli cyklickému hlášení OBK

# ====== HELPERS ======
def send_telegram(text: str):
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
    aktualni_hodina = prg_now.hour + 1
    row = df[df["Hodina"] == aktualni_hodina]
    if row.empty:
        raise Exception(f"Nenalezena cena pro hodinu {aktualni_hodina}.")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    print(f"🔍 Cena {aktualni_hodina-1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh")
    return (cena < LIMIT_EUR, cena)

def nacti_posledni_stav():
    if not os.path.exists(POSLEDNI_STAV_SOUBOR):
        return None
    with open(POSLEDNI_STAV_SOUBOR, "r", encoding="utf-8") as f:
        stav = f.read().strip()
        if stav in ("1", "0"):
            return stav
        return None

def uloz_posledni_stav(stav: str):
    with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
        f.write(stav)

# ====== MQTT ovládání ======
class MqttRelaisController:
    def __init__(self, broker, port, username, password, base_topic):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.base = base_topic.rstrip("/")

        self.topic_set = f"{self.base}/1/set"
        self.topic_get = f"{self.base}/1/get"

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
        if payload in ("1", "0"):
            with self._lock:
                self._last_payload = payload
                self._confirm_event.set()
        else:
            print(f"⚠️ Neplatná hodnota relé: '{payload}' — ignorováno.")

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru.")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        if desired_state not in ("1", "0"):
            raise ValueError("Stav musí být '1' nebo '0'.")

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
    ctl = None
    try:
        prg_now = datetime.now(ZoneInfo("Europe/Prague"))
        hod = prg_now.hour
        if hod < CAS_OD or hod > CAS_DO:
            print(f"⏸ Mimo {CAS_OD}–{CAS_DO} h ({hod}). Konec.")
            return

        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "1" if pod_limitem else "0"
        akce_text = "ZAPNOUT" if desired_payload == "1" else "VYPNOUT"
        print(f"ℹ️ Rozhodnutí: {akce_text} relé ({cena:.2f} EUR/MWh).")

        ctl = MqttRelaisController(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE)
        ctl.connect(timeout=15)

        success = False
        posledni_stav = nacti_posledni_stav()

        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            if ctl.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND):
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                success = True

                # Telegram jen při změně stavu
                if desired_payload != posledni_stav:
                    msg = f"✅ <b>Relé {akce_text}</b> ({cas}) – potvrzeno."
                    send_telegram(msg)
                else:
                    print("ℹ️ Stav se nezměnil – zpráva na Telegram nebude odeslána.")

                # vždy uložíme poslední stav
                uloz_posledni_stav(desired_payload)
                break
            else:
                print(f"❗ Nepotvrzeno, pokus {pokus}")
                if pokus < POKUSY:
                    time.sleep(10)

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
