# -*- coding: utf-8 -*-
"""
ovladani_rele.py
- relé se vždy zapne/vypne podle aktuální ceny
- Telegram oznámení se odešle jen při změně stavu oproti poslední_stav.txt
- běhy přesně v X:45, X+1:00, X+1:15, X+1:30
"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import paho.mqtt.client as mqtt

# ====== KONFIGURACE ======
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
CEKANI_SEKUND = 120

# ====== HELPERS ======
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram není nastaven — přeskočeno.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"Telegram error: {e}")

def nacti_ceny():
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"Soubor {CENY_SOUBOR} nenalezen.")
    return pd.read_csv(CENY_SOUBOR)

def je_cena_pod_limitem(df):
    prg_now = datetime.now(ZoneInfo("Europe/Prague")) + pd.Timedelta(minutes=6)
    ctvrthodina_index = prg_now.hour * 4 + prg_now.minute // 15 + 1
    row = df[df["Ctvrthodina"] == ctvrthodina_index]
    if row.empty:
        raise Exception(f"Nenalezena cena pro periodu {ctvrthodina_index}.")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    return (cena < LIMIT_EUR, cena)

def nacti_posledni_stav():
    if not os.path.exists(POSLEDNI_STAV_SOUBOR):
        return None
    try:
        with open(POSLEDNI_STAV_SOUBOR, "r", encoding="utf-8") as f:
            stav = f.read().strip()
            return int(stav) if stav in ("0", "1") else None
    except Exception:
        return None

def uloz_posledni_stav(stav: int):
    with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
        f.write(str(stav))

# ====== MQTT ======
class MqttRelaisController:
    def __init__(self, broker, port, username, password, base_topic):
        self.base = base_topic.rstrip("/")
        self.topic_set = f"{self.base}/1/set"
        self.topic_get = f"{self.base}/1/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.broker = broker
        self.port = port

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(self.topic_get)
            self._connected_event.set()

    def _on_disconnect(self, client, userdata, reason_code, properties, reason_string):
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()
        if payload in ("0", "1"):
            with self._lock:
                self._last_payload = payload
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("MQTT connect timeout")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        with self._lock:
            self._last_payload = None
        self._confirm_event.clear()
        self.client.publish(self.topic_set, desired_state)
        if not self._confirm_event.wait(timeout_seconds):
            return False
        return self._last_payload == desired_state

# ====== LOGIKA ======
def main_cycle():
    df = nacti_ceny()
    pod_limitem, _ = je_cena_pod_limitem(df)
    desired = "1" if pod_limitem else "0"

    posledni = nacti_posledni_stav()
    ctl = MqttRelaisController(
        MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE
    )
    ctl.connect()

    for _ in range(POKUSY):
        if ctl.publish_and_wait_confirmation(desired, CEKANI_SEKUND):
            if posledni != int(desired):
                send_telegram(f"<b>Relé {'zapnuto' if desired=='1' else 'vypnuto'}</b>")
            uloz_posledni_stav(int(desired))
            break

    ctl.disconnect()

def cekej_do(target):
    while True:
        now = datetime.now(ZoneInfo("Europe/Prague"))
        if now >= target:
            return
        time.sleep(min(30, (target - now).total_seconds()))

def nejblizsi_45(now):
    if now.minute < 45:
        return now.replace(minute=45, second=0, microsecond=0)
    return (now + timedelta(hours=1)).replace(minute=45, second=0, microsecond=0)

# ====== START ======
if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Prague"))
    start = nejblizsi_45(now)

    print(f"Čekám na první běh v {start.strftime('%H:%M:%S')}")
    cekej_do(start)

    for i in range(4):
        print(f"Cyklus {i+1}")
        main_cycle()
        if i < 3:
            start += timedelta(minutes=15)
            cekej_do(start)

    print("Hotovo.")
