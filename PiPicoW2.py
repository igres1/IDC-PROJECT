import network
import time
import machine
from machine import Pin, ADC
from umqtt.simple import MQTTClient
import ubinascii

try:
    import ujson as json
except ImportError:
    import json
# ==========================================
# 1. CONFIGURACIÓN INICIAL (WI-FI Y MQTT)
# ==========================================
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
#wlan.connect('vodafoneBA1603_EXT', 'KCGSAUQCLM55DJJM')
wlan.connect('WIFI', 'WIFIPASSWORD')

# CONFIG MQTT
mqtt_server = 'broker.hivemq.com'
# Generar un Client ID único basado en el hardware de tu Pico W
client_id = ubinascii.hexlify(machine.unique_id())

topic_camara_tx = b'sscma/v0/xiao_esp32_air_control_1/tx'
topic_camara_rx = b'sscma/v0/xiao_esp32_air_control_1/rx'

topic_personas = topic_camara_tx
topic_target_temp = b'target_temp'
#topic_actual_temp = b'actual_temp'
topic_estado = b'air_control/status'


COMANDO_INICIAR_CAMARA = b'AT+INVOKE=-1,0,0\n'

client = None

# PINES DE LEDS
led_pins = [
    Pin(10, Pin.OUT),
    Pin(11, Pin.OUT),
    Pin(12, Pin.OUT),
    Pin(13, Pin.OUT),
    Pin(14, Pin.OUT),
    Pin(15, Pin.OUT),
    Pin(16, Pin.OUT),
    Pin(17, Pin.OUT),
    Pin(18, Pin.OUT),
    Pin(19, Pin.OUT)
]

# VARIABLES DE ESTADO GLOBALES
num_personas = 0
target_temp = 22
actual_temp = None
potencia_actual = 1
manual = False
power = False
numero_actual = -1
controlLock = False
estado_pendiente = True
CameraCounter = None

# SENSOR DE TEMPERATURA INTERNO
conversion_factor = 3.3 / (65535) 
sensor_temp = machine.ADC(machine.ADC.CORE_TEMP)

# CONFIGURACIÓN LÓGICA CLIMATIZADOR
INTERVALO_LECTURA_TEMP = 5
POTENCIA_MAXIMA = 10
FACTOR_TEMPERATURA = 2.0
FACTOR_PERSONAS = 0.5 # Valor tomado par ver funcionamiento con pocas  personas
MARGEN_TEMPERATURA = 0.3

# ==========================================
# 2. LÓGICA DE ACCIONES Y MANDO MANUAL
# ==========================================
MAPA_BOTONES = {
    3141861120: "Test",
    3910598400: "0",
    4077715200: "1",
    3877175040: "2",
    2707357440: "3",
    4144561920: "4",
    3810328320: "5",
    2774204160: "6",
    3175284480: "7",
    2907897600: "8",
    3041591040: "9",
    3927310080: "Resume",
    3125149440: "Power",
    4127850240: "Min",
    4161273600: "Max",
    3208707840: "Add",
    3860463360: "Subs",
}

def setPower(num):
    global potencia_actual, estado_pendiente
    print(f"Variable 'numero_actual' actualizada a: {num}")
    for i in range(len(led_pins)):
        if i <= num:
            led_pins[i].on()
        else:
            led_pins[i].off()
    potencia_actual = num
    estado_pendiente = True
    
def boton_presionado(codigo):
    global numero_actual, manual, power
    
    if codigo == "REPETIR" or controlLock:
        return
    
    try:
        codigo = int(codigo)
    except ValueError:
        pass 
        
    nombre_boton = MAPA_BOTONES.get(codigo, None)
    
    if nombre_boton is None:
        print(f"Codigo desconocido recibido: {codigo}")
        return

    if nombre_boton == "Test" and not controlLock:
        print("Accion de TEST iniciada... MODO MANUAL ACTIVADO")
        manual = True
        numero_actual = potencia_actual
        
    elif not manual:
        return
        
    elif nombre_boton == "Power":
        print(f"Accion de Power iniciada...\n Power:{power}")
        numero_actual = -1
        setPower(numero_actual)
        
    elif nombre_boton == "Resume":
        print("Accion de RESUME iniciada... VOLVIENDO A MODO AUTOMATICO")
        manual = False
        aplicar_control()
        
    elif nombre_boton == "Max":
        numero_actual = 9
        setPower(numero_actual)
        
    elif nombre_boton == "Min":
        numero_actual = 0
        setPower(numero_actual)
        
    elif nombre_boton == "Add" and numero_actual != 9:
        numero_actual += 1
        print(numero_actual)
        setPower(numero_actual)
        
    elif nombre_boton == "Subs" and numero_actual != -1:
        numero_actual -= 1
        print(numero_actual)
        setPower(numero_actual)
        
    elif nombre_boton.isdigit():
        numero_actual = int(nombre_boton)
        setPower(numero_actual)

# ==========================================
# 3. LECTOR INFRARROJO (Protocolo NEC)
# ==========================================
class LectorIR_NEC:
    def __init__(self, pin_num, callback):
        self.pin = machine.Pin(pin_num, machine.Pin.IN)
        self.callback = callback
        self.last_tick = time.ticks_us()
        self.value = 0
        self.bits = 0
        self.pin.irq(trigger=machine.Pin.IRQ_FALLING, handler=self._irq_handler)

    def _irq_handler(self, pin):
        t = time.ticks_us()
        dt = time.ticks_diff(t, self.last_tick)
        self.last_tick = t

        if 13000 < dt < 14000:
            self.value = 0
            self.bits = 0
        elif 2000 < dt < 2500:
            self.value |= (1 << self.bits)
            self.bits += 1
        elif 1000 < dt < 1500:
            self.bits += 1
        elif 11000 < dt < 12000:
            self.callback("REPETIR")
            return

        if self.bits == 32:
            self.callback(self.value)
            self.bits = 0

# ==========================================
# 4. LÓGICA DEL SISTEMA AUTOMÁTICO
# ==========================================
def leer_actual_temp():
    reading = sensor_temp.read_u16() * conversion_factor
    temperatura = 27 - (reading - 0.706)/0.001721 
    return temperatura

def calcular_potencia(num_personas, actual_temp, target_temp):
    if actual_temp is None:
        return 0
    if target_temp is None:
        return 0
    if num_personas <= 0:
        return 0

    diferencia = actual_temp - target_temp
    

    if diferencia <=  MARGEN_TEMPERATURA:
        return 0
        
    potencia = diferencia * FACTOR_TEMPERATURA
    potencia += num_personas * FACTOR_PERSONAS
    potencia = round(potencia)

    if potencia < 0:
        potencia = 0
    if potencia > POTENCIA_MAXIMA:
        potencia = POTENCIA_MAXIMA

    return potencia

def actualizar_leds(potencia):
    for i in range(len(led_pins)):
        if i < potencia:
            led_pins[i].on()
        else:
            led_pins[i].off()

def publicar_estado():
    global client, actual_temp, CameraCounter

    if client is None:
        return

    estado = {
        "actual_temp": round(actual_temp, 2) if actual_temp is not None else "VOID",
        "target_temp": target_temp if target_temp is not None else "VOID",
        "num_personas": num_personas,
        "potencia_actual": potencia_actual,
        "manual": manual,
        "CameraCounter": CameraCounter
    }

    client.publish(topic_estado, json.dumps(estado).encode('utf-8'), retain=False)


def aplicar_control():
    global actual_temp, potencia_actual, client, controlLock

    # En modo manual no aplicamos la lógica de temperatura
    if manual:
        publicar_estado()
        return
    
    controlLock = True
    try:
        temp_leida = leer_actual_temp()
        actual_temp = temp_leida

        personas_actuales = num_personas
        objetivo_actual = target_temp

        nueva_potencia = calcular_potencia(personas_actuales, temp_leida, objetivo_actual)

        if nueva_potencia != potencia_actual:
            potencia_actual = nueva_potencia
            actualizar_leds(potencia_actual)
            print("Nueva potencia aplicada:", potencia_actual)

        publicar_estado()
    finally:
        controlLock = False


def obtener_num_personas(payload):
    global CameraCounter
    """
    Recibe el JSON de la camara como texto y devuelve el num de personas detectadas y el contador de la camara.
    
    Retorna:
    - int: número de personas detectadas
    - Retorna None si no se puede extraer (sin pisar num_personas global)
    - Lanza ValueError solo si hay un error crítico
    """

    json_data = json.loads(payload)

    # Caso 1: si existe una lista de boxes dentro de data
    data_section = json_data.get("data", {})
    
    if "count"  in data_section:
        CameraCounter = data_section["count"]
        
    
    if "boxes" in data_section:
        boxes_list = data_section["boxes"]
        if isinstance(boxes_list, list):
            return len(boxes_list)

    # Caso 2: si la camara manda el numero directamente en data
    posibles_claves = [
        "personas",
        "num_personas",
        "people",
        "people_count",
        "person_count",
        "total"
    ]

    for clave in posibles_claves:
        if clave in data_section:
            try:
                return int(data_section[clave])
            except (ValueError, TypeError):
                pass

    # Caso 3: si la camara manda el numero directamente en raíz del JSON
    for clave in posibles_claves:
        if clave in json_data:
            try:
                return int(json_data[clave])
            except (ValueError, TypeError):
                pass

    # Si no encontramos datos, retorna None (no lanzar excepción)
    print("Advertencia: No se encontró el número de personas en el JSON")
    return None


    
def sub_cb(topic, msg):
    global num_personas, target_temp, client

    try:
        if topic == topic_personas:
            payload = msg.decode('utf-8')

            nuevo_num_personas = obtener_num_personas(payload)
            
            # Solo actualizar si obtuvimos un número válido
            if nuevo_num_personas is not None:
                num_personas = nuevo_num_personas
            else:
                print("No se pudo extraer número de personas del JSON, manteniendo valor anterior:", num_personas)

                
        elif topic == topic_target_temp:
            target_temp = float(msg.decode('utf-8'))
            print("Temperatura objetivo actualizada:", target_temp)
        else:
            print("Topic desconocido:", topic)
            return

        aplicar_control()
        
    except ValueError as e:
        print(f"Error: el mensaje recibido no tiene un formato válido - {e}")
    except Exception as e:
        print(f"Error procesando el mensaje: {e}")


def iniciar_camara():
    global client

    if client is not None:
        print("Enviando comando de inicio a la camara...")
        client.publish(topic_camara_rx, COMANDO_INICIAR_CAMARA)
        print("Comando enviado:", COMANDO_INICIAR_CAMARA)

        
def mqtt_connect():
    client = MQTTClient(client_id, mqtt_server, keepalive=60)
    client.set_callback(sub_cb)
    client.connect()
    print('Connected to %s MQTT Broker' % (mqtt_server))
    
    client.subscribe(topic_personas)
    print("Suscrito a:", topic_personas)
    
    client.subscribe(topic_target_temp)
    print("Suscrito a:", topic_target_temp)
    
    
    
    return client

def reconnect():
    print('Failed to connect to MQTT Broker. Reconnecting...')
    time.sleep(5)
    machine.reset()

# ==========================================
# 5. INICIO DE EJECUCIÓN
# ==========================================
# Esperar a conexión Wi-Fi
max_wait = 10
while max_wait > 0:
    if wlan.status() < 0 or wlan.status() >= 3:
        break
    max_wait -= 1
    print('waiting for connection...')
    time.sleep(1)

if wlan.status() != 3:
    raise RuntimeError('network connection failed')
else:
    print('connected to Wi-Fi')
    status = wlan.ifconfig()
    print('ip = ' + status[0])

# Conectar a MQTT
try:
    client = mqtt_connect()
    iniciar_camara()
    print("Comando enviado para iniciar la camara")
except OSError:
    reconnect()

aplicar_control()
ultimo_tiempo_temp = time.time()

# Arrancar el receptor IR (la IRQ se encargará de gestionar el hardware)
print("Iniciando receptor IR en Pin 28...")
receptor = LectorIR_NEC(28, boton_presionado)

print("--- SISTEMA INICIADO Y LISTO ---")

# BUCLE PRINCIPAL
while True:
    try:
        # Siempre revisamos mensajes MQTT, también en modo manual
        client.check_msg()

        tiempo_actual = time.time()

        if tiempo_actual - ultimo_tiempo_temp >= INTERVALO_LECTURA_TEMP:
            temp_leida = leer_actual_temp()
            actual_temp = temp_leida

            if not manual:
                aplicar_control()
            else:
                publicar_estado()

            ultimo_tiempo_temp = tiempo_actual

    except OSError:
        reconnect()

    time.sleep(1)
