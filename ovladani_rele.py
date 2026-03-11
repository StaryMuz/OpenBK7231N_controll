# -*- coding: utf-8 -*-

import os
import time
import threading
import requests
import pandas as pd
import json

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt


# ====== KONFIGURACE ======

LIMIT_EUR = 13.0
CENY_SOUBOR = "ceny_ote.csv"
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MQTT_BASE = os.getenv("MQTT_BASE")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GH_TOKEN = os.getenv("GH_TOKEN_CUSTOM")

POKUSY = 3
CEKANI_SEKUND = 120


# ====== TELEGRAM ======

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }

        requests.post(url, data=data, timeout=15)

    except Exception as e:
        print("Telegram chyba:", e)


# ====== CENY ======

def nacti_ceny():

    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError("Soubor s cenami neexistuje")

    return pd.read_csv(CENY_SOUBOR)


def je_cena_pod_limitem(df):

    prg_now = datetime.now(ZoneInfo("Europe/Prague"))

    index = prg_now.hour * 4 + prg_now.minute // 15 + 1

    row = df[df["Ctvrthodina"] == index]

    if row.empty:
        raise Exception("Cena nenalezena")

    cena = float(row.iloc[0]["Cena (EUR/MWh)"])

    start_min = (index - 1) * 15
    end_min = start_min + 15

    start = f"{start_min//60:02d}:{start_min%60:02d}"
    end = f"{end_min//60:02d}:{end_min%60:02d}"

    print(f"Cena {start}–{end}: {cena:.2f} EUR/MWh")

    return cena < LIMIT_EUR


# ====== POSLEDNÍ STAV ======

def nacti_posledni_stav():

    if not os.path.exists(POSLEDNI_STAV_SOUBOR):
        return None

    try:

        with open(POSLEDNI_STAV_SOUBOR, "r") as f:
            return int(f.read().strip())

    except:
        return None


def uloz_posledni_stav(stav):

    try:

        with open(POSLEDNI_STAV_SOUBOR, "w") as f:
            f.write(str(stav))

    except Exception as e:
        print("Nelze uložit stav:", e)


# ====== MQTT ======

class MqttRelaisController:

    def __init__(self, broker, port, username, password, base_topic):

        self.topic_set = f"{base_topic}/2/set"
        self.topic_get = f"{base_topic}/2/get"

        self._event = threading.Event()

        self._payload = None

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )

        self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.broker = broker
        self.port = port

    def _on_connect(self, client, userdata, flags, reason_code, properties):

        if reason_code == 0:

            print("MQTT připojeno")

            client.subscribe(self.topic_get)

    def _on_message(self, client, userdata, msg):

        if msg.retain:
            return

        payload = msg.payload.decode()

        print("MQTT", msg.topic, payload)

        self._payload = payload

        self._event.set()

    def connect(self):

        self.client.connect(self.broker, self.port)

        self.client.loop_start()

        time.sleep(2)

    def disconnect(self):

        self.client.loop_stop()

        self.client.disconnect()

    def publish_and_wait(self, desired):

        self._event.clear()

        self.client.publish(self.topic_set, desired)

        if self._event.wait(CEKANI_SEKUND):

            return self._payload == desired

        return False


# ====== CYKLUS ======

def main_cycle():

    ctl = None

    try:

        df = nacti_ceny()

        zapnout = je_cena_pod_limitem(df)

        payload = "1" if zapnout else "0"

        posledni = nacti_posledni_stav()

        print("Poslední stav:", posledni)

        ctl = MqttRelaisController(
            MQTT_BROKER,
            MQTT_PORT,
            MQTT_USER,
            MQTT_PASS,
            MQTT_BASE
        )

        ctl.connect()

        for pokus in range(POKUSY):

            print("Pokus", pokus + 1)

            if ctl.publish_and_wait(payload):

                print("Potvrzeno")

                stav_int = int(payload)

                if posledni != stav_int:

                    text = "Relé zapnuto" if payload == "1" else "Relé vypnuto"

                    send_telegram(text)

                uloz_posledni_stav(stav_int)

                break

    except Exception as e:

        print("Chyba:", e)

    finally:

        if ctl:
            ctl.disconnect()


# ====== ČAS ======

def cekej_do(target):

    while True:

        now = datetime.now(ZoneInfo("Europe/Prague"))

        if now >= target:
            break

        time.sleep(10)


def dalsi_ctvrthodina():

    now = datetime.now(ZoneInfo("Europe/Prague"))

    minute = ((now.minute // 15) + 1) * 15

    if minute >= 60:

        return (now + timedelta(hours=1)).replace(minute=0, second=0)

    return now.replace(minute=minute, second=0)


# ====== HLAVNÍ PROGRAM ======

if __name__ == "__main__":

    now = datetime.now(ZoneInfo("Europe/Prague"))

    end_hour = 22 if now.month in (3,4,5,6,7,8,9,10) else 19

    if now.minute >= 46:

        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0)

        print("Čekám na další hodinu")

        cekej_do(next_hour)

    cycles = 4 - (now.minute // 15)

    for i in range(cycles):

        print("Cyklus", i+1)

        main_cycle()

        if i < cycles - 1:

            next_q = dalsi_ctvrthodina()

            print("Čekám do", next_q)

            cekej_do(next_q)

    print("Hodina dokončena")

    # ===== SAMOPOKRAČOVÁNÍ =====

    now = datetime.now(ZoneInfo("Europe/Prague"))

    if now.hour < end_hour and GH_TOKEN:

        print("Spouštím další run")

        repo = os.getenv("GITHUB_REPOSITORY")

        workflow = "ovladani_rele.yml"

        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"

        headers = {
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json"
        }

        data = {"ref": "main"}

        try:

            r = requests.post(url, headers=headers, data=json.dumps(data))

            print("API:", r.status_code)

        except Exception as e:

            print("Chyba API:", e)

    else:

        print("Konec dne nebo chybí token")
