import os
import time
import paho.mqtt.client as mqtt

MQTT_BROKER = os.environ["MQTT_BROKER"]
MQTT_USER   = os.environ["MQTT_USER"]
MQTT_PASS   = os.environ["MQTT_PASS"]

TOPIC_GET = "starymuz@centrum.cz/rele/1/get"
TOPIC_SET = "starymuz@centrum.cz/rele/1/set"

state = None  # "0", "1" nebo None = neznámý

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe(TOPIC_GET)
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

# krátké čekání na odpověď GET (nové zprávy)
for _ in range(20):
    if state is not None:
        break
    time.sleep(0.1)

# ====== VYHODNOCENÍ STAVU ======
if state == "1":
    print("Relé je ZAPNUTO → vypínám")
    client.publish(TOPIC_SET, "0", qos=1, retain=False)

elif state == "0":
    print("Relé je vypnuto – žádná akce")

else:
    print("Stav relé NEZNÁMÝ – žádná akce")

client.loop_stop()
client.disconnect()
