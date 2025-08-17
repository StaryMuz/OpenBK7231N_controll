# -*- coding: utf-8 -*-
"""
ovladani_rele.py
Ovládání relé přes Adafruit IO (MQTT) podle cen z ceny_ote.csv.
- posílá ON/OFF do feedu <username>/feeds/rele
- potvrzení stavu čte z feedu <username>/feeds/rele-status
- 3 pokusy, 60 s čekání mezi pokusy
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

# Adafruit IO (uprav v GitHub Secrets)
AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
AIO_FEED_CMD = os.getenv("AIO_FEED", "rele")          # feed pro příkazy
AIO_FEED_STATUS = os.getenv("AIO_FEED_STATUS", "rele-status")  # feed pro potvrzení

# Telegram (GitHub Secrets)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# MQTT parametry (Adafruit IO)
MQTT_BROKER = "io.adafruit.com"
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))  # default TLS port 8883
MQTT_KEEPALIVE = 60

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
    def __init__(self, username, key, feed_cmd, feed_status, broker=MQTT_BROKER, port=MQTT_PORT):
        self.username = username
        self.key = key
        self.feed_cmd = feed_cmd
        self.feed_status = feed_status
        self.broker = broker
        self.port = port

        # topicy Adafruit IO mají formát: <username>/feeds/<feed>
        self.topic_cmd = f"{self.username}/feeds/{self.feed_cmd}"
        self.topic_status = f"{self.username}/feeds/{self.feed_status}"

        # vnitřní stav pro potvrzení
        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        # MQTT client
        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.key)

        # TLS
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
            # subscribe na feed statusu
            client.subscribe(self.topic_status)
            self._connected_event.set()
        else:
            print(f"⚠️ MQTT připojení selhalo rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        print("ℹ️ MQTT odpojeno.")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode(errors="ignore").strip()
        except Exception:
            payload = str(msg.payload)
        print(f"📥 MQTT zpráva na {msg.topic}: {payload}")
        with self._lock:
            val = payload.lower()
            if val in ("on", "off", "1", "0", "true", "false"):
                norm = "ON" if val in ("on", "1", "true") else "OFF"
                self._last_payload = norm
                self._confirm_event.set()

    def connect(self, timeout=10):
        """Připojí klienta a počká na on_connect (timeout v sekundách)."""
        self.client.connect(self.broker, self.port, keepalive=MQTT_KEEPALIVE)
        self.client.loop_start()
        connected = self._connected_event.wait(timeout)
        if not connected:
            raise Exception("Nepodařilo se připojit k MQTT brokeru.")
        time.sleep(0.1)

    def disconnect(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish_and_wait_confirmation(self, desired_state: str, timeout_seconds: int):
        """Publikuje příkaz a čeká na potvrzení na feedu statusu."""
        desired_state = desired_state.upper()
        if desired_state not in ("ON", "OFF"):
            raise ValueError("Stav musí být 'ON' nebo 'OFF'.")

        self._confirm_event.clear()
        with self._lock:
            self._last_payload = None

        # publikuj příkaz
        print(f"➡️ Publikuji na {self.topic_cmd}: {desired_state}")
        self.client.publish(self.topic_cmd, desired_state)

        waited = self._confirm_event.wait(timeout_seconds)
        if not waited:
            print("⏱ Timeout — nebylo přijato potvrzení.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"🔎 Potvrzení z feedu: {self._last_payload} (očekávané: {desired_state})")
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
        akce_text = "ZAPNOUT" if pod_limitem else "VYPNOUT"
        print(f"ℹ️ Rozhodnutí: {akce_text} relé podle ceny {cena:.2f} EUR/MWh (limit {LIMIT_EUR}).")

        if not (AIO_USERNAME and AIO_KEY and AIO_FEED_CMD and AIO_FEED_STATUS):
            raise Exception("Nejsou nastaveny AIO_USERNAME/AIO_KEY/AIO_FEED/AIO_FEED_STATUS v prostředí.")

        controller = MqttRelaisController(AIO_USERNAME, AIO_KEY, AIO_FEED_CMD, AIO_FEED_STATUS)
        controller.connect(timeout=15)

        desired_payload = "ON" if pod_limitem else "OFF"
        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            ok = controller.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND)
            if ok:
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                send_text = f"✅ <b>Relé {'ZAPNUTO' if pod_limitem else 'VYPNUTO'}</b> ({cas} ČR) – potvrzeno (pokus {pokus})."
                send_telegram(send_text)
                print(send_text)
                success = True
                break
            else:
                print(f"❗ Potvrzení se nepodařilo na pokus {pokus}.")
                if pokus < POKUSY:
                    print(f"⏳ Čekám {CEKANI_SEKUND} s před dalším pokusem...")
                    time.sleep(1)
        if not success:
            cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
            send_telegram(f"❌ <b>Relé NEREAGUJE</b> ({cas} ČR) – nepodařilo se přepnout po {POKUSY} pokusech.")
            print("❌ Nepodařilo se relé přepnout po všech pokusech.")
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
