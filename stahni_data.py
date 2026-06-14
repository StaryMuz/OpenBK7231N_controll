# -*- coding: utf-8 -*-

import requests
import pandas as pd
import os
import time
import io

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt

from zoneinfo import ZoneInfo
from datetime import datetime

# ====== KONFIGURAČNÍ PROMĚNNÉ ======

LIMIT_EUR = float(
    os.getenv(
        "LIMIT_EUR",
        "13.0"
    )
)

dnes = datetime.now(
    ZoneInfo("Europe/Prague")
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)

HTTP_TIMEOUT = 30

# ====== FUNKCE ======

def ziskej_data_z_ote(
    max_pokusu=5,
    cekani=300
):
    """
    Stáhne dnešní SPOT ceny z OTE.
    Opakuje při neúspěchu.
    """

    den = dnes.strftime("%d")
    mesic = dnes.strftime("%m")
    rok = dnes.strftime("%Y")

    url = (
        f"https://www.ote-cr.cz/pubweb/"
        f"attachments/01/{rok}/"
        f"month{mesic}/"
        f"day{den}/"
        f"DT_15MIN_{den}_{mesic}_{rok}_CZ.xlsx"
    )

    for pokus in range(
        1,
        max_pokusu + 1
    ):

        try:

            print(
                f"⬇️ Pokus {pokus}: "
                f"stahuji data z {url}"
            )

            df = pd.read_excel(
                url,
                skiprows=22,
                usecols="A,C",
                engine="openpyxl",
                storage_options=None
            )

            df.columns = [
                "Ctvrthodina",
                "Cena (EUR/MWh)"
            ]

            df.dropna(inplace=True)

            df["Ctvrthodina"] = (
                pd.to_numeric(
                    df["Ctvrthodina"],
                    errors="coerce"
                )
                .fillna(0)
                .astype(int)
            )

            df["Cena (EUR/MWh)"] = (
                pd.to_numeric(
                    df["Cena (EUR/MWh)"]
                    .astype(str)
                    .str.replace(",", "."),
                    errors="coerce"
                )
            )

            df = df[
                df["Ctvrthodina"] >= 1
            ]

            return df

        except Exception as e:

            print(
                f"⚠️ Chyba: {e}"
            )

            if pokus < max_pokusu:

                print(
                    f"⏳ Čekám "
                    f"{cekani}s "
                    f"před dalším pokusem…"
                )

                time.sleep(cekani)

    raise Exception(
        "❌ Nepodařilo se "
        "stáhnout data z OTE."
    )


def uloz_csv(
    df,
    soubor="ceny_ote.csv"
):

    tmp = soubor + ".tmp"

    df.to_csv(
        tmp,
        index=False
    )

    os.replace(
        tmp,
        soubor
    )

    print(
        f"💾 Data uložena do "
        f"{soubor}"
    )


def vytvor_graf(df):

    fig, ax = plt.subplots(
        figsize=(8, 4)
    )

    ax.plot(
        df["Ctvrthodina"],
        df["Cena (EUR/MWh)"],
        marker="o"
    )

    ax.axhline(
        LIMIT_EUR,
        linestyle="--",
        label=(
            f"Limit "
            f"{LIMIT_EUR} EUR/MWh"
        )
    )

    ax.set_xlabel(
        "Čtvrthodina"
    )

    ax.set_ylabel(
        "Cena (EUR/MWh)"
    )

    ax.set_title(
        f"Ceny elektřiny "
        f"{dnes.strftime('%d.%m.%Y')}"
    )

    ax.grid(True)

    ax.legend()

    buf = io.BytesIO()

    plt.tight_layout()

    plt.savefig(
        buf,
        format="png"
    )

    buf.seek(0)

    plt.close(fig)

    return buf
