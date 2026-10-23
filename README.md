<<<<<<< HEAD
# ChatBot-Luxdrive
=======
# LuxDrive Taller — chatbot CRUD (Clase 08–09)

Esta versión integra **Gradio + Groq + FastAPI + SQLite** y conserva la estructura del proyecto original. La API es la única capa que lee/escribe las reservas; Gradio no modifica la base directamente.

## Funciones incluidas

- **Crear** reserva con nombre, servicio, fecha y hora; exige confirmar antes de guardar.
- **Consultar/obtener** una reserva por código.
- **Listar** reservas por nombre, con búsqueda tolerante a acentos, nombres parciales y pequeños errores de escritura.
- **Actualizar/modificar** servicio, fecha u hora de una reserva; exige confirmación y vuelve a validar disponibilidad.
- **Eliminar/cancelar** una reserva; se conserva como histórico con estado `cancelada`, en lugar de borrarla físicamente.
- Consultar servicios y disponibilidad.
- Conversación con contexto por sesión, confirmación/descartar operaciones, reinicio y manejo de solicitudes incompletas.
- SQLite con validación de horario, duración del servicio y cruces de intervalos dentro de una transacción.
- Si se modifica o cancela por nombre sin código, busca las reservas activas: si encuentra una, la selecciona; si hay varias, pide elegir el código.

> Las operaciones de escritura requieren que la persona pulse **Confirmar operación**. Una petición ambigua no se ejecuta silenciosamente: el chatbot debe preguntar por los datos que falten o por el código cuando haya varias coincidencias.

## Requisitos

- Python 3.10 o posterior (recomendado 3.11/3.12).
- Una API key de Groq y el identificador de un modelo habilitado en tu cuenta.
- Internet para que el chatbot consulte Groq. FastAPI/SQLite funcionan localmente.
- Windows PowerShell (los comandos de abajo son para Windows).

## Instalación desde cero

Descomprime la carpeta y abre PowerShell dentro de `LuxDriveChatBotDefi_funcional`.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Si `py` no está reconocido, instala Python desde python.org y marca **Add Python to PATH** durante la instalación. También puedes sustituir `py` por la ruta completa a `python.exe`.

## Configurar Groq e iniciar

Abre **dos terminales PowerShell** dentro de la carpeta del proyecto.

### Terminal 1 — API y base de datos

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8011
```

La API crea `reservas.db` automáticamente al iniciar. No borres ese archivo si quieres conservar las reservas. Revisa `http://127.0.0.1:8011/docs` para probar los endpoints.

### Terminal 2 — Chatbot Gradio

```powershell
$env:CHATBOT_API_URL = "http://127.0.0.1:8011"
$env:GROQ_API_KEY="Apikey de groq"
$env:GROQ_MODEL="openai/gpt-oss-120b"
.\.venv\Scripts\python.exe app_gradio.py
```



Reemplaza `ID_DEL_MODELO_HABILITADO_EN_GROQ` por un modelo actisvo que tu cuenta de Groq permita usar. La clave se define en la terminal y no se guarda en los archivos del proyecto. No compartas la clave ni la subas a Git.

Abre `http://127.0.0.1:7860`. Mantén las dos terminales abiertas. Las variables de entorno solo existen en la terminal donde las configuraste; si cierras esa terminal, vuelve a configurarlas.

## Ejemplos para probar el CRUD

Prueba con fechas futuras, de lunes a sábado, dentro de 09:00–18:00. Las horas de inicio van en intervalos de 30 minutos y la cita debe terminar antes de las 18:00.

1. **Servicios:** `¿Qué servicios tienen y cuánto cuestan?`
2. **Disponibilidad:** `¿Qué horas tienen para cambio de aceite mañana?`
3. **Crear:** `Quiero reservar un cambio de aceite para Camilo el [fecha] a las 10:00.`
   - Completa los datos que pregunte el bot y pulsa **Confirmar operación**.
   - Guarda el código que devuelve la API.
4. **Obtener/consultar:** `Consulta la reserva ABC12345` (usa el código real).
5. **Listar:** `Muéstrame las reservas de Camilo`.
6. **Actualizar:** `Cambia la reserva ABC12345 para diagnóstico el [fecha] a las 11:00`.
   - Se conserva cualquier campo que no indiques; revisa el resumen y confirma.
7. **Eliminar/cancelar:** `Cancela la reserva ABC12345` o `Cancela la reserva de Camilo`.
   - Si el nombre coincide con varias reservas activas, el bot te pedirá el código.
8. **Conflicto:** intenta crear otra cita en un intervalo que se cruce con una reserva activa; la API debe rechazarla.
9. **Descartar:** cuando aparezca un resumen pendiente, pulsa **Descartar solicitud** y verifica que no se haya guardado.
10. **Sesión nueva:** pulsa **Reiniciar conversación**. Esto borra el historial de la sesión, no las reservas guardadas en SQLite.

## API REST disponible

- `GET /api/negocio`
- `GET /api/servicios`
- `GET /api/disponibilidad?fecha=AAAA-MM-DD&servicio=cambio_aceite`
- `GET /api/reservas`
- `GET /api/reservas/{codigo}` — obtener
- `POST /api/reservas` — crear (el cuerpo debe incluir `confirmada: true`)
- `PUT /api/reservas/{codigo}` — actualizar servicio/fecha/hora
- `DELETE /api/reservas/{codigo}` — cancelar lógicamente

La documentación interactiva está en `http://127.0.0.1:8011/docs`.

## Datos del negocio

Edita `negocio.json` para cambiar servicios, precios, duración u horario. Los IDs del catálogo son los valores que se guardan en SQLite. Si cambias la duración de un servicio, las reservas ya creadas conservan su duración histórica hasta que sean modificadas.

## Problemas frecuentes

- **No conecta con FastAPI:** confirma que la Terminal 1 siga abierta y que `CHATBOT_API_URL` use el puerto 8011.
- **Error de Groq/modelo:** verifica la API key y que `GROQ_MODEL` sea un modelo habilitado; luego reinicia Gradio.
- **La hora no aparece:** verifica que el negocio esté abierto ese día, que el inicio sea cada 30 minutos, que la cita termine antes de las 18:00 y que no se cruce con otra.
- **No se puede reservar una fecha pasada o domingo:** es una validación intencional del negocio.
- **No encuentra una reserva por nombre:** prueba con el nombre completo. Si existen varias, usa el código de la reserva.
- **No borra físicamente una reserva:** el botón eliminar marca el estado como `cancelada` para conservar el histórico y liberar el horario.

## Estructura del proyecto

```text
LuxDriveChatBotDefi_funcional/
├── main.py                 # API FastAPI y endpoints CRUD
├── almacen_sqlite.py       # reglas y persistencia SQLite
├── chatbot_memoria.py      # interpretación, historial y confirmación
├── consulta_llm.py         # Groq + cliente HTTP de FastAPI
├── app_gradio.py           # interfaz web con botones
├── negocio.json            # servicios y horario
├── requirements.txt
└── reservas.db             # se crea al iniciar la API
```

No se incluye ninguna API key. El archivo `reservas.db` se crea vacío cuando se ejecuta `main.py`.
>>>>>>> master
