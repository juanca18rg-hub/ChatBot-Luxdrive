"""Cliente del chatbot de LuxDrive Taller.

Groq interpreta la frase de la persona y devuelve un plan. FastAPI valida y
ejecuta las operaciones. Este archivo contiene las dos conexiones.
"""

import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import date
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_BASE = os.getenv("CHATBOT_API_URL", "http://127.0.0.1:8011").rstrip("/")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

RUTAS = {
    "negocio": "/api/negocio",
    "servicios": "/api/servicios",
    "disponibilidad": "/api/disponibilidad",
    "reservas": "/api/reservas",
    "reserva": "/api/reservas/{codigo}",
}

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ACCIONES_ESCRITURA = ("crear", "modificar", "cancelar")


# ---------------------------------------------------------------- HTTP básico

def llamar_api(metodo, ruta, cuerpo=None, parametros=None):
    """Hace una petición a FastAPI y devuelve el JSON de la respuesta."""
    url = API_BASE + ruta
    if parametros:
        url += "?" + urlencode(parametros)
    datos = None
    cabeceras = {}
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    peticion = Request(url, data=datos, headers=cabeceras, method=metodo)
    with urlopen(peticion, timeout=15) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def detalle_http(error):
    """Extrae el mensaje de un HTTPError de FastAPI o de Groq."""
    try:
        cuerpo = json.loads(error.read().decode("utf-8"))
    except (ValueError, OSError):
        return "sin detalle"
    if not isinstance(cuerpo, dict):
        return "sin detalle"
    if isinstance(cuerpo.get("error"), dict):  # formato de Groq
        return str(cuerpo["error"].get("message", "sin detalle"))
    detalle = cuerpo.get("detail", "sin detalle")
    if isinstance(detalle, list):  # validación de FastAPI
        return "; ".join(str(item.get("msg", item)) for item in detalle)
    return str(detalle)


# ----------------------------------------------------------------------- Groq

def leer_plan(texto):
    """Convierte el texto del modelo en un diccionario."""
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("La respuesta del modelo no contiene un JSON")
    plan = json.loads(texto[inicio:fin + 1])
    if not isinstance(plan, dict):
        raise ValueError("El plan del modelo debe ser un objeto JSON")
    return plan


def consultar_groq(mensajes, clave, modelo):
    """Envía los mensajes a Groq y devuelve el plan que interpretó."""
    payload = {
        "model": modelo,
        "messages": mensajes,
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    peticion = Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {clave}",
            "Content-Type": "application/json",
            "User-Agent": "LuxDriveChatBot/1.0",
        },
        method="POST",
    )
    with urlopen(peticion, timeout=30) as respuesta:
        cuerpo = json.loads(respuesta.read().decode("utf-8"))
    return leer_plan(cuerpo["choices"][0]["message"]["content"])


# ------------------------------------------------------------ textos y formato

def dia_texto(fecha):
    """'2026-10-19' -> 'lunes 2026-10-19'."""
    try:
        return f"{DIAS[date.fromisoformat(fecha).weekday()]} {fecha}"
    except ValueError:
        return fecha


def sumar_minutos(hora, minutos):
    total = int(hora[:2]) * 60 + int(hora[3:]) + minutos
    return f"{total // 60:02d}:{total % 60:02d}"


def dinero(valor):
    if not isinstance(valor, (int, float)):
        return "precio por consultar"
    if valor == 0:
        return "sin costo"
    return "$" + f"{int(valor):,}".replace(",", ".") + " COP"


def nombre_servicio(servicios, identificador):
    return servicios.get(identificador, {}).get("nombre", identificador)


def texto_reserva(reserva, servicios):
    nombre = nombre_servicio(servicios, reserva["servicio"])
    fin = sumar_minutos(reserva["hora"], reserva["duracion_minutos"])
    return "\n".join([
        f"**Código {reserva['codigo']}** ({reserva['estado']})",
        "",
        f"- Cliente: {reserva['cliente']}",
        f"- Servicio: {nombre} ({reserva['duracion_minutos']} min)",
        f"- Fecha: {dia_texto(reserva['fecha'])}",
        f"- Hora: {reserva['hora']} a {fin}",
    ])


def formatear(accion, datos, servicios):
    """Convierte la respuesta de FastAPI en un texto para la conversación."""
    if accion == "servicios":
        lineas = [
            f"- **{datos[clave].get('nombre', clave)}** (`{clave}`): "
            f"{datos[clave].get('duracion_minutos')} min · {dinero(datos[clave].get('precio_cop'))}"
            for clave in datos
        ]
        return "Estos son nuestros servicios:\n\n" + "\n".join(lineas)

    if accion == "disponibilidad":
        nombre = nombre_servicio(servicios, datos["servicio"])
        dia = dia_texto(datos["fecha"])
        if not datos["horas"]:
            return f"No hay horas libres para **{nombre}** el {dia}."
        return (
            f"Horas disponibles para **{nombre}** ({datos['duracion_minutos']} min) "
            f"el {dia}:\n\n{', '.join(datos['horas'])}\n\n"
            "Son horas de inicio. Dime cuál prefieres y la reservamos."
        )

    if accion == "listar":
        if not datos:
            return "No encontré reservas con ese nombre."
        textos = [texto_reserva(reserva, servicios) for reserva in datos]
        return f"Encontré {len(datos)} reserva(s):\n\n" + "\n\n".join(textos)

    titulos = {
        "consultar": "Esta es la reserva:",
        "crear": "Reserva creada. Guarda el código para consultarla, modificarla o cancelarla.",
        "modificar": "Reserva modificada.",
        "cancelar": "Reserva cancelada. El horario quedó libre.",
    }
    return f"{titulos[accion]}\n\n{texto_reserva(datos, servicios)}"


def catalogo():
    """Servicios para dar nombre a los resultados. Si falla, se muestran los id."""
    try:
        return llamar_api("GET", RUTAS["servicios"])
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ operaciones

def coincide_nombre(buscado, cliente):
    """Búsqueda tolerante a acentos, nombre parcial y errores pequeños."""
    normalizar = lambda valor: re.sub(
        r"[^a-z0-9]+", " ",
        "".join(c for c in unicodedata.normalize("NFKD", str(valor or "").lower())
                if not unicodedata.combining(c))
    ).strip()
    consulta, nombre = normalizar(buscado), normalizar(cliente)
    if not consulta or not nombre:
        return False
    if consulta in nombre or all(p in nombre.split() for p in consulta.split()):
        return True
    palabras_q, palabras_n = set(consulta.split()), set(nombre.split())
    cobertura = len(palabras_q & palabras_n) / max(1, len(palabras_q))
    return cobertura >= 0.6 or SequenceMatcher(None, consulta, nombre).ratio() >= 0.72


def ejecutar(plan, confirmar=False):
    """Ejecuta un plan ya validado. Crear, modificar y cancelar exigen confirmar=True."""
    accion = plan["accion"]
    if accion in ACCIONES_ESCRITURA and not confirmar:
        raise ValueError("Esta operación necesita confirmación")
    ruta_reserva = RUTAS["reserva"].format(codigo=quote(plan["codigo"], safe=""))

    if accion == "servicios":
        datos = llamar_api("GET", RUTAS["servicios"])
    elif accion == "disponibilidad":
        datos = llamar_api(
            "GET", RUTAS["disponibilidad"],
            parametros={"fecha": plan["fecha"], "servicio": plan["servicio"]},
        )
    elif accion == "listar":
        todas = llamar_api("GET", RUTAS["reservas"])
        datos = [r for r in todas if coincide_nombre(plan["cliente"], r["cliente"])]
    elif accion == "consultar":
        datos = llamar_api("GET", ruta_reserva)
    elif accion == "crear":
        datos = llamar_api("POST", RUTAS["reservas"], cuerpo={
            "cliente": plan["cliente"],
            "servicio": plan["servicio"],
            "fecha": plan["fecha"],
            "hora": plan["hora"],
            "confirmada": True,
        })
    elif accion == "modificar":
        datos = llamar_api("PUT", ruta_reserva, cuerpo={
            "servicio": plan["servicio"],
            "fecha": plan["fecha"],
            "hora": plan["hora"],
        })
    elif accion == "cancelar":
        datos = llamar_api("DELETE", ruta_reserva)
    else:
        raise ValueError("Acción no permitida")

    # La operación ya se hizo: si el catálogo falla, solo se muestran los id.
    servicios = datos if accion == "servicios" else catalogo()
    return {
        "tipo": "resultado",
        "accion": accion,
        "datos": datos,
        "mensaje": formatear(accion, datos, servicios),
    }
