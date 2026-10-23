"""Conversación con historial del chatbot de LuxDrive Taller.

Aquí viven los mensajes que se envían a Groq, la revisión del plan que devuelve
y la decisión de qué hacer: preguntar un dato, consultar, pedir confirmación o
rechazar. Nada se guarda sin que la persona confirme.
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import quote

from consulta_llm import (
    ACCIONES_ESCRITURA, DIAS, RUTAS, consultar_groq, detalle_http, dia_texto,
    dinero, ejecutar, llamar_api, nombre_servicio, sumar_minutos, texto_reserva,
)

CAMPOS = ("codigo", "cliente", "servicio", "fecha", "hora")
REQUERIDOS = {
    "servicios": [],
    "disponibilidad": ["servicio", "fecha"],
    "consultar": ["codigo"],
    "listar": ["cliente"],
    "crear": ["cliente", "servicio", "fecha", "hora"],
    "modificar": ["codigo"],
    "cancelar": ["codigo"],
    "conversar": [],
}
SINONIMOS = {
    "reservar": "crear", "agendar": "crear", "cambiar": "modificar",
    "editar": "modificar", "eliminar": "cancelar", "borrar": "cancelar",
    "ver": "consultar",
}
NOMBRES_CAMPO = {
    "codigo": "el código de la reserva", "cliente": "tu nombre",
    "servicio": "el servicio", "fecha": "la fecha", "hora": "la hora",
}
VERBOS = {
    "disponibilidad": "consultar la disponibilidad", "consultar": "consultar la reserva",
    "listar": "buscar tus reservas", "crear": "reservar",
    "modificar": "modificar la reserva", "cancelar": "cancelar la reserva",
}
MAX_MENSAJES = 30  # mensajes anteriores (sin contar system) que se reenvían a Groq


# ------------------------------------------------------------------ instrucciones

def calendario(hoy):
    etiquetas = {0: " (hoy)", 1: " (mañana)", 2: " (pasado mañana)"}
    lineas = []
    for i in range(15):
        dia = hoy + timedelta(days=i)
        lineas.append(f"- {DIAS[dia.weekday()]} {dia.isoformat()}{etiquetas.get(i, '')}")
    return "\n".join(lineas)


def instrucciones(negocio):
    """Mensaje system: catálogo, horario, fecha de hoy y formato de respuesta."""
    nombre = "LuxDrive Taller"
    servicios_texto = "(catálogo no disponible)"
    horario_texto = ""
    if negocio:
        nombre = negocio.get("nombre", nombre)
        servicios_texto = "\n".join(
            f"- {clave}: {datos.get('nombre', clave)}, {datos.get('duracion_minutos')} min, "
            f"{dinero(datos.get('precio_cop'))}"
            for clave, datos in negocio["servicios"].items()
        )
        horario = negocio["horario"]
        dias = ", ".join(DIAS[numero] for numero in horario["dias"])
        horario_texto = (
            f"HORARIO: {dias}, de {horario['apertura']} a {horario['cierre']}. "
            f"Las horas de inicio van de {horario['paso']} en {horario['paso']} minutos.\n\n"
        )
    hoy = date.today()
    return (
        f"Eres el asistente virtual de {nombre}, un taller automotriz. "
        "Atiendes por chat y gestionas las citas de servicio de los vehículos.\n\n"
        f"SERVICIOS (id: nombre, duración, precio):\n{servicios_texto}\n\n"
        f"{horario_texto}"
        f"FECHAS: hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()}. Próximos días:\n"
        f"{calendario(hoy)}\n\n"
        "RESPUESTA: devuelve únicamente un objeto JSON con estas claves, todas de texto:\n"
        '{"accion": "", "codigo": "", "cliente": "", "servicio": "", "fecha": "", '
        '"hora": "", "respuesta": ""}\n\n'
        'ACCIONES (valor de "accion"):\n'
        "- servicios: pregunta qué servicios hay, sus precios o su duración.\n"
        "- disponibilidad: pregunta qué horas hay libres. Usa servicio y fecha.\n"
        "- consultar: quiere ver una reserva. Usa codigo.\n"
        "- listar: quiere ver sus reservas y no da el código. Usa cliente.\n"
        "- crear: quiere reservar. Usa cliente, servicio, fecha y hora.\n"
        "- modificar: quiere cambiar una reserva. Usa codigo y solo los datos que cambian "
        "(servicio, fecha u hora).\n"
        "- cancelar: quiere cancelar o eliminar una reserva. Usa codigo; si no lo conoce, extrae el nombre del cliente para buscarla.\n"
        '- conversar: saludo, agradecimiento o duda general. Escribe una respuesta breve en "respuesta".\n\n'
        "REGLAS:\n"
        "1. Responde solo con el JSON. Nunca uses las claves plan, resultado_api, rechazo ni "
        "mensaje: aparecen en los mensajes anteriores del asistente y son el registro de lo "
        "que hizo el sistema.\n"
        "2. Usa los mensajes anteriores para completar lo que la persona ya dijo (nombre, "
        "servicio, fecha, hora y código). Si empieza una solicitud distinta, no arrastres "
        "datos de la anterior.\n"
        '3. Lo que no se haya dicho se deja como "". Nunca inventes nombres, códigos, fechas ni horas.\n'
        "4. fecha en formato AAAA-MM-DD. Usa el calendario de arriba para «mañana», «el viernes», "
        "etc. Si dan día y mes sin año, usa el año de hoy, o el siguiente si esa fecha ya pasó. "
        'hora en formato HH:MM de 24 horas («3 de la tarde» es "15:00").\n'
        "5. servicio debe ser exactamente uno de los id de la lista.\n"
        "6. Para cancelar o cambiar una reserva, busca su código en resultado_api de los mensajes "
        "anteriores. Si no lo conoce, extrae el nombre del cliente para buscar sus reservas activas; "
        'no inventes un código.'
    )


def nuevo_historial():
    """Conversación nueva: solo el mensaje system."""
    try:
        negocio = llamar_api("GET", RUTAS["negocio"])
    except (OSError, ValueError):
        negocio = None  # si la API está apagada, el primer turno vuelve a cargarlo
    return [{"role": "system", "content": instrucciones(negocio)}]


def registro(plan, **extra):
    """Texto que se guarda como mensaje assistant: el plan y lo que ocurrió con él."""
    return json.dumps({"plan": plan, **extra}, ensure_ascii=False)


# ------------------------------------------------------------- revisar el plan

def _normalizar_texto(valor):
    """Normaliza acentos, mayúsculas y puntuación para admitir lenguaje informal."""
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def buscar_servicio(valor, servicios):
    """Acepta id, nombre completo o descripción razonablemente parecida."""
    texto = _normalizar_texto(valor)
    if not texto:
        return ""
    mejor_id, mejor_puntaje = "", 0.0
    for identificador, datos in servicios.items():
        for opcion in (identificador, datos.get("nombre", "")):
            candidato = _normalizar_texto(opcion)
            if texto == candidato:
                return identificador
            if texto in candidato or candidato in texto:
                puntaje = max(0.82, min(len(texto), len(candidato)) / max(len(texto), len(candidato)))
            else:
                palabras, palabras_candidato = set(texto.split()), set(candidato.split())
                solapamiento = len(palabras & palabras_candidato) / max(1, len(palabras | palabras_candidato))
                puntaje = max(solapamiento, SequenceMatcher(None, texto, candidato).ratio() * 0.82)
            if puntaje > mejor_puntaje:
                mejor_id, mejor_puntaje = identificador, puntaje
    return mejor_id if mejor_puntaje >= 0.48 else ""


def normalizar_hora(valor):
    partes = re.fullmatch(r"(\d{1,2}):(\d{2})", valor)
    if partes is None or int(partes.group(1)) > 23 or int(partes.group(2)) > 59:
        return ""
    return f"{int(partes.group(1)):02d}:{partes.group(2)}"


def normalizar_fecha(valor):
    try:
        if date.fromisoformat(valor).isoformat() == valor:
            return valor
    except ValueError:
        pass
    return ""


def limpiar_plan(plan, servicios):
    """Deja el plan con todas las claves, en texto, y vacía lo que no sea válido."""
    if isinstance(plan.get("plan"), dict):  # el modelo copió el formato del registro
        plan = plan["plan"]
    limpio = {"accion": str(plan.get("accion") or "").strip().lower()}
    for campo in CAMPOS + ("respuesta",):
        valor = plan.get(campo)
        limpio[campo] = "" if valor is None else str(valor).strip()
    limpio["accion"] = SINONIMOS.get(limpio["accion"], limpio["accion"])
    if limpio["accion"] not in REQUERIDOS:
        limpio["accion"] = "conversar"
        limpio["respuesta"] = "No entendí tu solicitud. ¿Puedes contarme de otra forma qué necesitas?"
    limpio["codigo"] = limpio["codigo"].upper()
    limpio["servicio"] = buscar_servicio(limpio["servicio"], servicios)
    limpio["fecha"] = normalizar_fecha(limpio["fecha"])
    limpio["hora"] = normalizar_hora(limpio["hora"])
    return limpio


def unir(lista):
    if len(lista) <= 1:
        return "".join(lista)
    return ", ".join(lista[:-1]) + " y " + lista[-1]


def pedir_datos(plan, faltan, servicios):
    texto = f"Para {VERBOS[plan['accion']]} necesito {unir([NOMBRES_CAMPO[c] for c in faltan])}."
    if "servicio" in faltan:
        nombres = [nombre_servicio(servicios, clave) for clave in servicios]
        texto += f" Servicios disponibles: {', '.join(nombres)}."
    if "codigo" in faltan:
        texto += " Si no recuerdas el código, dime tu nombre y busco tus reservas."
    return texto


# ----------------------------------------------------------- decidir qué hacer

def rechazo(plan, texto):
    """El sistema no puede seguir adelante: se explica y no queda nada pendiente."""
    return {"tipo": "rechazo", "mensaje": texto}, plan, {"rechazo": texto}


def rango(hora, minutos):
    return f"{hora} a {sumar_minutos(hora, minutos)}"


def linea(etiqueta, antes, ahora):
    if antes is not None and antes != ahora:
        return f"- {etiqueta}: {antes} → **{ahora}**"
    return f"- {etiqueta}: {ahora}"


def resumen_creacion(plan, servicios):
    datos = servicios[plan["servicio"]]
    return "\n".join([
        "Voy a **crear** esta reserva:",
        "",
        f"- Cliente: {plan['cliente']}",
        f"- Servicio: {nombre_servicio(servicios, plan['servicio'])} "
        f"({datos['duracion_minutos']} min, {dinero(datos.get('precio_cop'))})",
        f"- Fecha: {dia_texto(plan['fecha'])}",
        f"- Hora: {rango(plan['hora'], datos['duracion_minutos'])}",
    ])


def resumen_modificacion(plan, servicios, actual):
    duracion = servicios[plan["servicio"]]["duracion_minutos"]
    return "\n".join([
        f"Voy a **modificar** la reserva **{plan['codigo']}** de {plan['cliente']}:",
        "",
        linea("Servicio", nombre_servicio(servicios, actual["servicio"]),
              nombre_servicio(servicios, plan["servicio"])),
        linea("Fecha", dia_texto(actual["fecha"]), dia_texto(plan["fecha"])),
        linea("Hora", rango(actual["hora"], actual["duracion_minutos"]),
              rango(plan["hora"], duracion)),
    ])


def preparar_confirmacion(plan, servicios):
    """Revisa la reserva y la disponibilidad antes de mostrar el resumen.

    La comprobación definitiva ocurre en FastAPI, dentro de la transacción;
    esta revisión solo evita pedir una confirmación que sería rechazada.
    """
    accion = plan["accion"]
    actual = None
    if accion in ("modificar", "cancelar"):
        actual = llamar_api("GET", RUTAS["reserva"].format(codigo=quote(plan["codigo"], safe="")))
        if actual["estado"] != "activa":
            return rechazo(plan, f"La reserva {plan['codigo']} ya está cancelada.")
        pidio_cambio = any(plan[c] for c in ("servicio", "fecha", "hora"))
        plan["cliente"] = actual["cliente"]
        for campo in ("servicio", "fecha", "hora"):
            if accion == "cancelar" or not plan[campo]:
                plan[campo] = actual[campo]
        if accion == "modificar" and not pidio_cambio:
            texto = "¿Qué quieres cambiar de la reserva: el servicio, la fecha o la hora?"
            return {"tipo": "pregunta", "mensaje": texto}, plan, {"mensaje": texto}
        if accion == "modificar" and all(plan[c] == actual[c] for c in ("servicio", "fecha", "hora")):
            return rechazo(plan, "La reserva ya tiene ese servicio, esa fecha y esa hora.")

    if accion == "cancelar":
        texto = "Voy a **cancelar** esta reserva:\n\n" + texto_reserva(actual, servicios)
    else:
        parametros = {"fecha": plan["fecha"], "servicio": plan["servicio"]}
        if accion == "modificar":
            parametros["excluir"] = plan["codigo"]  # la reserva no compite consigo misma
        libres = llamar_api("GET", RUTAS["disponibilidad"], parametros=parametros)["horas"]
        if plan["hora"] not in libres:
            nombre = nombre_servicio(servicios, plan["servicio"])
            dia = dia_texto(plan["fecha"])
            if libres:
                texto = (f"A las {plan['hora']} no hay espacio para {nombre} el {dia}. "
                         f"Horas libres: {', '.join(libres)}.")
            else:
                texto = f"No hay horas libres para {nombre} el {dia}. Prueba con otra fecha."
            return rechazo(plan, texto)
        if accion == "crear":
            texto = resumen_creacion(plan, servicios)
        else:
            texto = resumen_modificacion(plan, servicios, actual)

    texto += "\n\nPulsa **Confirmar operación** para ejecutarla o **Descartar solicitud** para no hacerlo."
    registrado = "Resumen mostrado; a la espera de confirmación."
    return {"tipo": "confirmacion", "mensaje": texto}, plan, {"mensaje": registrado}


def coincide_nombre(buscado, cliente):
    """Coincidencia tolerante a acentos, orden parcial y pequeños errores."""
    consulta, nombre = _normalizar_texto(buscado), _normalizar_texto(cliente)
    if not consulta or not nombre:
        return False
    if consulta in nombre or all(p in nombre.split() for p in consulta.split()):
        return True
    pq, pn = set(consulta.split()), set(nombre.split())
    cobertura = len(pq & pn) / max(1, len(pq))
    return cobertura >= 0.6 or SequenceMatcher(None, consulta, nombre).ratio() >= 0.72


def resolver_codigo_por_nombre(plan):
    """Resuelve modificación/cancelación por nombre si falta el código."""
    if plan["accion"] not in ("modificar", "cancelar") or plan["codigo"] or not plan["cliente"]:
        return None
    todas = llamar_api("GET", RUTAS["reservas"])
    candidatas = [
        r for r in todas
        if r.get("estado") == "activa" and coincide_nombre(plan["cliente"], r.get("cliente", ""))
    ]
    if len(candidatas) == 1:
        plan["codigo"] = candidatas[0]["codigo"]
        return None
    if len(candidatas) > 1:
        detalle = "\\n".join(
            f"- Código **{r['codigo']}**: {r['cliente']}, {r['servicio']}, {r['fecha']} a las {r['hora']}"
            for r in candidatas
        )
        texto = "Encontré varias reservas activas para ese nombre. Dime cuál código quieres usar:\\n\\n" + detalle
        return {"tipo": "pregunta", "mensaje": texto}, plan, {"mensaje": texto}
    return None


def decidir(plan, servicios):
    """Devuelve (resultado, plan, datos extra para el registro del historial)."""
    accion = plan["accion"]
    if accion == "conversar":
        texto = plan["respuesta"] or "¿En qué puedo ayudarte?"
        return {"tipo": "conversacion", "mensaje": texto}, plan, {"mensaje": texto}

    try:
        resuelto = resolver_codigo_por_nombre(plan)
        if resuelto is not None:
            return resuelto
    except HTTPError as error:
        if error.code not in (404, 409, 422):
            raise

    faltan = [campo for campo in REQUERIDOS[accion] if not plan[campo]]
    if faltan:
        texto = pedir_datos(plan, faltan, servicios)
        return {"tipo": "pregunta", "mensaje": texto}, plan, {"mensaje": texto}

    try:
        if accion in ACCIONES_ESCRITURA:
            return preparar_confirmacion(plan, servicios)
        resultado = ejecutar(plan)
        return resultado, plan, {"resultado_api": resultado["datos"]}
    except HTTPError as error:
        if error.code not in (404, 409, 422):
            raise
        detalle = detalle_http(error).rstrip(".")
        return rechazo(plan, f"No se pudo continuar: {detalle}.")


def procesar_turno(mensaje, historial, clave, modelo):
    """Un turno: devuelve (resultado, historial nuevo, plan).

    Si algo falla antes de terminar, se lanza la excepción y el historial
    recibido no cambia: un turno fallido no queda como respuesta del asistente.
    """
    negocio = llamar_api("GET", RUTAS["negocio"])
    servicios = negocio["servicios"]
    sistema = {"role": "system", "content": instrucciones(negocio)}  # actualiza la fecha de hoy
    anteriores = historial[1:][-MAX_MENSAJES:]
    candidatos = [sistema] + anteriores + [{"role": "user", "content": mensaje}]

    plan = limpiar_plan(consultar_groq(candidatos, clave, modelo), servicios)
    resultado, plan, extra = decidir(plan, servicios)

    asistente = {"role": "assistant", "content": registro(plan, **extra)}
    return resultado, candidatos + [asistente], plan
