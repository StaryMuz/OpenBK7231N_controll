import os
import time
import paho.mqtt.client as mqtt

MQTT_BROKER = os.environ["MQTT_BROKER"]
MQTT_USER   = os.environ["MQTT_USER"]
MQTT_PASS   = os.environ["MQTT_PASS"]

TOPIC_GET = "starymuz@centrum.cz/rele/2/get"
TOPIC_SET = "starymuz@centrum.cz/rele/2/set"

state = None  # "0", "1" nebo None = neznámý

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        # přihlásíme se k TOPIC_GET
        client.subscribe(TOPIC_GET)
        # po přihlášení rovnou požádáme relé o aktuální stav
        client.publish(TOPIC_GET, "", qos=1, retain=False)
    else:
        print("MQTT connect error:", reason_code)

def on_message(client, userdata, msg):
    global state
    # ignorovat retained zprávy
    if msg.retain:
        return
    payload = msg.payload.decode(errors="ignore").strip()
    if payload in ("0", "1"):
        state = payload

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2
)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_BROKER, 1883, 30)
client.loop_start()

# ------------------------------
# Čekání na odpověď od zařízení s timeoutem
# ------------------------------
max_wait = 70   # max 70 sekund
interval = 0.5  # polling interval
waited = 0

while state is None and waited < max_wait:
    time.sleep(interval)
    waited += interval

client.loop_stop()
client.disconnect()

# ====== VYHODNOCENÍ STAVU ======
if state == "1":
    print("Relé je ZAPNUTO → vypínám")
    client.connect(MQTT_BROKER, 1883, 30)
    client.loop_start()
    client.publish(TOPIC_SET, "0", qos=1, retain=False)
    client.loop_stop()
    client.disconnect()

elif state == "0":
    print("Relé je vypnuto – žádná akce")

else:
    print(f"Stav relé NEZNÁMÝ – žádná odpověď po {max_wait}s")
