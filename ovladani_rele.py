# -*- coding: utf-8 -*-
"""
ovladani_rele.py
- relé se zapne / vypne podle aktuální ceny
- Telegram se pošle jen při změně stavu
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

# ====== KONFIGURACE ======
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

# ====== TELEGRAM ======
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=15
        )
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

# ====== DATA ======
def nacti_ceny():
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError("Chybí ceny_ote.csv")
    return pd.read_csv(CENY_SOUBOR)

def je_cena_pod_limitem(df):
    now = datetime.now(ZoneInfo("Europe/Prague")) + pd.Timedelta(minutes=6)
    index = now.hour * 4 + now.minute // 15 + 1

    row = df[df["Ctvrthodina"] == index]
    if row.empty:
        raise Exception(f"Nenalezena cena pro periodu {index}")

    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    return cena < LIMIT_EUR, cena

def nacti_posledni_stav():
    try:
        with open(POSLEDNI_STAV_SOUBOR, "r", encoding="utf-8") as f:
            v = f.read().strip()
            return int(v) if v in ("0", "1") else None
    except Exception:
        return None

def uloz_posledni_stav(stav: int):
    with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
        f.write(str(stav))

# ====== MQTT ======
class MqttRelaisController:
    def __init__(self):
        self.topic_set = f"{MQTT_BASE.rstrip('/')}/1/set"
        self.topic_get = f"{MQTT_BASE.rstrip('/')}/1/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("✅ MQTT připojeno")
            client.subscribe(self.topic_get)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT connect failed: {reason_code}")

    def _on_disconnect(self, client, userdata, reason_code, properties):
        print("ℹ️ MQTT odpojeno")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()

        # DŮLEŽITÉ – ignorujeme retained
        if msg.retain:
            print(f"⚠️ Ignoruji retained zprávu: {payload}")
            return

        if payload in ("0", "1"):
            with self._lock:
                self._last_payload = payload
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait(self, state: str):
        self._confirm_event.clear()
        self._last_payload = None

        self.client.publish(self.topic_set, state)

        if not self._confirm_event.wait(CEKANI_SEKUND):
            return False

        return self._last_payload == state

# ====== HLAVNÍ CYKLUS ======
def main_cycle():
    ctl = None
    try:
        df = nacti_ceny()
        pod_limitem, _ = je_cena_pod_limitem(df)

        desired = "1" if pod_limitem else "0"
        desired_int = int(desired)

        posledni = nacti_posledni_stav()

        ctl = MqttRelaisController()
        ctl.connect()

        for _ in range(POKUSY):
            if ctl.publish_and_wait(desired):
                if posledni != desired_int:
                    send_telegram(
                        f"✅ <b>Relé {'zapnuto' if desired=='1' else 'vypnuto'}</b>"
                    )
                uloz_posledni_stav(desired_int)
                return

        send_telegram("❗ <b>Relé nereaguje</b>")

    except Exception as e:
        send_telegram(f"🛑 Chyba v ovladani_rele.py: {e}")

    finally:
        if ctl:
            ctl.disconnect()

# ====== ČASOVÁ LOGIKA ======
def cekej_do(dt):
    while True:
        now = datetime.now(ZoneInfo("Europe/Prague"))
        if now >= dt:
            return
        time.sleep(30)

def nejblizsi_ctvrthodina():
    now = datetime.now(ZoneInfo("Europe/Prague"))
    minute = ((now.minute // 15) + 1) * 15
    if minute >= 60:
        return (now + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
    return now.replace(minute=minute, second=0, microsecond=0)

# ====== START ======
if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Prague"))
    dalsi_hodina = (now + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )

    cekej_do(dalsi_hodina)

    for i in range(4):
        main_cycle()
        if i < 3:
            cekej_do(nejblizsi_ctvrthodina())

    print("🏁 Hotovo")
