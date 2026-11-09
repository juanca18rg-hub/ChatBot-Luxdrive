"""Canal de Telegram de LuxDrive Taller.

Va junto a chatbot_memoria.py y consulta_llm.py. Recibe el mensaje en Telegram,
llama a procesar_turno() (el mismo que usa app_gradio.py) y envía la respuesta
al mismo chat. En esta versión solo atiende consultas: nunca llama a
ejecutar(..., confirmar=True), así que ninguna reserva se crea, modifica ni
cancela desde Telegram.
"""

import asyncio
import logging
import os
from getpass import getpass
from urllib.error import HTTPError, URLError

from telegram import Update
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from chatbot_memoria import nuevo_historial, procesar_turno
from consulta_llm import dinero

BIENVENIDA = (
    "Hola. Soy el asistente de LuxDrive Taller. "
    "Puedes consultar nuestros servicios, sus precios y los horarios disponibles. "
    "Escribe /help para ver ejemplos."
)
AYUDA = (
    "/start: mostrar la bienvenida.\n"
    "/help: ver esta ayuda.\n"
    "/reset: reiniciar esta conversación.\n\n"
    "Prueba: ¿Qué servicios tienen y cuánto cuestan?\n"
    "O pregunta por disponibilidad indicando servicio y fecha, por ejemplo: "
    "¿Qué horas hay para alineación y balanceo mañana?\n\n"
    "En esta versión las operaciones de reserva (crear, cambiar o cancelar) "
    "quedan sin ejecutar. Para reservar usa la interfaz web del taller."
)
# Estas acciones muestran datos de reservas de otras personas o las modifican.
# Quedan para la interfaz web y para la siguiente sesión (reservas con confirmación).
ACCIONES_SOLO_WEB = ("consultar", "listar", "modificar", "cancelar")
MENSAJE_SOLO_WEB = (
    "Por ahora Telegram solo atiende consultas de servicios, precios y disponibilidad. "
    "Consultar, cambiar o cancelar reservas no está disponible aquí y no se ejecutó nada. "
    "Usa la interfaz web del taller."
)
NO_EJECUTADA = (
    "Telegram solo atiende consultas en esta versión: la operación NO se ejecutó "
    "y no se guardó ninguna reserva. Para reservar usa la interfaz web del taller."
)


async def enviar_texto(mensaje, texto):
    # Telegram admite hasta 4096 caracteres por mensaje.
    for inicio in range(0, len(texto), 4000):
        await mensaje.reply_text(texto[inicio:inicio + 4000])


async def iniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message is not None:
        if "historial" not in context.chat_data:
            context.chat_data["historial"] = await asyncio.to_thread(nuevo_historial)
        await enviar_texto(update.effective_message, BIENVENIDA)


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message is not None:
        await enviar_texto(update.effective_message, AYUDA)


async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message is not None:
        context.chat_data["historial"] = await asyncio.to_thread(nuevo_historial)
        await enviar_texto(
            update.effective_message,
            "Conversación reiniciada. Dime qué deseas consultar. Las reservas del negocio se conservan.",
        )


def quitar_markdown(texto):
    """Los mensajes del proyecto usan **negrita** y `código` para Gradio; Telegram los mostraría tal cual."""
    return str(texto).replace("**", "").replace("`", "")


def formatear_resultado(resultado, plan):
    """Convierte lo que devuelve procesar_turno() en texto plano para Telegram."""
    if resultado.get("tipo") == "confirmacion":
        # El resumen termina con «Pulsa Confirmar operación…», que habla de botones de Gradio.
        resumen = quitar_markdown(resultado.get("mensaje", "")).split("\n\nPulsa")[0].strip()
        return f"{resumen}\n\n{NO_EJECUTADA}"

    datos = resultado.get("datos")
    if plan["accion"] == "servicios" and isinstance(datos, dict) and datos:
        lineas = ["Servicios disponibles:"]
        for identificador, detalle in datos.items():
            nombre = detalle.get("nombre", identificador)
            lineas.append(
                f"• {nombre}: {dinero(detalle.get('precio_cop'))}. "
                f"Duración: {detalle.get('duracion_minutos', 'por consultar')} minutos."
            )
        return "\n".join(lineas)

    texto = quitar_markdown(resultado.get("mensaje", "")).strip()
    if plan["accion"] == "disponibilidad" and isinstance(datos, dict) and "horas" in datos:
        # Se quita la invitación final a reservar: aquí todavía no se puede.
        texto = texto.rsplit("\n\n", 1)[0] if datos["horas"] else texto
        if datos["horas"]:
            texto += "\n\nSon horas de inicio de la cita."
    return texto or "No tengo una respuesta para esa consulta."


async def recibir_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.effective_message
    if mensaje is None or not mensaje.text or not mensaje.text.strip():
        return
    clave = context.bot_data.get("clave_modelo", "")
    modelo = context.bot_data.get("modelo", "")
    if not clave or not modelo:
        await enviar_texto(mensaje, "Falta configurar GROQ_API_KEY o GROQ_MODEL en el servidor del bot.")
        return
    historial = context.chat_data.get("historial")
    try:
        if historial is None:
            historial = await asyncio.to_thread(nuevo_historial)
        # procesar_turno() es síncrono (HTTP a Groq y a FastAPI): va en otro hilo.
        resultado, actualizado, plan = await asyncio.to_thread(
            procesar_turno, mensaje.text.strip(), historial, clave, modelo
        )
        if plan["accion"] in ACCIONES_SOLO_WEB:
            # No se guarda este turno en el historial: no se arrastran datos de reservas.
            await enviar_texto(mensaje, MENSAJE_SOLO_WEB)
            return
        texto = formatear_resultado(resultado, plan)
    except HTTPError:
        await enviar_texto(mensaje, "El servicio rechazó la consulta. Revisa la configuración o los datos.")
        return
    except (URLError, TimeoutError):
        await enviar_texto(
            mensaje,
            "No pude conectar con el servidor del taller o con el modelo. Intenta la consulta más tarde.",
        )
        return
    except (ValueError, KeyError, IndexError, TypeError):
        await enviar_texto(mensaje, "La respuesta del servicio no tiene el formato esperado.")
        return
    if resultado.get("tipo") == "confirmacion":
        # Igual que «Descartar solicitud» en Gradio: el modelo no debe creer que algo quedó pendiente.
        actualizado = actualizado + [
            {"role": "user", "content": "Descartar la solicitud pendiente."},
            {"role": "assistant", "content": "Solicitud no ejecutada; Telegram solo atiende consultas."},
        ]
    context.chat_data["historial"] = actualizado
    await enviar_texto(mensaje, texto)


async def comando_desconocido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await enviar_texto(update.effective_message, "No conozco ese comando. Usa /start, /help o /reset.")


async def otro_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await enviar_texto(update.effective_message, "Por ahora recibo consultas escritas. Envíame un mensaje de texto.")


async def informar_error(update, context):
    # Registramos la clase del error sin imprimir credenciales ni URL del bot.
    logging.error("Error del bot: %s", type(context.error).__name__)


def registrar_handlers(aplicacion):
    privado = filters.ChatType.PRIVATE
    aplicacion.add_handler(CommandHandler("start", iniciar, filters=privado))
    aplicacion.add_handler(CommandHandler("help", ayuda, filters=privado))
    aplicacion.add_handler(CommandHandler("reset", reiniciar, filters=privado))
    aplicacion.add_handler(
        MessageHandler(privado & filters.TEXT & ~filters.COMMAND, recibir_texto)
    )
    aplicacion.add_handler(MessageHandler(privado & filters.COMMAND, comando_desconocido))
    aplicacion.add_handler(MessageHandler(privado & ~filters.TEXT, otro_mensaje))
    aplicacion.add_error_handler(informar_error)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or getpass("Token de BotFather (oculto): ").strip()
    if not token:
        print("Necesito el token de Telegram para iniciar.")
        return
    try:
        aplicacion = Application.builder().token(token).build()
        aplicacion.bot_data["clave_modelo"] = os.getenv("GROQ_API_KEY", "").strip()
        aplicacion.bot_data["modelo"] = os.getenv("GROQ_MODEL", "").strip()
        registrar_handlers(aplicacion)
        if not aplicacion.bot_data["clave_modelo"] or not aplicacion.bot_data["modelo"]:
            print("Los comandos funcionarán. Configura GROQ_API_KEY y GROQ_MODEL para las consultas.")
        print("Bot en ejecución. Abre su conversación en Telegram. Para detenerlo: Ctrl+C.")
        aplicacion.run_polling(allowed_updates=["message"])
    except InvalidToken:
        print("Token inválido. Comprueba la credencial entregada por BotFather.")
    except NetworkError:
        print("No fue posible iniciar la conexión con Telegram. Revisa Internet e inténtalo otra vez.")
    except TelegramError as error:
        print(f"Telegram no completó el inicio: {type(error).__name__}.")


if __name__ == "__main__":
    main()
