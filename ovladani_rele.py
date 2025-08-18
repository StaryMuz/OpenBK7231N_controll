# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes EMQX Cloud (MQTT) podle cen z ceny_ote.csv.
- 3 pokusy, 60 s čekání mezi pokusy
- potvrzení stavu přes odběr status feedu
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

# EMQX MQTT – načte se z GitHub Secrets
MQTT_BROKER   = os.getenv("EMQX_BROKER")     # např. b07ede00.emqxsl.com
MQTT_PORT     = int(os.getenv("EMQX_PORT", "8883"))  # 8883 = TLS, 1883 = bez TLS
MQTT_USERNAME = os.getenv("EMQX_USERNAME")
MQTT_PASSWORD = os.getenv("EMQX_PASSWORD")

# MQTT topics
TOPIC_COMMAND = os.getenv("EMQX_TOPIC_COMMAND", "rele/command")   # kam posíláme ON/OFF
TOPIC_STATUS  = os.getenv("EMQX_TOPIC_STATUS", "rele/status")     # odkud čteme stav

# Telegram (GitHub Secrets)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Pokusy / čekání
POKUSY = 3
CEKANI_SEKUND = 60


# ====== HELPERS ======
def send_telegram(text: str):
    """Odešle text na Telegram (HTML parse_mode)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram není nastaven — přeskočeno odeslání zprávy.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ Chyba Telegram API: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ Výjimka při odesílání Telegram zprávy: {e}")


def nacti_ceny():
    """Načte ceny z lokálního CSV (předpoklad: vytvořeno stahni_data.py)."""
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"Soubor {CENY_SOUBOR} nenalezen.")
    df = pd.read_csv(CENY_SOUBOR)
    return df


def je_cena_pod_limitem(df):
    """Vrátí (bool, cena) jestli je cena pro aktuální hodinu ČR pod limitem."""
    prg_now = datetime.now(ZoneInfo("Europe/Prague"))
    aktualni_hodina = prg_now.hour + 1  # cena platí DO této hodiny
    row = df[df["Hodina"] == aktualni_hodina]
    if row.empty:
        raise Exception(f"Nenalezena cena pro hodinu {aktualni_hodina} (ČR).")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    print(f"🔍 Cena pro {aktualni_hodina-1}.–{aktualni_hodina}. hod: {cena:.2f} EUR/MWh")
    return (cena < LIMIT_EUR, cena)


# ====== MQTT ovládání s potvrzením ======
class MqttRelaisController:
    def __init__(self, broker, port, username, password, topic_cmd, topic_status):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.topic_cmd = topic_cmd
        self.topic_status = topic_status

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client()
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)

        # TLS pokud port = 8883
        if self.port == 8883:
            self.client.tls_set()

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ MQTT připojeno {self.broker}:{self.port}")
            client.subscribe(self.topic_status)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        print("ℹ️ MQTT odpojeno.")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip()
        print(f"📥 MQTT zpráva {msg.topic}: {payload}")
        with self._lock:
            self._last_payload = payload.upper()
            if self._last_payload in ("ON", "OFF"):
                self._confirm_event.set()

    def connect(self, timeout=10):
        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout):
            raise Exception("Nepodařilo se připojit k MQTT brokeru.")
        time.sleep(0.1)

    def disconnect(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        desired_state = desired_state.upper()
        self._confirm_event.clear()
        with self._lock:
            self._last_payload = None

        print(f"➡️ Publikuji na {self.topic_cmd}: {desired_state}")
        self.client.publish(self.topic_cmd, desired_state)

        if not self._confirm_event.wait(timeout_seconds):
            print("⏱ Timeout — nebylo přijato potvrzení.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"🔎 Potvrzený payload: {self._last_payload} (očekávaný: {desired_state})")
            return confirmed


# ====== HLAVNÍ LOGIKA ======
def main():
    try:
        prg_now = datetime.now(ZoneInfo("Europe/Prague"))
        hod = prg_now.hour
        if hod < 9 or hod > 19:
            print(f"⏸ Mimo interval 9–19 h (aktuálně {hod} h ČR). Skript ukončen.")
            return

        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "ON" if pod_limitem else "OFF"
        print(f"ℹ️ Rozhodnutí: {desired_payload} (cena {cena:.2f} EUR/MWh, limit {LIMIT_EUR})")

        controller = MqttRelaisController(MQTT_BROKER, MQTT_PORT, MQTT_USERNAME,
                                          MQTT_PASSWORD, TOPIC_COMMAND, TOPIC_STATUS)
        controller.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            ok = controller.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND)
            if ok:
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                send_telegram(f"✅ <b>Relé {desired_payload}</b> ({cas} ČR) – potvrzeno.")
                success = True
                break
            else:
                print(f"❗ Nepotvrzeno, pokus {pokus}.")
        if not success:
            send_telegram("❌ Relé nereaguje po všech pokusech.")
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
