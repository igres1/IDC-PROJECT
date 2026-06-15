import paho.mqtt.client as mqtt
import threading
import urllib.request
import urllib.parse
import time
import json
import os

from telegram.ext import Application
from telegram.ext import CommandHandler
from telegram.ext import MessageHandler
from telegram.ext import filters
from telegram.ext import JobQueue



# ==========================
# CONFIGURACIÓN MQTT Y TELEGRAM
# ==========================

# Por seguridad, el token se lee desde una variable de entorno.
# Si prefieres dejarlo fijo, sustituye la línea siguiente por:
# TELEGRAM_BOT_TOKEN = "TU_TOKEN"
TELEGRAM_BOT_TOKEN = "TOKEN"
chat_id_usuario = None

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883

# La Pico publica el estado completo en este único topic.
TOPIC_STATUS = 'air_control/status'

# Este topic se mantiene porque /settemp debe seguir enviando la consigna a la Pico.
TOPIC_TARGET_TEMP = 'target_temp'

ultima_temperatura = "VOID"
personas = "VOID"
potencia = "VOID"
target_temp = "VOID"
modo = "VOID"

# Watchdog: guarda el último instante en el que llegó el estado general.
ultima_vez_status = time.time()
fallo = False



# ==========================
# WATCHDOG (PERRO GUARDIÁN)
# ==========================

def watchdog_temperatura():
    global fallo
    """Revisa si se han dejado de recibir mensajes del topic air_control/status."""
    global ultima_temperatura, target_temp, personas, potencia, modo
    global ultima_vez_status, chat_id_usuario

    while True:
        time.sleep(1)

        # Si no llega el estado general, dejamos todos los datos en VOID
        if (time.time() - ultima_vez_status) > 6.0 and any(
            valor != "VOID"
            for valor in [ultima_temperatura, personas, potencia, modo]
        ):
            ultima_temperatura = "VOID"
            personas = "VOID"
            potencia = "VOID"
            modo = "VOID"

            if chat_id_usuario is not None:
                texto_alerta = (
                    "⚠️ ¡Alerta! Se han perdido los datos de estado de la Pico "
                    "(6 segundos sin recibir mensajes en air_control/status)."
                )
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

                data = urllib.parse.urlencode({
                    'chat_id': chat_id_usuario,
                    'text': texto_alerta
                }).encode('utf-8')
                fallo = True
                try:
                    urllib.request.urlopen(url, data=data)
                except Exception:
                    pass



# ==========================
# CALLBACKS MQTT
# ==========================

def on_connect(mqttc, userdata, flags, reason_code, properties=None):
    print(f"Connected to {MQTT_BROKER}:{MQTT_PORT}")

    # Ahora el bot solo necesita escuchar el topic de estado completo.
    mqttc.subscribe(TOPIC_STATUS, qos=0)
    print(f"Suscrito al topic: {TOPIC_STATUS}")


def valor_o_void(valor):
    """Devuelve VOID si el dato no existe o llega vacío; si existe, lo convierte a texto."""
    if valor is None or valor == "":
        return "VOID"
    return str(valor)


def on_message(client, userdata, msg):
    global ultima_temperatura, target_temp, personas, potencia, modo, ultima_vez_status, fallo, chat_id_usuario

    payload = msg.payload.decode("utf-8")
    #print(f"Mensaje recibido | Topic: {msg.topic} | Payload: {payload}")

    # Ignoramos cualquier topic que no sea el estado general de la Pico.
    if msg.topic != TOPIC_STATUS:
        return

    try:
        estado = json.loads(payload)
    except json.JSONDecodeError:
        print("Error: el mensaje de air_control/status no es un JSON válido.")
        ultima_temperatura = "VOID"
        personas = "VOID"
        potencia = "VOID"
        modo = "VOID"
        return

    # Si llega un JSON válido del topic genérico, actualizamos el watchdog.
    ultima_vez_status = time.time()

    # La Pico envía estas claves en Prototype2_CORREGIDO.py:
    # actual_temp, target_temp, num_personas, potencia_actual y manual.
    ultima_temperatura = valor_o_void(estado.get("actual_temp"))
    #target_temp = valor_o_void(estado.get("target_temp"))
    personas = valor_o_void(estado.get("num_personas", estado.get("personas")))
    potencia = valor_o_void(estado.get("potencia_actual", estado.get("potencia")))

    # El Prototype2 manda "manual": True/False. Lo convertimos a un modo legible.
    if "modo" in estado:
        modo = valor_o_void(estado.get("modo"))
    elif "manual" in estado:
        if estado.get("manual") is True:
            modo = "MANUAL"
        elif estado.get("manual") is False:
            modo = "AUTOMATICO"
        else:
            modo = "VOID"
    else:
        modo = "VOID"

    '''print(
        "Estado actualizado -> "
        f"Temp: {ultima_temperatura}, "
        f"Target: {target_temp}, "
        f"Personas: {personas}, "
        f"Potencia: {potencia}, "
        f"Modo: {modo}"
    )'''
    
    if fallo == True:
        texto_alerta = (
            "😊 La Pico vuelve a estar online y enviando datos al bot. ¡Todo ha vuelto a la normalidad! "
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        data = urllib.parse.urlencode({
            'chat_id': chat_id_usuario,
            'text': texto_alerta
        }).encode('utf-8')
        fallo = False
        try:
            urllib.request.urlopen(url, data=data)
        except Exception:
            pass


# ==========================
# COMANDOS TELEGRAM
# ==========================

async def start(update, context):
    global chat_id_usuario
    chat_id_usuario = update.effective_chat.id # ID para enviar alertas

    texto = (
    "👋 ¡Hola! Soy el bot de control de temperatura.\n\n"
    "Comandos disponibles:\n"
    "/getdata - Ver la temperatura actual 🌡️\n"
    "/settemp <temperatura> - Establecer temperatura objetivo 🎯\n\n"
    "Ejemplo:\n"
    "/settemp 22.5"
    )

    await context.bot.send_message(
    chat_id=update.effective_chat.id,
    text=texto
    )


async def getdata(update, context):
    if ultima_temperatura == "VOID" and False:
        texto = "⚠️ Todavía no he recibido ninguna temperatura de la Pico."
    else:
        texto = f"🌡️ Temperatura actual: {ultima_temperatura}°C\n 🎯 Objetivo de temperatura: {target_temp}°C\n👥 Número de personas: {personas} \n⚡ Potencia: {potencia}\n⚙️ Modo: {modo}"

    await context.bot.send_message(
    chat_id=update.effective_chat.id,
    text=texto
    )


async def settemp(update, context):
    global target_temp
    if len(context.args) != 1:
        await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="❌ Uso incorrecto.\n\nEjemplo correcto:\n/settemp 22.5"
        )
        return

    try:
        target_temp = round(float(context.args[0]), 1)
    except ValueError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ La temperatura debe ser un número.\n\nEjemplo:\n/settemp 22.5"
        )
        return

    # Publicamos la temperatura objetivo al broker
    result = client.publish(
        TOPIC_TARGET_TEMP,
        str(target_temp),
        qos=0,
        retain=True
        )

    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        texto = (
        f"✅ Temperatura objetivo enviada correctamente.\n"
        f"🎯 Nueva temperatura objetivo: {target_temp} °C"
        )
    else:
        texto = "❌ Error al publicar la temperatura objetivo en MQTT."

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=texto
    )
    

async def unknown(update, context):
    texto = (
        "💬 No entiendo este comando 😕\n\n"
        "Usa uno de estos comandos:\n"
        "/start - Iniciar sistema de control\n"
        "/getdata - Ver configuración actual\n"
        "/settemp <temperatura> - Cambiar temperatura objetivo\n\n"
        "Ejemplo:\n"
        "/settemp 23"
    )    

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=texto
    )



# ==========================
# INICIO DE SERVICIOS
# ==========================

if __name__ == '__main__':
    # 1. Iniciar el hilo del Watchdog en segundo plano (daemon=True hace que se cierre si cerramos el programa)
    hilo_watchdog = threading.Thread(target=watchdog_temperatura, daemon=True)
    hilo_watchdog.start()

    # 2. Iniciar MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, port=MQTT_PORT, keepalive=60)
    client.loop_start()

    # 3. Iniciar Telegram
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).job_queue(JobQueue()).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("getdata", getdata))
    application.add_handler(CommandHandler("settemp", settemp))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), unknown))

    #print("Bot iniciado... (Recuerda escribir /start en tu chat de Telegram)")
    application.run_polling()
