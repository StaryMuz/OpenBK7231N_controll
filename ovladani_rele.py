# -*- coding: utf-8 -*-
"""
ovladani_rele.py

- relé se řídí podle ceny příslušné řízené čtvrthodiny
- Telegram oznámení se odešle pouze při skutečné změně stavu relé
- workflow se spouští s předstihem před další hodinou
- předstih sepnutí se použije pouze při přechodu OFF → ON
- ostatní změny proběhnou na začátku příslušné čtvrthodiny
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

PREDSTIH_MINUT = 5
REZERVA_START_MINUT = PREDSTIH_MINUT + 2
HRANICE_OKAMZITEHO_STARTU = 60 - REZERVA_START_MINUT + 1

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MQTT_BASE = os.getenv("MQTT_BASE")

GITHUB_TOKEN = os.getenv("MY_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")
GITHUB_WORKFLOW = "ovladani_rele.yml"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
        print(f"Telegram error [{type(e).__name__}]: {e}")

def spustit_dalsi_beh():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Chybí GITHUB_TOKEN nebo GITHUB_REPOSITORY – nelze spustit další běh.")
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        requests.post(url, headers=headers, json={"ref": "main"}, timeout=30)
    except Exception as e:
        print(f"Nelze spustit další workflow [{type(e).__name__}]: {e}")

def commitni_posledni_stav():
    try:
        os.system('git config --global user.name "github-actions"')
        os.system('git config --global user.email "github-actions@github.com"')
        os.system(f"git add {POSLEDNI_STAV_SOUBOR}")
        os.system('git commit -m "Aktualizace posledni_stav.txt" || echo "Žádná změna."')
        os.system('git push || echo "Nic k pushnutí."')
    except Exception as e:
        print(f"Chyba při commitování [{type(e).__name__}]: {e}")

def nacti_ceny():
    if not os.path.exists(CENY_SOUBOR):
        raise FileNotFoundError(f"Soubor {CENY_SOUBOR} nenalezen.")
    return pd.read_csv(CENY_SOUBOR)

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
        with open(POSLEDNI_STAV_SOUBOR, "w", encoding="utf-8") as f:
            f.write(str(stav))
    except Exception as e:
        print(f"Nelze zapsat {POSLEDNI_STAV_SOUBOR} [{type(e).__name__}]: {e}")

# ====== NOVÁ LOGIKA ŘÍZENÍ ČASU ======

def je_predstihove_okno(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))
    return now.minute >= 60 - PREDSTIH_MINUT

def urci_rizenou_ctvrthodinu(predstih=False):
    now = datetime.now(ZoneInfo("Europe/Prague"))

    if predstih:
        cil = now + timedelta(minutes=PREDSTIH_MINUT)
    else:
        cil = now

    return cil.hour * 4 + cil.minute // 15 + 1

def je_cena_pod_limitem(df, predstih=False):
    ctvrthodina_index = urci_rizenou_ctvrthodinu(predstih)

    row = df[df["Ctvrthodina"] == ctvrthodina_index]

    if row.empty:
        raise Exception(f"Nenalezena cena pro periodu {ctvrthodina_index}.")

    cena = float(row.iloc[0]["Cena (EUR/MWh)"])

    start_min = (ctvrthodina_index - 1) * 15
    end_min = start_min + 15

    print(f"Cena {start_min//60:02d}:{start_min%60:02d}–{end_min//60:02d}:{end_min%60:02d}: {cena:.2f} EUR/MWh")

    return cena < LIMIT_EUR, cena
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
            return

        payload = msg.payload.decode(errors="ignore").strip()

        print(f"MQTT {msg.topic}: {payload}")

        if payload in ("1", "0"):
            with self._lock:
                self._last_payload = payload
                self._confirm_event.set()

    def connect(self, timeout=10):
        print(f"MQTT connect na {self.broker}:{self.port}")

        self.client.connect(self.broker, self.port, keepalive=60)
        self.client.loop_start()

        if not self._connected_event.wait(timeout):
            raise TimeoutError("MQTT broker nepotvrdil připojení.")

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
            return self._last_payload == desired_state


# ====== HLAVNÍ LOGIKA ======

def rozhodni_spusteni_cyklu():
    """
    Vrací:
    - True  = cyklus může běžet ihned s předstihem
    - False = čekat na začátek čtvrthodiny

    Předstih se použije pouze při přechodu OFF -> ON.
    """

    df = nacti_ceny()
    posledni_stav = nacti_posledni_stav()

    pod_limitem, cena = je_cena_pod_limitem(df, predstih=True)

    pozadovany_stav = 1 if pod_limitem else 0

    print(f"Poslední stav: {posledni_stav}")
    print(f"Požadovaný stav následující čtvrthodiny: {pozadovany_stav}")

    if posledni_stav == 0 and pozadovany_stav == 1:
        print("Detekován přechod OFF → ON, používám předstih.")
        return True

    print("Předstih není potřeba, čekám na začátek čtvrthodiny.")
    return False


def main_cycle(predstih=False):
    ctl = None

    try:
        df = nacti_ceny()

        pod_limitem, cena = je_cena_pod_limitem(df, predstih)

        desired_payload = "1" if pod_limitem else "0"
        desired_payload_int = int(desired_payload)

        posledni_stav = nacti_posledni_stav()

        print(f"Poslední známý stav: {posledni_stav}")
        print(f"Požadovaný stav relé: {desired_payload}")

        akce_text = "zapnuto" if desired_payload == "1" else "vypnuto"

        ctl = MqttRelaisController(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE)
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

            print(f"Nepotvrzeno, pokus {pokus}")

        if not success:
            cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%H:%M")
            send_telegram(f"<b>Relé nereaguje</b> ({cas}).")

    except Exception as e:
        print(f"Chyba [{type(e).__name__}]: {e}")
        send_telegram(f"Chyba v ovladani_rele.py [{type(e).__name__}]: {e}")

    finally:
        if ctl:
            try:
                ctl.disconnect()
            except Exception:
                pass
# ====== ČASOVACÍ FUNKCE ======

def cekej_do_casoveho_bodu(target_dt):
    while True:
        now = datetime.now(ZoneInfo("Europe/Prague"))
        delta = (target_dt - now).total_seconds()

        if delta <= 0:
            break

        if delta > 240:
            time.sleep(30)
        elif delta > 60:
            time.sleep(10)
        else:
            time.sleep(1)


def zacatek_ctvrthodiny(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))

    minuta = (now.minute // 15) * 15

    return now.replace(
        minute=minuta,
        second=0,
        microsecond=0
    )


def dalsi_ctvrthodina(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))

    minuta = ((now.minute // 15) + 1) * 15

    if minuta >= 60:
        return (now + timedelta(hours=1)).replace(
            minute=0,
            second=0,
            microsecond=0
        )

    return now.replace(
        minute=minuta,
        second=0,
        microsecond=0
    )

def dalsi_cela_hodina(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))

    return (now + timedelta(hours=1)).replace(
        minute=0,
        second=0,
        microsecond=0
    )

def rozhodovaci_cas_ctvrthodiny(now=None):
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Prague"))
    minuta = ((now.minute // 15) + 1) * 15
    if minuta >= 60:
        cil = (now + timedelta(hours=1)).replace(minute=0,second=0,microsecond=0)
    else:
        cil = now.replace(minute=minuta,second=0,microsecond=0)
    return cil - timedelta(minutes=PREDSTIH_MINUT)

# ====== START PROGRAMU ======

if __name__ == "__main__":
    now = datetime.now(ZoneInfo("Europe/Prague"))
    if now.minute < HRANICE_OKAMZITEHO_STARTU:
        print("Běží již nová hodina – první cyklus se spustí ihned.")
        main_cycle(predstih=False)
    else:
        next_hour = dalsi_cela_hodina(now)
        start_rozhodovani = next_hour - timedelta(minutes=PREDSTIH_MINUT)
        print(f"Čekám do rozhodovacího bodu {start_rozhodovani.strftime('%H:%M:%S')}")
        cekej_do_casoveho_bodu(start_rozhodovani)
        if rozhodni_spusteni_cyklu():
            print("První cyklus spuštěn s předstihem.")
            main_cycle(predstih=True)
        else:
            print("Předstih není potřeba, čekám na začátek nové hodiny.")
            cekej_do_casoveho_bodu(next_hour)
            main_cycle(predstih=False)

    # Po prvním cyklu pokračují standardní
    # čtvrthodinové cykly.

while True:
    now = datetime.now(ZoneInfo("Europe/Prague"))
    next_cycle = dalsi_ctvrthodina(now)
    if next_cycle.hour != now.hour:
        break
    rozhodovaci_cas = next_cycle - timedelta(minutes=PREDSTIH_MINUT)
    print(f"Čekám na rozhodnutí před cyklem {rozhodovaci_cas.strftime('%H:%M:%S')}")
    cekej_do_casoveho_bodu(rozhodovaci_cas)
    if rozhodni_spusteni_cyklu():
        print("Cyklus spuštěn s předstihem.")
        main_cycle(predstih=True)
    else:
        print(f"Čekám na začátek čtvrthodiny {next_cycle.strftime('%H:%M:%S')}")
        cekej_do_casoveho_bodu(next_cycle)
        main_cycle(predstih=False)

    # Po ukončení hodiny se spustí další workflow
    # s předstihem REZERVA_START_MINUT.

    now = datetime.now(ZoneInfo("Europe/Prague"))

    if 5 <= now.hour < 21:

        commitni_posledni_stav()

        trigger_time = (
            dalsi_cela_hodina(now)
            -
            timedelta(minutes=REZERVA_START_MINUT)
        )

        print(
            f"Čekám do spuštění dalšího workflow "
            f"{trigger_time.strftime('%H:%M:%S')}"
        )

        cekej_do_casoveho_bodu(trigger_time)

        spustit_dalsi_beh()

    else:

        print(
            "Noční pauza – čekám na ranní spuštění."
        )

        trigger_time = (
            dalsi_cela_hodina(now)
            -
            timedelta(minutes=REZERVA_START_MINUT)
        )

        cekej_do_casoveho_bodu(trigger_time)

        spustit_dalsi_beh()
