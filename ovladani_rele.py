# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes MQTT (Maqiatto) podle cen z ceny_ote.csv.
- 3 pokusy, 60 s čekání mezi pokusy
- potvrzení stavu přes kanál /get
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
CENY_SOUBOR = "ceny_ote.csv"   # soubor s cenami (generuje stahni_data.py)

# MQTT (Maqiatto) — načítáno z GitHub Secrets
MQTT_BROKER = os.getenv("MQTT_BROKER", "maqiatto.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")       # např. "starymuz@centrum.cz"
MQTT_PASS = os.getenv("MQTT_PASS")
MQTT_BASE = os.getenv("MQTT_BASE", "rele")  # základ topicu, např. "rele"

# Telegram (GitHub Secrets)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Pokusy / čekání
POKUSY = 3
CEKANI_SEKUND = 60


# ====== HELPERS ======
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven — přeskočeno odeslání zprávy.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"⚠️ Výjimka při odesílání Telegram zprávy: {e}")


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


# ====== MQTT ovládání ======
class MqttRelaisController:
    def __init__(self, broker, port, user, passwd, base_topic):
        self.broker = broker
        self.port = port
        self.user = user
        self.passwd = passwd
        self.base_topic = f"{user}/{base_topic}"  # např. starymuz@centrum.cz/rele

        self.topic_cmd = f"{self.base_topic}/1/set"
        self.topic_stat = f"{self.base_topic}/1/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client()
        self.client.username_pw_set(self.user, self.passwd)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ MQTT připojeno {self.broker}:{self.port}")
            client.subscribe(self.topic_stat)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        print("ℹ️ MQTT disconnected.")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip().upper()
        print(f"📥 MQTT zpráva {msg.topic}: {payload}")
        with self._lock:
            self._last_payload = payload
            if payload in ("ON", "OFF", "1", "0"):
                norm = "ON" if payload in ("ON", "1") else "OFF"
                self._last_payload = norm
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru.")
        time.sleep(0.1)

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        desired_state = desired_state.upper()
        if desired_state not in ("ON", "OFF"):
            raise ValueError("Stav musí být ON nebo OFF.")

        self._confirm_event.clear()
        with self._lock:
            self._last_payload = None

        print(f"➡️ Publikuji {desired_state} na {self.topic_cmd}")
        self.client.publish(self.topic_cmd, desired_state)

        if not self._confirm_event.wait(timeout_seconds):
            print("⏱ Timeout — potvrzení nepřišlo.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"🔎 Potvrzení: {self._last_payload} (očekávané: {desired_state})")
            return confirmed


# ====== HLAVNÍ LOGIKA ======
def main():
    try:
        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "ON" if pod_limitem else "OFF"

        controller = MqttRelaisController(
            MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE
        )
        controller.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            ok = controller.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND)
            if ok:
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                send_telegram(f"✅ Relé {desired_payload} ({cas} ČR) – potvrzeno.")
                success = True
                break
            else:
                print("❗ Nepotvrzeno.")
        if not success:
            send_telegram("❌ Relé NEREAGUJE")
    except Exception as e:
        print(f"🛑 Chyba: {e}")
        send_telegram(f"🛑 Chyba v ovladani_rele.py: {e}")


if __name__ == "__main__":
    main()
