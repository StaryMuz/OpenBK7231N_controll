# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Upraveno dle požadavků:
- relé se vždy zapne/vypne podle aktuální ceny bez porovnání s minulým stavem
- Telegram oznámení se odešle jen při změně stavu oproti poslední_stav.txt
- do souboru se uloží nový stav jen při potvrzeném přepnutí
- vyhodnocení ceny probíhá podle aktuální započaté čtvrthodiny (1–96)
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
LIMIT_EUR = 13.0
CENY_SOUBOR = "ceny_ote.csv"
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

# MQTT (z GitHub secrets)
MQTT_BROKER   = os.getenv("MQTT_BROKER")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER")
MQTT_PASS     = os.getenv("MQTT_PASS")
MQTT_BASE     = os.getenv("MQTT_BASE")  # např. starymuz@centrum.cz/rele

# Telegram (volitelné)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Pokusy / čekání
POKUSY = 3
CEKANI_SEKUND = 300

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
    """Vrátí (True/False, cena) pro aktuální započatou čtvrthodinu."""
    prg_now = datetime.now(ZoneInfo("Europe/Prague")) + pd.Timedelta(minutes=10)

    # index čtvrthodiny (1–96), započatá perioda
    ctvrthodina_index = prg_now.hour * 4 + prg_now.minute // 15 + 1


    row = df[df["Ctvrthodina"] == ctvrthodina_index]
    if row.empty:
        raise Exception(f"Nenalezena cena pro periodu {ctvrthodina_index}.")

    cena = float(row.iloc[0]["Cena (EUR/MWh)"])

    # převod indexu zpět na časové okno pro log
    start_min = (ctvrthodina_index - 1) * 15
    end_min = start_min + 15
    start_time = f"{start_min // 60:02d}:{start_min % 60:02d}"
    end_time   = f"{end_min // 60:02d}:{end_min % 60:02d}"

    print(f"🔍 Cena {start_time}–{end_time}: {cena:.2f} EUR/MWh")
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
    try:
        print(f"💾 Ukládám stav {stav} do {POSLEDNI_STAV_SOUBOR}")
        with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
            f.write(str(stav))
    except Exception as e:
        print(f"⚠️ Nelze zapsat {POSLEDNI_STAV_SOUBOR}: {e}")

# ====== MQTT třída ======
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
        if msg.retain:
            print(f"⚠️ Ignoruji retained zprávu: {payload}")
            return
        print(f"📥 MQTT {msg.topic}: {payload}")
        if payload in ("1", "0"):
            with self._lock:
                self._last_payload = payload
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
        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "1" if pod_limitem else "0"
        desired_payload_int = int(desired_payload)
        akce_text = "zapnuto" if desired_payload == "1" else "vypnuto"

        posledni_stav = nacti_posledni_stav()
        print(f"ℹ️ Poslední známý stav: {posledni_stav}")

        ctl = MqttRelaisController(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE)
        ctl.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")

            if ctl.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND):
                success = True
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")

                # Oznámení jen při změně oproti souboru
                if posledni_stav != desired_payload_int:
                    msg = f"✅ <b>Relé {akce_text}</b> ({cas})."
                    send_telegram(msg)
                else:
                    print("ℹ️ Stav se nezměnil – Telegram se neposílá.")

                # uložíme jen po potvrzení
                uloz_posledni_stav(desired_payload_int)
                break
            else:
                print(f"❗ Nepotvrzeno, pokus {pokus}")

        if not success:
            cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
            send_telegram(f"❗ <b>Relé nereaguje</b> ({cas}).")

    except Exception as e:
        print(f"🛑 Chyba: {e}")
        send_telegram(f"🛑 Chyba v ovladani_rele.py: {e}")
    finally:
        try:
            if ctl:
                ctl.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()
