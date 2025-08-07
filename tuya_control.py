import os
import requests
from tuyapy2 import TuyaApi
from datetime import datetime

# Přístupové údaje z GitHub secrets
API_KEY = os.getenv("TUYA_ACCESS_ID")
API_SECRET = os.getenv("TUYA_ACCESS_SECRET")
EMAIL = os.getenv("TUYA_EMAIL")
PASSWORD = os.getenv("TUYA_PASSWORD")
DEVICE_NAME = os.getenv("DEVICE_NAME")
SPOT_LIMIT = 0.05  # upravte dle potřeby

def ziskej_spot_cenu():
    # ZDE vložte váš zdroj SPOT cen
    r = requests.get("https://api.moje-cena.cz/spot-ceny")
    data = r.json()
    hodina = datetime.now().hour
    return float(data["ceny"][hodina])

def rid_rele():
    api = TuyaApi()
    api.init(API_KEY, API_SECRET)
    api.login(EMAIL, PASSWORD)

    device = next(d for d in api.get_all_devices() if DEVICE_NAME.lower() in d.name().lower())

    cena = ziskej_spot_cenu()
    print(f"SPOT cena: {cena:.3f} Kč/kWh")

    if cena < SPOT_LIMIT:
        print("Nízká cena – zapínám relé")
        device.turn_on()
    else:
        print("Vysoká cena – vypínám relé")
        device.turn_off()

if __name__ == "__main__":
    rid_rele()
