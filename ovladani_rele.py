# -*- coding: utf-8 -*-
"""
ovladani_rele.py

- relé se řídí podle ceny příslušné řízené čtvrthodiny
- Telegram oznámení se odešle pouze při skutečné změně stavu relé
- workflow může být spuštěno s předstihem před celou hodinou
- první cyklus může proběhnout již v předstihovém okně
- další cykly pokračují po čtvrthodinách
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

# časování workflow a cyklů

PREDSTIH_MINUT = 5
REZERVA_START_MINUT = PREDSTIH_MINUT + 2

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

# ====== STAVOVÉ PROMĚNNÉ ======

# True = probíhá souvislé období nízkých cen
# False = nízké ceny právě nezačaly nebo skončily

trvaji_nizke_ceny = False

# ====== HELPERS ======

def send_telegram(text: str):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "Telegram není nastaven — přeskočeno."
        )
        return

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }

        requests.post(
            url,
            data=data,
            timeout=15
        )

    except Exception as e:

        print(
            f"Telegram error "
            f"[{type(e).__name__}]: {e}"
        )

def spustit_dalsi_beh():

    if not GITHUB_TOKEN or not GITHUB_REPO:

        print(
            "Chybí GITHUB_TOKEN "
            "nebo GITHUB_REPOSITORY."
        )

        return

    try:

        url = (
            f"https://api.github.com/repos/"
            f"{GITHUB_REPO}/actions/workflows/"
            f"{GITHUB_WORKFLOW}/dispatches"
        )

        headers = {

            "Authorization":
                f"Bearer {GITHUB_TOKEN}",

            "Accept":
                "application/vnd.github+json"
        }

        data = {
            "ref": "main"
        }

        r = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=30
        )

        if r.status_code == 204:

            print(
                "Spuštěn další běh workflow."
            )

        else:

            print(
                f"Chyba při spouštění workflow: "
                f"{r.status_code} {r.text}"
            )

    except Exception as e:

        print(
            f"Nelze spustit další workflow "
            f"[{type(e).__name__}]: {e}"
        )

def commitni_posledni_stav():

    try:

        print(
            "Provádím commit "
            "posledni_stav.txt..."
        )

        os.system(
            'git config --global '
            'user.name "github-actions"'
        )

        os.system(
            'git config --global '
            'user.email '
            '"github-actions@github.com"'
        )

        os.system(
            f"git add {POSLEDNI_STAV_SOUBOR}"
        )

        os.system(
            'git commit '
            '-m "Aktualizace posledni_stav.txt" '
            '|| echo "Žádná změna."'
        )

        os.system(
            'git push || echo "Nic k pushnutí."'
        )

    except Exception as e:

        print(
            f"Chyba při commitování "
            f"[{type(e).__name__}]: {e}"
        )

def nacti_ceny():

    if not os.path.exists(
        CENY_SOUBOR
    ):

        raise FileNotFoundError(
            f"Soubor {CENY_SOUBOR} nenalezen."
        )

    return pd.read_csv(
        CENY_SOUBOR
    )

def urci_rizenou_ctvrthodinu():

    """
    Určí čtvrthodinu, podle které
    se má řídit první cyklus.

    Pokud je workflow spuštěno
    v předstihovém okně X:55–X:59,
    použije se první čtvrthodina
    následující hodiny.

    Pokud je spuštěno po začátku hodiny,
    použije se aktuální čtvrthodina.
    """

    now = datetime.now(
        ZoneInfo("Europe/Prague")
    )

    if now.minute >= (
        60 - PREDSTIH_MINUT
    ):

        if now.hour == 23:

            return 1

        return (
            (now.hour + 1)
            *
            4
            +
            1
        )

    return (
        now.hour
        *
        4
        +
        now.minute // 15
        +
        1
    )
def je_cena_pod_limitem(df):

    ctvrthodina_index = (
        urci_rizenou_ctvrthodinu()
    )

    row = df[
        df["Ctvrthodina"]
        ==
        ctvrthodina_index
    ]

    if row.empty:

        raise Exception(
            f"Nenalezena cena pro periodu "
            f"{ctvrthodina_index}."
        )

    cena = float(
        row.iloc[0]["Cena (EUR/MWh)"]
    )

    start_min = (
        (ctvrthodina_index - 1)
        *
        15
    )

    end_min = start_min + 15

    start_time = (
        f"{start_min // 60:02d}:"
        f"{start_min % 60:02d}"
    )

    end_time = (
        f"{end_min // 60:02d}:"
        f"{end_min % 60:02d}"
    )

    print(
        f"Cena {start_time}–{end_time}: "
        f"{cena:.2f} EUR/MWh"
    )

    return (
        cena < LIMIT_EUR,
        cena
    )

def nacti_posledni_stav():

    if not os.path.exists(
        POSLEDNI_STAV_SOUBOR
    ):

        return None

    try:

        with open(
            POSLEDNI_STAV_SOUBOR,
            "r",
            encoding="utf-8"
        ) as f:

            stav = f.read().strip()

            if stav in (
                "0",
                "1"
            ):

                return int(stav)

            return None

    except Exception:

        return None

def uloz_posledni_stav(stav: int):

    try:

        print(
            f"Ukládám stav {stav} "
            f"do {POSLEDNI_STAV_SOUBOR}"
        )

        with open(
            POSLEDNI_STAV_SOUBOR,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                str(stav)
            )

    except Exception as e:

        print(
            f"Nelze zapsat "
            f"{POSLEDNI_STAV_SOUBOR} "
            f"[{type(e).__name__}]: {e}"
        )

class MqttRelaisController:

    def __init__(
        self,
        broker,
        port,
        username,
        password,
        base_topic
    ):

        self.broker = broker
        self.port = port
        self.username = username
        self.password = password

        self.base = (
            base_topic.rstrip("/")
        )

        self.topic_set = (
            f"{self.base}/2/set"
        )

        self.topic_get = (
            f"{self.base}/2/get"
        )

        self._lock = threading.Lock()

        self._last_payload = None

        self._confirm_event = (
            threading.Event()
        )

        self._connected_event = (
            threading.Event()
        )

        self.client = mqtt.Client(
            callback_api_version=
            mqtt.CallbackAPIVersion.VERSION2
        )

        self.client.username_pw_set(
            self.username,
            self.password
        )

        self.client.on_connect = (
            self._on_connect
        )

        self.client.on_disconnect = (
            self._on_disconnect
        )

        self.client.on_message = (
            self._on_message
        )

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties
    ):

        if reason_code == 0:

            print(
                f"MQTT připojeno "
                f"{self.broker}:{self.port}"
            )

            client.subscribe(
                self.topic_get
            )

            self._connected_event.set()

        else:

            print(
                f"MQTT chyba "
                f"reason_code={reason_code}"
            )

    def _on_disconnect(
        self,
        client,
        userdata,
        reason_code,
        properties,
        reason_string
    ):

        print(
            "MQTT odpojeno"
        )

        self._connected_event.clear()

    def _on_message(
        self,
        client,
        userdata,
        msg
    ):

        if msg.retain:

            print(
                "Ignoruji retained zprávu: "
                f"{msg.payload.decode(errors='ignore')}"
            )

            return

        payload = (
            msg.payload
            .decode(errors="ignore")
            .strip()
        )

        print(
            f"MQTT {msg.topic}: "
            f"{payload}"
        )

        if payload in (
            "1",
            "0"
        ):

            with self._lock:

                self._last_payload = payload

                self._confirm_event.set()

    def connect(
        self,
        timeout=10
    ):

        print(
            f"MQTT connect na "
            f"{self.broker}:{self.port}"
        )

        self.client.connect(
            self.broker,
            self.port,
            keepalive=60
        )

        self.client.loop_start()

        if not self._connected_event.wait(
            timeout
        ):

            raise TimeoutError(
                "MQTT broker "
                "nepotvrdil připojení."
            )

    def disconnect(self):

        self.client.loop_stop()

        self.client.disconnect()

    def publish_and_wait_confirmation(
        self,
        desired_state: str,
        timeout_seconds: int
    ):

        if desired_state not in (
            "1",
            "0"
        ):

            raise ValueError(
                "Stav musí být '1' nebo '0'."
            )

        with self._lock:

            self._last_payload = None

        self._confirm_event.clear()

        print(
            f"Publikuji "
            f"{desired_state} "
            f"na {self.topic_set}"
        )

        self.client.publish(
            self.topic_set,
            desired_state
        )

        if not self._confirm_event.wait(
            timeout_seconds
        ):

            print(
                "Timeout — "
                "žádné potvrzení."
            )

            return False

        with self._lock:

            confirmed = (
                self._last_payload
                ==
                desired_state
            )

        return confirmed
def main_cycle():

    global trvaji_nizke_ceny

    ctl = None

    try:

        df = nacti_ceny()

        pod_limitem, cena = (
            je_cena_pod_limitem(df)
        )

        desired_payload = (
            "1"
            if pod_limitem
            else "0"
        )

        desired_payload_int = int(
            desired_payload
        )

        akce_text = (
            "zapnuto"
            if desired_payload == "1"
            else "vypnuto"
        )

        # ---------------------------------------------
        # Určení, zda pokračují nízké ceny
        # nebo právě začínají
        # ---------------------------------------------

        posledni_stav = (
            nacti_posledni_stav()
        )

        print(
            f"Poslední známý stav: "
            f"{posledni_stav}"
        )

        if posledni_stav is None:

            trvaji_nizke_ceny = (
                desired_payload_int == 1
            )

        else:

            trvaji_nizke_ceny = (
                posledni_stav == 1
                and
                desired_payload_int == 1
            )

        print(
            f"Trvají nízké ceny: "
            f"{trvaji_nizke_ceny}"
        )

        ctl = MqttRelaisController(
            MQTT_BROKER,
            MQTT_PORT,
            MQTT_USER,
            MQTT_PASS,
            MQTT_BASE
        )

        ctl.connect(
            timeout=15
        )

        success = False

        for pokus in range(
            1,
            POKUSY + 1
        ):

            print(
                f"--- Pokus "
                f"{pokus}/{POKUSY} ---"
            )

            if ctl.publish_and_wait_confirmation(
                desired_payload,
                CEKANI_SEKUND
            ):

                success = True

                cas = (
                    datetime.now(
                        ZoneInfo("Europe/Prague")
                    )
                    .strftime("%H:%M")
                )

                if (
                    posledni_stav
                    != desired_payload_int
                ):

                    send_telegram(
                        f"<b>Relé "
                        f"{akce_text}"
                        f"</b> ({cas})."
                    )

                else:

                    print(
                        "Stav se nezměnil "
                        "– Telegram se neposílá."
                    )

                uloz_posledni_stav(
                    desired_payload_int
                )

                break

            else:

                print(
                    f"Nepotvrzeno, "
                    f"pokus {pokus}"
                )

        if not success:

            cas = (
                datetime.now(
                    ZoneInfo("Europe/Prague")
                )
                .strftime("%H:%M")
            )

            send_telegram(
                f"<b>Relé nereaguje"
                f"</b> ({cas})."
            )

    except Exception as e:

        print(
            f"Chyba "
            f"[{type(e).__name__}]: "
            f"{e}"
        )

        send_telegram(
            f"Chyba v "
            f"ovladani_rele.py "
            f"[{type(e).__name__}]: "
            f"{e}"
        )

    finally:

        if ctl:

            try:

                ctl.disconnect()

            except Exception:

                pass

def cekej_do_casoveho_bodu(target_dt):

    while True:

        now = datetime.now(
            ZoneInfo("Europe/Prague")
        )

        delta = (
            target_dt - now
        ).total_seconds()

        if delta <= 0:

            break

        if delta > 240:

            time.sleep(30)

        elif delta > 60:

            time.sleep(10)

        else:

            time.sleep(1)

def nejblizsi_ctvrthodina(now=None):

    if now is None:

        now = datetime.now(
            ZoneInfo("Europe/Prague")
        )

    minute = (
        (now.minute // 15) + 1
    ) * 15

    if minute >= 60:

        return (
            now + timedelta(hours=1)
        ).replace(
            minute=0,
            second=0,
            microsecond=0
        )

    return now.replace(
        minute=minute,
        second=0,
        microsecond=0
    )
        if payload in ("1", "0"):

            with self._lock:

                self._last_payload = payload

                self._confirm_event.set()

    def connect(
        self,
        timeout=10
    ):

        print(
            f"MQTT connect na "
            f"{self.broker}:{self.port}"
        )

        self.client.connect(
            self.broker,
            self.port,
            keepalive=60
        )

        self.client.loop_start()

        if not self._connected_event.wait(
            timeout
        ):
            raise TimeoutError(
                "MQTT broker nepotvrdil připojení."
            )

    def disconnect(self):

        self.client.loop_stop()
        self.client.disconnect()

    def publish_and_wait_confirmation(
        self,
        desired_state: str,
        timeout_seconds: int
    ):

        if desired_state not in (
            "1",
            "0"
        ):
            raise ValueError(
                "Neplatný požadovaný stav."
            )

        with self._lock:

            self._last_payload = None

        self._confirm_event.clear()

        print(
            f"Publikuji stav {desired_state}"
        )

        self.client.publish(
            self.topic_set,
            desired_state
        )

        if not self._confirm_event.wait(
            timeout_seconds
        ):

            print(
                "Nepřišlo potvrzení MQTT."
            )

            return False

        with self._lock:

            return (
                self._last_payload
                ==
                desired_state
            )

# ====== HLAVNÍ CYKLUS ======

def main_cycle():

    global trvaji_nizke_ceny

    ctl = None

    try:

        df = nacti_ceny()

        pod_limitem, cena = (
            je_cena_pod_limitem(df)
        )

        desired_payload = (
            "1"
            if pod_limitem
            else "0"
        )

        desired_payload_int = int(
            desired_payload
        )

        posledni_stav = (
            nacti_posledni_stav()
        )

        print(
            f"Poslední známý stav: "
            f"{posledni_stav}"
        )

        # -------------------------------------------------
        # Určení, zda začíná nové období nízké ceny
        # -------------------------------------------------

        if (
            posledni_stav == 0
            and desired_payload_int == 1
        ):

            trvaji_nizke_ceny = True

            print(
                "Detekován přechod "
                "OFF → ON."
            )

        elif (
            posledni_stav == 1
            and desired_payload_int == 1
        ):

            trvaji_nizke_ceny = True

            print(
                "Nízké ceny pokračují."
            )

        else:

            trvaji_nizke_ceny = False

        akce_text = (
            "zapnuto"
            if desired_payload == "1"
            else "vypnuto"
        )

        ctl = MqttRelaisController(
            MQTT_BROKER,
            MQTT_PORT,
            MQTT_USER,
            MQTT_PASS,
            MQTT_BASE
        )

        ctl.connect(
            timeout=15
        )

        success = False

        for pokus in range(
            1,
            POKUSY + 1
        ):

            print(
                f"Pokus "
                f"{pokus}/{POKUSY}"
            )

            if ctl.publish_and_wait_confirmation(
                desired_payload,
                CEKANI_SEKUND
            ):

                success = True

                cas = datetime.now(
                    ZoneInfo("Europe/Prague")
                ).strftime("%H:%M")

                if (
                    posledni_stav
                    != desired_payload_int
                ):

                    send_telegram(
                        f"<b>Relé "
                        f"{akce_text}"
                        f"</b> ({cas})."
                    )

                else:

                    print(
                        "Stav beze změny."
                    )

                uloz_posledni_stav(
                    desired_payload_int
                )

                break

        if not success:

            cas = datetime.now(
                ZoneInfo("Europe/Prague")
            ).strftime("%H:%M")

            send_telegram(
                f"<b>Relé nereaguje"
                f"</b> ({cas})."
            )

    except Exception as e:

        print(
            f"Chyba "
            f"[{type(e).__name__}]: "
            f"{e}"
        )

        send_telegram(
            f"Chyba v ovladani_rele.py "
            f"[{type(e).__name__}]: {e}"
        )

    finally:

        if ctl:

            try:
                ctl.disconnect()

            except Exception:
                pass
# ====== ČASOVACÍ FUNKCE ======

def cekej_do_casoveho_bodu(target_dt):

    while True:

        now = datetime.now(
            ZoneInfo("Europe/Prague")
        )

        delta = (
            target_dt - now
        ).total_seconds()

        if delta <= 0:
            break

        if delta > 240:
            time.sleep(30)

        elif delta > 60:
            time.sleep(10)

        else:
            time.sleep(1)

def nejblizsi_ctvrthodina(now=None):

    if now is None:

        now = datetime.now(
            ZoneInfo("Europe/Prague")
        )

    minute = (
        (now.minute // 15) + 1
    ) * 15

    if minute >= 60:

        return (
            now + timedelta(hours=1)
        ).replace(
            minute=0,
            second=0,
            microsecond=0
        )

    return now.replace(
        minute=minute,
        second=0,
        microsecond=0
    )

# ====== START PROGRAMU ======

if __name__ == "__main__":

    now = datetime.now(
        ZoneInfo("Europe/Prague")
    )

    # -------------------------------------------------
    # URČENÍ ZAČÁTKU ŘÍZENÍ
    #
    # Workflow může být spuštěno dříve.
    # První cyklus se má provést:
    #
    # X:55  -> začátek nové hodiny - předstih
    #
    # Pokud už jsme po X:55, cyklus běží ihned.
    # -------------------------------------------------

    zacatek_hodiny = (
        now + timedelta(hours=1)
    ).replace(
        minute=0,
        second=0,
        microsecond=0
    )

    zacatek_predstihu = (
        zacatek_hodiny
        -
        timedelta(
            minutes=PREDSTIH_MINUT
        )
    )

    if now < zacatek_predstihu:

        print(
            "Čekám do začátku předstihu: "
            f"{zacatek_predstihu.strftime('%H:%M:%S')}"
        )

        cekej_do_casoveho_bodu(
            zacatek_predstihu
        )

    else:

        print(
            "Jsme v předstihu nebo nové hodině "
            "– první cyklus spouštím ihned."
        )

    # -------------------------------------------------
    # VÝPOČET POČTU CYKLŮ V AKTUÁLNÍ HODINĚ
    # -------------------------------------------------

    now = datetime.now(
        ZoneInfo("Europe/Prague")
    )

    cycles = (
        4
        -
        (
            now.minute // 15
        )
    )

    for i in range(cycles):

        print(
            f"Spouštím cyklus "
            f"#{i + 1} v "
            f"{datetime.now(ZoneInfo('Europe/Prague')).strftime('%H:%M:%S')}"
        )

        main_cycle()

        if i < cycles - 1:

            next_quarter = (
                nejblizsi_ctvrthodina()
            )

            print(
                "Čekám do další "
                f"čtvrthodiny "
                f"({next_quarter.strftime('%H:%M:%S')})"
            )

            cekej_do_casoveho_bodu(
                next_quarter
            )

    print(
        "Dokončena řízená hodina."
    )
    now = datetime.now(
        ZoneInfo("Europe/Prague")
    )

    # =====================================================
    # DENNÍ PROVOZ 05:00–20:59
    # =====================================================

    if 5 <= now.hour < 21:

        commitni_posledni_stav()

        next_hour = (
            now + timedelta(hours=1)
        ).replace(
            minute=0,
            second=0,
            microsecond=0
        )

        trigger_time = (
            next_hour
            -
            timedelta(
                minutes=REZERVA_START_MINUT
            )
        )

        print(
            f"Čekám do "
            f"{trigger_time.strftime('%H:%M:%S')} "
            "pro spuštění dalšího runu workflow..."
        )

        cekej_do_casoveho_bodu(
            trigger_time
        )

        print(
            "Spouštím další run workflow "
            "pro následující hodinu."
        )

        spustit_dalsi_beh()

    # =====================================================
    # NOČNÍ PAUZA 21:00–04:59
    # =====================================================

    else:

        print(
            "Noční pauza – "
            "řízení relé není aktivní."
        )

        next_hour = (
            now + timedelta(hours=1)
        ).replace(
            minute=0,
            second=0,
            microsecond=0
        )

        trigger_time = (
            next_hour
            -
            timedelta(
                minutes=REZERVA_START_MINUT
            )
        )

        print(
            f"Čekám do "
            f"{trigger_time.strftime('%H:%M:%S')} "
            "pro spuštění dalšího runu..."
        )

        cekej_do_casoveho_bodu(
            trigger_time
        )

        print(
            "Spouštím další run workflow..."
        )

        spustit_dalsi_beh()
