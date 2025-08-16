# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes Adafruit IO (MQTT) podle cen z ceny_ote.csv.
- 3 pokusy, 60 s čekání mezi pokusy
- potvrzení stavu přes odběr feedu Adafruit IO
- český čas (Europe/Prague)
- Telegram zpráva se pošle jen při změně stavu relé
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
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

# Adafruit IO (nastaveno v GitHub Secrets)
AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
AIO_FEED = os.getenv("AIO_FEED")  # např. "rele"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# MQTT parametry
MQTT_BROKER = "io.adafruit.com"
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_KEEPALIVE = 60

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

def uloz_stav(stav):
    with open(POSLEDNI_STAV_SOUBOR, "w") as f:
        f.write(stav)

def nacti_stav():
    if os.path.exists(POSLEDNI_STAV_SOUBOR):
        with open(POSLEDNI_STAV_SOUBOR, "r") as f:
            return f.read().strip()
    return None

# ====== MQTT controller ======
class MqttRelaisController:
    def __init__(self, username, key, feed, broker=MQTT_BROKER, port=MQTT_PORT):
        self.username = username
        self.key = key
        self.feed = feed
        self.broker = broker
        self.port = port
        self.topic_feed = f"{self.username}/feeds/{self.feed}"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.key)
        try:
            self.client.tls_set()
        except Exception:
            pass

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ MQTT připojeno {self.broker}:{self.port}")
            client.subscribe(self.topic_feed)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        print("ℹ️ MQTT odpojeno.")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode(errors="ignore").strip()
        except Exception:
            payload = str(msg.payload)
        print(f"📥 MQTT {msg.topic}: {payload}")
        with self._lock:
            val = payload.lower()
            if val in ("on", "off", "1", "0", "true", "false"):
                self._last_payload = "ON" if val in ("on", "1", "true") else "OFF"
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=MQTT_KEEPALIVE)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT.")
        time.sleep(0.1)

    def disconnect(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish_and_wait_confirmation(self, desired_state, timeout_seconds):
        desired_state = desired_state.upper()
        if desired_state not in ("ON", "OFF"):
            raise ValueError("Stav musí být ON/OFF.")

        self._confirm_event.clear()
        with self._lock:
            self._last_payload = None

        print(f"➡️ Publikuji {desired_state} na {self.topic_feed}")
        self.client.publish(self.topic_feed, desired_state)

        if not self._confirm_event.wait(timeout_seconds):
            print("⏱ Timeout — žádné potvrzení.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"🔎 Potvrzení: {self._last_payload} (očekávané: {desired_state})")
            return confirmed

# ====== HLAVNÍ LOGIKA ======
def main():
    try:
        prg_now = datetime.now(ZoneInfo("Europe/Prague"))
        hod = prg_now.hour
        if hod < 9 or hod > 19:
            print(f"⏸ Mimo interval 9–19 h ({hod} h).")
            return

        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "ON" if pod_limitem else "OFF"

        posledni = nacti_stav()
        if posledni == desired_payload:
            print(f"⏸ Stav beze změny ({desired_payload}), ukončuji.")
            return

        controller = MqttRelaisController(AIO_USERNAME, AIO_KEY, AIO_FEED)
        controller.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            ok = controller.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND)
            if ok:
                cas = prg_now.strftime("%H:%M")
                send_telegram(f"✅ <b>Relé {desired_payload}</b> ({cas} ČR) – potvrzeno (pokus {pokus}).")
                success = True
                break
            else:
                print(f"❗ Nepotvrzeno na pokus {pokus}.")
                if pokus < POKUSY:
                    print(f"⏳ Čekám {CEKANI_SEKUND} s...")
                    time.sleep(1)

        uloz_stav(desired_payload)

        if not success:
            cas = prg_now.strftime("%H:%M")
            send_telegram(f"❌ <b>Relé nereaguje</b> ({cas} ČR) – po {POKUSY} pokusech.")
    except Exception as e:
        print(f"🛑 Chyba: {e}")
        send_telegram(f"🛑 Chyba v ovladani_rele.py: {e}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()
