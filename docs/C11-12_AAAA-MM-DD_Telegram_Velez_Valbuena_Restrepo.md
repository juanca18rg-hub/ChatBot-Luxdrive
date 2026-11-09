# Bitácora — Clase [11–12]

> **Cómo usar esta plantilla**
> Duplica esta nota al finalizar cada clase y nómbrala `C##_AAAA-MM-DD_Tema`. Completa los campos entre corchetes y conserva enlaces verificables.

## 1. Datos de la sesión

| Campo | Registro |
|---|---|
| Tema | Telegram como canal del chatbot · long polling, comandos y adaptador (`bot_telegram.py`) |
| Objetivo | Que una persona abra el bot del equipo en Telegram, use `/start`, `/help` y `/reset`, y consulte servicios, precios y disponibilidad reales de LuxDrive Taller, reutilizando el chatbot que ya existía |
| Proyecto | Chatbot de servicio para un Taller Automotriz (LuxDrive Taller) |
| Inicio | [hora de inicio] |
| Cierre | [hora de cierre] |

## 2. Participación y responsabilidades

| Integrante | Asistió | Rol de la clase | Responsabilidad asumida | Aporte verificable |
|---|---|---|---|---|
| Juan Camilo Restrepo | [Sí/No] | [rol] | [responsabilidad] | [commit, archivo o evidencia] |
| Juan Jose Valbuena | [Sí/No] | [rol] | [responsabilidad] | [commit, archivo o evidencia] |
| Sebastian Velez Bedoya | [Sí/No] | [rol] | [responsabilidad] | [commit, archivo o evidencia] |

## 3. Reto aplicado al proyecto

**Funcionalidad trabajada**

- [x] Copiar `bot_telegram.py` junto a `chatbot_memoria.py` y `consulta_llm.py`, y adaptar su texto de bienvenida y de ayuda a LuxDrive Taller.
- [x] Reutilizar `nuevo_historial()` y `procesar_turno()` sin cambiar su firma: reciben pregunta, historial, clave y modelo y devuelven resultado, historial actualizado y plan.
- [x] Adaptar `formatear_resultado()` a lo que devuelve nuestra API (`nombre`, `precio_cop`, `duracion_minutos`, `horas`).
- [x] Mantener la regla de la sesión: el adaptador nunca llama a `ejecutar(..., confirmar=True)`; si llega un resultado de tipo `confirmacion`, informa que la operación no se ejecutó.
- [x] Agregar `python-telegram-bot>=22,<23` a `requirements.txt` y documentar la ejecución en el README.
- [ ] Crear el bot con BotFather y publicar su nombre o enlace: [@usuario_del_bot].

**Recorrido de un mensaje**

```text
Persona escribe en Telegram
  → run_polling()                          el proceso Python recoge la actualización (long polling)
  → MessageHandler (chat privado, texto, sin comando)
  → recibir_texto(update, context)         [bot_telegram.py] saca el texto de update.effective_message
  → asyncio.to_thread(procesar_turno, …)   [chatbot_memoria.py] Groq interpreta → FastAPI → SQLite
  → formatear_resultado(resultado, plan)   [bot_telegram.py] convierte el resultado en texto plano
  → enviar_texto() → reply_text()          devuelve la respuesta al mismo chat
```

| Qué hay que localizar | Dónde está |
|---|---|
| Función que recibe la pregunta | `recibir_texto()` en `bot_telegram.py` |
| Llamada al módulo anterior | `procesar_turno()` y `nuevo_historial()` de `chatbot_memoria.py`, ejecutados con `asyncio.to_thread()` |
| Conexión a Groq y a FastAPI | `consulta_llm.py` (`consultar_groq()`, `llamar_api()`), usada por `procesar_turno()` |
| Método que entrega la respuesta | `reply_text()`, dentro de `enviar_texto()` (divide en bloques de 4000 caracteres) |
| Historial de cada conversación | `context.chat_data["historial"]`; `/reset` lo reemplaza y no borra las reservas |

**Funciones reutilizadas:** `nuevo_historial()` y `procesar_turno()` (`chatbot_memoria.py`), `dinero()` (`consulta_llm.py`) y, a través de `procesar_turno()`, `consultar_groq()`, `llamar_api()` y la API con SQLite.

**Criterios de aceptación**

- [ ] `/start` presenta LuxDrive Taller y orienta cómo comenzar.
- [ ] `/help` muestra comandos y ejemplos que realmente están implementados.
- [ ] Una consulta de servicios o precios coincide con `GET /api/servicios`.
- [ ] Una consulta de disponibilidad usa el servicio y la fecha pedidos y coincide con `GET /api/disponibilidad`.
- [ ] Un dato pendiente se completa en otro mensaje con el contexto del mismo chat.
- [ ] `/start` a mitad de la consulta no pierde el historial; `/reset` sí lo reinicia.
- [ ] Un comando inexistente orienta hacia `/start`, `/help` y `/reset`; una foto pide un mensaje de texto.
- [ ] Con FastAPI apagada el bot informa el fallo y no inventa un resultado.
- [ ] Una solicitud de reserva muestra el resumen y avisa que no se ejecutó; no aparece ninguna reserva nueva.
- [ ] Ni el token de Telegram ni la API key de Groq aparecen en el código, las capturas o esta bitácora.

**Archivos o componentes modificados**

- `bot_telegram.py` : archivo nuevo; adaptador de Telegram con los handlers, el formato de las respuestas y las reglas de solo consulta
- `requirements.txt` : se agregó `python-telegram-bot>=22,<23`
- `README.md` : sección «Canal de Telegram», estructura del proyecto actualizada y conflicto de Git resuelto (quedaron las dos versiones mezcladas)
- `prueba_telegram.py` : archivo nuevo; prueba automática del adaptador con Telegram y Groq simulados

## 4. Decisiones técnicas

| Decisión | Razón | Alternativa descartada |
|---|---|---|
| Atender solo consultas y no llamar nunca a `ejecutar(..., confirmar=True)` | Es el alcance de esta sesión; las reservas con confirmación desde Telegram se desarrollan en la siguiente. Evita guardar algo con un «sí» suelto | Aceptar un «sí» en el chat y ejecutar la reserva |
| Mostrar texto plano: quitar `**` y las comillas invertidas, y armar la lista de servicios desde los datos de la API | Telegram no interpreta el Markdown de Gradio y mostraría los símbolos tal cual | Activar `parse_mode` de Markdown, que falla si el texto trae un símbolo mal cerrado |
| No mostrar por Telegram consultar, listar, modificar ni cancelar reservas | Esas respuestas traen nombre, servicio y fecha de otras personas, y cualquiera puede escribirle al bot | Mostrar las reservas de quien las pida por nombre o código |
| Ejecutar `procesar_turno()` y `nuevo_historial()` con `asyncio.to_thread()` | Hacen peticiones HTTP síncronas a Groq y FastAPI; si se llaman directo, el bot no atiende a nadie mientras esperan | Llamarlas directamente dentro de la función `async` |
| Recibir los mensajes con long polling | El computador del bot abre la conexión y no hace falta una dirección pública | Webhook, que necesita una URL accesible desde Internet |

## 5. Problemas y soluciones

| Problema encontrado | Causa identificada | Solución aplicada | ¿Cómo se verificó? |
|---|---|---|---|
| Al revisar `formatear_resultado()` contra nuestro proyecto: los mensajes de `procesar_turno()` llevan asteriscos y comillas invertidas, y el resumen de una reserva dice «Pulsa Confirmar operación», botón que no existe en Telegram | Los textos de `procesar_turno()` están escritos para Gradio (Markdown y botones) | `quitar_markdown()`, cortar el párrafo «Pulsa…» y reemplazarlo por un aviso de que la operación no se ejecutó | Prueba automática `prueba_telegram.py` y [captura en Telegram] |
| El ejemplo de la lista de servicios habría mostrado el identificador (`cambio_aceite`) y el precio como «por consultar» | El ejemplo de `formatear_resultado()` buscaba la clave `precio`, pero nuestra API devuelve `nombre` y `precio_cop` | Se leyó `nombre`, `precio_cop` y `duracion_minutos` y se formateó el precio con `dinero()` | La lista del bot coincide con `GET /api/servicios` ([captura de ambas]) |
| Riesgo de que el bot dejara de atender mientras esperaba a FastAPI o a Groq | `nuevo_historial()` hace una petición HTTP síncrona y el ejemplo la evaluaba en cada `/start` (`setdefault`) dentro de la función `async` | Se ejecuta con `asyncio.to_thread()` y solo se crea cuando todavía no existe el historial | Prueba con FastAPI apagada: el bot informa el fallo y no inventa datos |
| [Problema real que tuvo el equipo al conectar el bot] | [Causa] | [Solución aplicada] | [Prueba o evidencia] |

## 6. Evidencias

| Tipo | Descripción | Enlace o ruta |
|---|---|---|
| captura | `/start` y `/help` en Telegram | [ruta] |
| captura | consulta de servicios y precios, junto a `GET /api/servicios` | [ruta] |
| captura | consulta de disponibilidad con servicio y fecha, junto a `GET /api/disponibilidad` | [ruta] |
| captura | consulta incompleta completada en otro mensaje | [ruta] |
| captura | `/start` a mitad de la consulta y `/reset` seguido del dato suelto | [ruta] |
| captura | comando inexistente y foto | [ruta] |
| captura | solicitud de reserva: resumen y aviso de que no se ejecutó | [ruta] |
| captura | FastAPI apagada y consulta repetida | [ruta] |
| código | `recibir_texto()` y `formatear_resultado()` | `bot_telegram.py` |
| script | prueba automática del adaptador (resultado: TODO OK) | `.\.venv\Scripts\python.exe prueba_telegram.py` |
| script | iniciar FastAPI | `.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8011` |
| script | iniciar el bot | `.\.venv\Scripts\python.exe bot_telegram.py` |
| enlace | bot en Telegram | [https://t.me/usuario_del_bot] |

> No incluir el token de BotFather ni la API key de Groq en ninguna evidencia.

## 7. Uso de inteligencia artificial

> Registrar la IA no reemplaza la explicación técnica. El equipo debe verificar, adaptar y comprender cualquier resultado utilizado.

| Herramienta | Objetivo o pregunta | Qué se utilizó | Cómo se verificó | Qué se modificó |
|---|---|---|---|---|
| Claude | Adaptar `bot_telegram.py` al proyecto y preparar la prueba automática, el README y esta entrada | Código del adaptador, formato de las respuestas y borrador de la bitácora | Ejecutando `prueba_telegram.py` y probando el bot real en Telegram con FastAPI encendida | [lo que ajustó el equipo] |

## 8. Defensa técnica y retroalimentación

| Campo | Registro |
|---|---|
| Integrante que defendió | [Nombre] |
| Pregunta recibida | [Pregunta] |
| Respuesta resumida | [Respuesta] |
| Retroalimentación docente | [Observación] |
| Mejora acordada | [Acción concreta] |

## 9. Aprendizajes

- Para agregar un canal nuevo basta con un adaptador que reciba el mensaje y entregue la respuesta: la conversación, Groq, FastAPI y SQLite siguieron en sus módulos.
- Un texto escrito para una interfaz (Markdown y botones de Gradio) no sirve igual en otra; cada canal necesita su propio formato de salida.
- Las llamadas síncronas dentro de funciones `async` hay que moverlas a otro hilo para que el bot no se bloquee.
- Cuando un bot es público, conviene decidir qué datos de las reservas se muestran y cuáles no.

## 10. Revisión antes de entregar

- [ ] Todos los integrantes registraron un aporte verificable.
- [ ] Los enlaces y rutas de evidencia funcionan.
- [ ] Los criterios de aceptación están actualizados.
- [ ] El uso de IA fue declarado y verificado.
- [ ] El equipo puede explicar las decisiones registradas.
