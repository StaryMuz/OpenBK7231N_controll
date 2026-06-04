# -*- coding: utf-8 -*-
"""
ovladani_rele.py
- relé se vždy zapne/vypne podle aktuální ceny
- Telegram oznámení se odešle jen při změně stavu oproti posledni_stav.txt
- logika spuštění cyklů po čtvrthodinách v rámci aktuální hodiny
- po poslední čtvrthodině hodiny skript počká do začátku další hodiny a případně spustí nový workflow
"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import paho.mqtt.client as mqtt
import stahni_data

# ====== KONFIGURACE ======
LIMIT_EUR = 13.0
CENY_SOUBOR = "ceny_ote.csv"
POSLEDNI_STAV_SOUBOR = "posledni_stav.txt"

MQTT_BROKER   = os.getenv("MQTT_BROKER")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER")
MQTT_PASS     = os.getenv("MQTT_PASS")
MQTT_BASE     = os.getenv("MQTT_BASE")
GITHUB_TOKEN = os.getenv("MY_PAT")
GITHUB_REPO  = os.getenv("GITHUB_REPOSITORY")
GITHUB_WORKFLOW = "ovladani_rele.yml"

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


def spustit_dalsi_beh():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Chybí GITHUB_TOKEN nebo GITHUB_REPOSITORY – nelze spustit další běh.")
        return

    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        data = {
            "ref": "main"
        }
        r = requests.post(url, headers=headers, json=data, timeout=30)
        if r.status_code == 204:
            print("Spuštěn další běh workflow.")
        else:
            print(f"Chyba při spouštění workflow: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Nelze spustit další workflow: {e}")


def commitni_posledni_stav():
    try:
        print("Provádím commit posledni_stav.txt...")
        os.system('git config --global user.name "github-actions"')
        os.system('git config --global user.email "github-actions@github.com"')
        os.system(f'git add {POSLEDNI_STAV_SOUBOR}')
        os.system('git commit -m "Aktualizace posledni_stav.txt" || echo "Žádná změna – commit se neprovádí."')
        os.system('git push || echo "Nic k pushnutí."')
    except Exception as e:
        print(f"Chyba při commitování: {e}")


def nacti_ceny():
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"Soubor {CENY_SOUBOR} nenalezen.")
    return pd.read_csv(CENY_SOUBOR)


def je_cena_pod_limitem(df):
    prg_now = datetime.now(ZoneInfo("Europe/Prague"))
    ctvrthodina_index = prg_now.hour * 4 + prg_now.minute // 15 + 1
    row = df[df["Ctvrthodina"] == ctvrthodina_index]
    if row.empty:
        raise Exception(f"Nenalezena cena pro periodu {ctvrthodina_index}.")
    cena = float(row.iloc[0]["Cena (EUR/MWh)"])
    start_min = (ctvrthodina_index - 1) * 15
    end_min = start_min + 15
    start_time = f"{start_min // 60:02d}:{start_min % 60:02d}"
    end_time   = f"{end_min // 60:02d}:{end_min % 60:02d}"
    print(f"Cena {start_time}–{end_time}: {cena:.2f} EUR/MWh")
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
        print(f"Ukládám stav {stav} do {POSLEDNI_STAV_SOUBOR}")
        with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
            f.write(str(stav))
    except Exception as e:
        print(f"Nelze zapsat {POSLEDNI_STAV_SOUBOR}: {e}")


# ====== MQTT třída ======
class MqttRelaisController:
    def __init__(self, broker, port, username, password, base_topic):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.base = base_topic.rstrip("/")
        self.topic_set = f"{self.base}/2/set"
        self.topic_get = f"{self.base}/2/get"

        self._lock = threading.Lock()
        self._last_payload = None
        self._confirm_event = threading.Event()
        self._connected_event = threading.Event()

        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(self.username, self.password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"MQTT připojeno {self.broker}:{self.port}")
            client.subscribe(self.topic_get)
            self._connected_event.set()
        else:
            print(f"MQTT chyba reason_code={reason_code}")

    def _on_disconnect(self, client, userdata, reason_code, properties, reason_string):
        print("MQTT odpojeno")
        self._connected_event.clear()

    def _on_message(self, client, userdata, msg):
        if msg.retain:
            print(f"Ignoruji retained zprávu: {msg.payload.decode(errors='ignore')}")
            return

        payload = msg.payload.decode(errors="ignore").strip()
        print(f"MQTT {msg.topic}: {payload}")

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

        with self._lock:
            self._last_payload = None

        self._confirm_event.clear()
        print(f"Publikuji {desired_state} na {self.topic_set}")
        self.client.publish(self.topic_set, desired_state)

        if not self._confirm_event.wait(timeout_seconds):
            print("Timeout — žádné potvrzení.")
            return False

        with self._lock:
            confirmed = (self._last_payload == desired_state)
            print(f"Potvrzeno: {self._last_payload} (oček.: {desired_state})")
            return confirmed


# ====== HLAVNÍ LOGIKA ======
def main_cycle():
    ctl = None
    try:
        df = nacti_ceny()
        pod_limitem, cena = je_cena_pod_limitem(df)
        desired_payload = "1" if pod_limitem else "0"
        desired_payload_int = int(desired_payload)
        akce_text = "zapnuto" if desired_payload == "1" else "vypnuto"

        posledni_stav = nacti_posledni_stav()
        print(f"Poslední známý stav: {posledni_stav}")

        ctl = MqttRelaisController(
            MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE
        )
        ctl.connect(timeout=15)

        success = False
        for pokus in range(1, POKUSY + 1):
            print(f"--- Pokus {pokus}/{POKUSY} ---")
            if ctl.publish_and_wait_confirmation(desired_payload, CEKANI_SEKUND):
                success = True
                cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
                if posledni_stav != desired_payload_int:
                    send_telegram(f"<b>Relé {akce_text}</b> ({cas}).")
                else:
                    print("Stav se nezměnil – Telegram se neposílá.")
                uloz_posledni_stav(desired_payload_int)
                break
            else:
                print(f"Nepotvrzeno, pokus {pokus}")

        if not success:
            cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
            send_telegram(f"<b>Relé nereaguje</b> ({cas}).")

    except Exception as e:
        print(f"Chyba: {e}")
        send_telegram(f"Chyba v ovladani_rele.py: {e}")
    finally:
        if ctl:
            try:
                ctl.disconnect()
            except Exception:
                pass


def cekej_do_casoveho_bodu(target_dt):
    while True:
        now = datetime.now(ZoneInfo("Europe/Prague"))
        delta = (target_dt - now).total_seconds()

        if delta <= 0:
            break

        if delta > 240:      # více než 4 minuty
            time.sleep(30)
        elif delta > 60:     # 1–4 minuty
            time.sleep(10)
        else:                # méně než 1 minuta
            time.sleep(1)


def nejblizsi_ctvrthodina(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))
    minute = ((now.minute // 15) + 1) * 15
    if minute >= 60:
        return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return now.replace(minute=minute, second=0, microsecond=0)


# ====== START ======
if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Prague"))

    if now.minute >= 46:
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        print(f"Čekám do začátku celé hodiny ({next_hour.strftime('%H:%M:%S')})...")
        cekej_do_casoveho_bodu(next_hour)
        now = datetime.now(ZoneInfo("Europe/Prague"))
    else:
        print("Běží již nová hodina – první cyklus se spustí ihned.")

    cycles = 4 - (now.minute // 15)

    for i in range(cycles):
        print(f"Spouštím cyklus #{i+1} v {datetime.now(ZoneInfo('Europe/Prague')).strftime('%H:%M:%S')}")
        main_cycle()
        if i < cycles - 1:
            next_quarter = nejblizsi_ctvrthodina()
            print(f"Čekám do další čtvrthodiny ({next_quarter.strftime('%H:%M:%S')})...")
            cekej_do_casoveho_bodu(next_quarter)

    print(f"Dokončeno v {datetime.now(ZoneInfo('Europe/Prague')).strftime('%H:%M:%S')}")


    now = datetime.now(ZoneInfo("Europe/Prague"))
    if now.hour < 22:

        commitni_posledni_stav()

        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        trigger_time = next_hour - timedelta(seconds=120)

        print(f"Čekám do {trigger_time.strftime('%H:%M:%S')} pro spuštění nového runu...")
        cekej_do_casoveho_bodu(trigger_time)

        print("Spouštím další run workflow pro další hodinu...")
        spustit_dalsi_beh()

    else:
        print("Večerní hodina – nový run nebude spuštěn.")
