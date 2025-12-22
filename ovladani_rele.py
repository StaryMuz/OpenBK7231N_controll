# -*- coding: utf-8 -*-
"""
ovladani_rele.py
- relé se zapíná/vypíná dle ceny
- Telegram jen při změně stavu
- běh po čtvrthodinách
"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import paho.mqtt.client as mqtt

# ===== KONFIGURACE =====
LIMIT_EUR = 13.0
CENY_SOUBOR = "ceny_ote.csv"
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER   = os.getenv("MQTT_USER")
MQTT_PASS   = os.getenv("MQTT_PASS")
MQTT_BASE   = os.getenv("MQTT_BASE")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

POKUSY = 3
CEKANI_SEKUND = 300

# ===== HELPERS =====
def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

def nacti_ceny():
    return pd.read_csv(CENY_SOUBOR)

def je_cena_pod_limitem(df):
    now = datetime.now(ZoneInfo("Europe/Prague")) + pd.Timedelta(minutes=6)
    idx = now.hour * 4 + now.minute // 15 + 1
    row = df[df["Ctvrthodina"] == idx]
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    print(f"🔍 Cena: {cena:.2f} EUR/MWh")
    return cena < LIMIT_EUR, cena

def nacti_posledni_stav():
    if not os.path.exists(POSLEDNI_STAV_SOUBOR):
        return None
    with open(POSLEDNI_STAV_SOUBOR, "r") as f:
        return int(f.read().strip())

def uloz_posledni_stav(stav):
    with open(POSLEDNI_STAV_SOUBOR, "w") as f:
        f.write(str(stav))

# ===== MQTT CONTROLLER =====
class MqttRelaisController:
    def __init__(self):
        self.topic_set = f"{MQTT_BASE}/1/set"
        self.topic_get = f"{MQTT_BASE}/1/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("✅ MQTT připojeno")
            client.subscribe(self.topic_get)
            self._connected_event.set()

    def _on_disconnect(self, client, userdata, reason_code, properties):
        print("ℹ️ MQTT odpojeno")

    def _on_message(self, client, userdata, msg):
        if msg.retain:
            print(f"⚠️ Ignoruji retained zprávu: {msg.payload.decode()}")
            return

        payload = msg.payload.decode().strip()
        print(f"📥 MQTT {msg.topic}: {payload}")

        if payload in ("0", "1"):
            with self._lock:
                self._last_payload = payload
                self._confirm_event.set()

    def connect(self, timeout=10):
        self._confirm_event.clear()
        self._last_payload = None

        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()

        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait(self, value):
        self._confirm_event.clear()
        self.client.publish(self.topic_set, value)

        if not self._confirm_event.wait(CEKANI_SEKUND):
            return False

        return self._last_payload == value

# ===== HLAVNÍ CYKLUS =====
def main_cycle():
    df = nacti_ceny()
    pod_limitem, _ = je_cena_pod_limitem(df)

    desired = "1" if pod_limitem else "0"
    desired_int = int(desired)

    posledni = nacti_posledni_stav()
    print(f"ℹ️ Poslední známý stav: {posledni}")

    ctl = MqttRelaisController()
    ctl.connect()

    try:
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            if ctl.publish_and_wait(desired):
                if posledni != desired_int:
                    send_telegram(f"✅ Relé {'zapnuto' if desired=='1' else 'vypnuto'}")
                uloz_posledni_stav(desired_int)
                return
    finally:
        ctl.disconnect()

# ===== ČASOVÁ LOGIKA =====
def cekej_do(dt):
    while datetime.now(ZoneInfo("Europe/Prague")) < dt:
        time.sleep(20)

def dalsi_ctvrthodina():
    now = datetime.now(ZoneInfo("Europe/Prague"))
    m = ((now.minute // 15) + 1) * 15
    if m >= 60:
        return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return now.replace(minute=m, second=0, microsecond=0)

# ===== START =====
if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Prague"))
    start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    print(f"🕒 Čekám do celé hodiny ({start.strftime('%H:%M:%S')})")
    cekej_do(start)

    for i in range(4):
        print(f"🚀 Spouštím cyklus #{i+1}")
        main_cycle()
        if i < 3:
            cekej_do(dalsi_ctvrthodina())

    print("🏁 Hotovo")
