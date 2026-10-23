"""Interfaz del chatbot de LuxDrive Taller.

Gradio muestra la conversación y los botones. El estado de cada sesión guarda
el historial, lo que se ve en pantalla y la operación pendiente de confirmar.
La FastAPI debe estar encendida (CHATBOT_API_URL) y Groq configurado
(GROQ_API_KEY y GROQ_MODEL).
"""

import os
from urllib.error import HTTPError

import gradio as gr

from chatbot_memoria import nuevo_historial, procesar_turno, registro
from consulta_llm import detalle_http, ejecutar

CLAVE = os.getenv("GROQ_API_KEY", "").strip()
MODELO = os.getenv("GROQ_MODEL", "").strip()

BIENVENIDA = (
    "### LuxDrive Taller\n"
    "Pregúntame por los servicios y las horas disponibles, o pídeme agendar, "
    "consultar, modificar o cancelar una cita."
)


def nuevo_estado():
    return {
        "historial": nuevo_historial(),
        "pantalla": [],
        "pendiente": None,
    }


def salida(estado, aviso):
    return estado["pantalla"], estado, "", aviso, estado["historial"]


def enviar(mensaje, estado):
    mensaje = mensaje.strip()
    if not mensaje:
        raise gr.Error("Escribe una solicitud antes de enviarla.")
    if estado["pendiente"] is not None:
        raise gr.Error("Usa «Confirmar operación» o «Descartar solicitud» antes de seguir.")
    if not CLAVE or not MODELO:
        raise gr.Error("Configura GROQ_API_KEY y GROQ_MODEL e inicia de nuevo.")

    try:
        resultado, historial, plan = procesar_turno(
            mensaje, estado["historial"], CLAVE, MODELO
        )
    except HTTPError as error:
        raise gr.Error(f"Error HTTP {error.code}: {detalle_http(error)}") from error
    except OSError as error:
        raise gr.Error(
            "No pude conectar con Groq o FastAPI. Revisa que la API esté encendida "
            "y que haya conexión."
        ) from error
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise gr.Error("La respuesta no tiene el formato esperado. Intenta de nuevo.") from error

    pendiente = plan if resultado["tipo"] == "confirmacion" else None
    nuevo = {
        "historial": historial,
        "pantalla": estado["pantalla"] + [
            {"role": "user", "content": mensaje},
            {"role": "assistant", "content": resultado["mensaje"]},
        ],
        "pendiente": pendiente,
    }
    aviso = "Revisa el resumen y confirma o descarta." if pendiente else "Listo."
    return salida(nuevo, aviso)


def confirmar(estado):
    plan = estado["pendiente"]
    if plan is None:
        raise gr.Error("No hay una operación pendiente.")
    pedido = {"role": "user", "content": "Confirmar la operación pendiente."}

    try:
        resultado = ejecutar(plan, confirmar=True)
    except HTTPError as error:
        detalle = detalle_http(error)
        if error.code >= 500:
            raise gr.Error(f"FastAPI respondió con HTTP {error.code}: {detalle}") from error
        # FastAPI rechazó la operación (por ejemplo, un cruce de horarios): no se guardó nada.
        texto = (
            f"No se realizó la operación (HTTP {error.code}): {detalle.rstrip('.')}. "
            "Dime cómo quieres continuar."
        )
        nuevo = {
            "historial": estado["historial"] + [
                pedido,
                {"role": "assistant", "content": registro(plan, rechazo=detalle)},
            ],
            "pantalla": estado["pantalla"] + [pedido, {"role": "assistant", "content": texto}],
            "pendiente": None,
        }
        return salida(nuevo, "FastAPI rechazó la operación.")
    except OSError as error:
        raise gr.Error(
            "No recibí respuesta. Consulta la reserva antes de repetir la operación."
        ) from error
    except (ValueError, KeyError, TypeError) as error:
        raise gr.Error("Revisa la respuesta de FastAPI antes de repetir la operación.") from error

    nuevo = {
        "historial": estado["historial"] + [
            pedido,
            {"role": "assistant", "content": registro(plan, resultado_api=resultado["datos"])},
        ],
        "pantalla": estado["pantalla"] + [
            pedido,
            {"role": "assistant", "content": resultado["mensaje"]},
        ],
        "pendiente": None,
    }
    return salida(nuevo, "Operación respondida por FastAPI. Revisa el resultado.")


def descartar(estado):
    if estado["pendiente"] is None:
        raise gr.Error("No hay una solicitud pendiente para descartar.")
    mensajes = [
        {"role": "user", "content": "Descartar la solicitud pendiente."},
        {"role": "assistant", "content": "Solicitud descartada; no se ejecutó la operación."},
    ]
    nuevo = {
        "historial": estado["historial"] + mensajes,
        "pantalla": estado["pantalla"] + mensajes,
        "pendiente": None,
    }
    return salida(nuevo, "Puedes iniciar otra solicitud.")


def reiniciar():
    return salida(nuevo_estado(), "Conversación reiniciada.")


def crear_interfaz(titulo="LuxDrive Taller · Asistente virtual"):
    with gr.Blocks(title=titulo) as demo:
        gr.Markdown(f"## {titulo}")
        estado = gr.State(value=nuevo_estado)
        pantalla = gr.Chatbot(label="Conversación", height=460, buttons=[], placeholder=BIENVENIDA)
        entrada = gr.Textbox(label="Tu mensaje", placeholder="Escribe tu solicitud", elem_id="mensaje_chat")
        aviso = gr.Markdown("Listo para comenzar.")

        with gr.Row():
            boton_enviar = gr.Button("Enviar", variant="primary", elem_id="enviar_chat")
            boton_confirmar = gr.Button("Confirmar operación", elem_id="confirmar_chat")
            boton_descartar = gr.Button("Descartar solicitud", elem_id="descartar_chat")
            boton_reiniciar = gr.Button("Reiniciar conversación", elem_id="reiniciar_chat")

        with gr.Accordion("Revisar historial del laboratorio", open=False):
            historial_debug = gr.JSON(label="Mensajes para Groq", value=nuevo_historial())

        salidas = [pantalla, estado, entrada, aviso, historial_debug]
        # Una cola común evita solapar envío, confirmación y reinicio.
        eventos = {"concurrency_id": "operaciones_chat", "concurrency_limit": 1}
        boton_enviar.click(enviar, inputs=[entrada, estado], outputs=salidas, **eventos)
        entrada.submit(enviar, inputs=[entrada, estado], outputs=salidas, **eventos)
        boton_confirmar.click(confirmar, inputs=[estado], outputs=salidas, **eventos)
        boton_descartar.click(descartar, inputs=[estado], outputs=salidas, **eventos)
        boton_reiniciar.click(reiniciar, inputs=[], outputs=salidas, **eventos)
        pantalla.clear(reiniciar, inputs=[], outputs=salidas, **eventos)

    return demo


if __name__ == "__main__":
    crear_interfaz().queue().launch(server_name="127.0.0.1", server_port=7860)
