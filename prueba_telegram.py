r"""Prueba automática de bot_telegram.py. No necesita Telegram, Groq ni Internet.

Se simulan Telegram y Groq; los datos salen de AgendaSQLite (con una base temporal,
no toca reservas.db). Ejecutar desde la carpeta del proyecto:
    .\.venv\Scripts\python.exe prueba_telegram.py
"""
import asyncio, json, os, sys, tempfile, threading, types
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse, parse_qs

PROYECTO = Path(__file__).resolve().parent
os.environ["CHATBOT_API_URL"] = "http://127.0.0.1:8099"
sys.path.insert(0, str(PROYECTO))

# ---- Telegram simulado (la biblioteca real no se puede instalar aquí)
class Filtro:
    def __init__(self, nombre): self.nombre = nombre
    def __and__(self, otro): return Filtro(f"({self.nombre}&{otro.nombre})")
    def __invert__(self): return Filtro(f"~{self.nombre}")
tg = types.ModuleType("telegram"); tg.Update = object
err = types.ModuleType("telegram.error")
class TelegramError(Exception): pass
class InvalidToken(TelegramError): pass
class NetworkError(TelegramError): pass
err.TelegramError, err.InvalidToken, err.NetworkError = TelegramError, InvalidToken, NetworkError
ext = types.ModuleType("telegram.ext")
ext.Application = object
ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
class CommandHandler:
    def __init__(self, c, f, filters=None): self.c, self.f = c, f
class MessageHandler:
    def __init__(self, flt, f): self.flt, self.f = flt, f
ext.CommandHandler, ext.MessageHandler = CommandHandler, MessageHandler
ext.filters = types.SimpleNamespace(ChatType=types.SimpleNamespace(PRIVATE=Filtro("PRIVATE")),
                                    TEXT=Filtro("TEXT"), COMMAND=Filtro("COMMAND"))
sys.modules.update({"telegram": tg, "telegram.error": err, "telegram.ext": ext})

# ---- API simulada con la agenda SQLite real del proyecto
from almacen_sqlite import AgendaSQLite
NEGOCIO = json.loads((PROYECTO / "negocio.json").read_text(encoding="utf-8-sig"))
H = NEGOCIO["horario"]
agenda = AgendaSQLite(Path(tempfile.mkdtemp()) / "t.db", NEGOCIO["servicios"], apertura=H["apertura"],
                      cierre=H["cierre"], dias=H["dias"], paso=H["paso"])
class API(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/negocio": cuerpo = NEGOCIO
            elif u.path == "/api/servicios": cuerpo = NEGOCIO["servicios"]
            elif u.path == "/api/disponibilidad": cuerpo = agenda.disponibilidad(q["fecha"], q["servicio"], q.get("excluir", ""))
            elif u.path == "/api/reservas": cuerpo = agenda.listar()
            else: raise ValueError("ruta")
            codigo = 200
        except ValueError as e:
            cuerpo, codigo = {"detail": str(e)}, 422
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(datos)
servidor = HTTPServer(("127.0.0.1", 8099), API); threading.Thread(target=servidor.serve_forever, daemon=True).start()

import consulta_llm, chatbot_memoria, bot_telegram

# ---- Groq simulado: devuelve un plan según el texto, usando el historial como lo haría el modelo
def hoy_mas(dias):
    d = date.today() + timedelta(days=dias)
    while d.weekday() == 6: d += timedelta(days=1)
    return d.isoformat()
FECHA = hoy_mas(3)
def vacio(**k): return {"accion": "", "codigo": "", "cliente": "", "servicio": "", "fecha": "", "hora": "", "respuesta": "", **k}
llamadas_groq = []
def groq_falso(mensajes, clave, modelo):
    llamadas_groq.append(mensajes)
    previos = " ".join(m["content"] for m in mensajes[1:-1] if m["role"] == "user").lower()
    t = mensajes[-1]["content"].lower()
    if "servicios" in t or "cuánto cuestan" in t: return vacio(accion="servicios")
    if "disponibilidad" in t or "horas" in t:
        return vacio(accion="disponibilidad", servicio="alineacion_balanceo" if "alineaci" in t else "", fecha=FECHA if "fecha" in t else "")
    if t.strip() == FECHA:  # solo el dato que faltaba
        serv = "alineacion_balanceo" if "alineaci" in previos else ""
        return vacio(accion="disponibilidad", servicio=serv, fecha=FECHA)
    if "reservar" in t: return vacio(accion="crear", cliente="Camilo", servicio="cambio_aceite", fecha=FECHA, hora="10:00")
    if "cancela" in t: return vacio(accion="cancelar", cliente="Camilo")
    if "muéstrame" in t: return vacio(accion="listar", cliente="Camilo")
    return vacio(accion="conversar", respuesta="Hola, ¿en qué te ayudo?")
chatbot_memoria.consultar_groq = groq_falso

# ---- Espías: ninguna escritura
escrituras = []
original = consulta_llm.ejecutar
def espia(plan, confirmar=False):
    if confirmar: escrituras.append(plan)
    return original(plan, confirmar)
chatbot_memoria.ejecutar = espia; consulta_llm.ejecutar = espia

# ---- Objetos de Telegram simulados
class Msg:
    def __init__(self, texto=None): self.text, self.respuestas = texto, []
    async def reply_text(self, t): self.respuestas.append(t)
class Upd:
    def __init__(self, texto=None): self.effective_message = Msg(texto)
class Ctx:
    def __init__(self, clave="k", modelo="m"):
        self.chat_data, self.bot_data = {}, {"clave_modelo": clave, "modelo": modelo}
def run(c): return asyncio.run(c)
def decir(ctx, texto):
    u = Upd(texto)
    run(bot_telegram.iniciar(u, ctx) if texto == "/start" else bot_telegram.ayuda(u, ctx) if texto == "/help"
        else bot_telegram.reiniciar(u, ctx) if texto == "/reset" else bot_telegram.recibir_texto(u, ctx))
    return "\n".join(u.effective_message.respuestas)

fallos = []
def ok(nombre, condicion, detalle=""):
    print(("OK   " if condicion else "FALLA"), nombre)
    if not condicion: fallos.append(nombre); print("     ->", detalle)

c = Ctx()
r = decir(c, "/start"); ok("/start: bienvenida de LuxDrive y historial creado", "LuxDrive Taller" in r and "historial" in c.chat_data, r)
n = len(c.chat_data["historial"]); r = decir(c, "/start"); ok("/start repetido conserva el historial", len(c.chat_data["historial"]) == n)
r = decir(c, "/help"); ok("/help muestra comandos y ejemplos", "/reset" in r and "disponibilidad" in r, r)
r = decir(c, "¿Qué servicios tienen y cuánto cuestan?"); print(r)
ok("servicios: texto plano con nombre, precio y duración", "Cambio de aceite y filtro: $180.000 COP" in r and "30 minutos" in r and "**" not in r and "`" not in r, r)
ok("servicios coinciden con la API", all(d["nombre"] in r for d in NEGOCIO["servicios"].values()))
r = decir(c, f"Disponibilidad de alineación y balanceo, fecha {FECHA}"); print(r)
esperadas = agenda.disponibilidad(FECHA, "alineacion_balanceo")["horas"]
ok("disponibilidad: usa servicio y fecha pedidos y coincide con la API", all(h in r for h in esperadas) and FECHA in r and "Alineación y balanceo" in r and "**" not in r, r)
ok("disponibilidad: sin invitación a reservar", "reservamos" not in r, r)
c2 = Ctx(); r1 = decir(c2, "Disponibilidad de alineación y balanceo"); print(r1)
ok("consulta incompleta pide el dato que falta", "fecha" in r1.lower(), r1)
r2 = decir(c2, FECHA); ok("completa el dato en otro mensaje con el mismo contexto", FECHA in r2 and "Alineación y balanceo" in r2, r2)
c3 = Ctx(); decir(c3, "Disponibilidad de alineación y balanceo"); decir(c3, "/start")
r = decir(c3, FECHA); ok("/start a mitad de la consulta no pierde el contexto", FECHA in r and "Alineación y balanceo" in r, r)
c4 = Ctx(); decir(c4, "Disponibilidad de alineación y balanceo"); decir(c4, "/reset")
r = decir(c4, FECHA); ok("/reset: el dato suelto ya no completa la solicitud", "necesito el servicio" in r and "Horas disponibles" not in r, r)
c5 = Ctx(); decir(c5, "/start"); antes = list(c5.chat_data["historial"])
r = decir(c5, "Quiero reservar un cambio de aceite para Camilo"); print(r)
ok("crear: informa que NO se ejecutó", "NO se ejecutó" in r and "Cambio de aceite" in r, r)
ok("crear: no menciona botones de Gradio ni markdown", "Pulsa" not in r and "Confirmar operación" not in r and "**" not in r, r)
ok("crear: nunca se llamó ejecutar(confirmar=True) y no hay reservas", not escrituras and agenda.listar() == [], escrituras)
r = decir(c5, "sí, confírmala"); ok("un «sí» posterior tampoco ejecuta nada", not escrituras and agenda.listar() == [] and "NO se ejecutó" in r or "Hola" in r, r)
c6 = Ctx(); decir(c6, "/start"); h6 = list(c6.chat_data["historial"])
r = decir(c6, "cancela la reserva de Camilo"); ok("cancelar: no se ejecuta y no se arrastra al historial", "no se ejecutó nada" in r and c6.chat_data["historial"] == h6, r)
r = decir(c6, "muéstrame las reservas de Camilo"); ok("listar: bloqueado por privacidad", "no se ejecutó nada" in r and c6.chat_data["historial"] == h6, r)
r = decir(Ctx(clave="", modelo=""), "hola"); ok("sin GROQ configurado avisa", "Falta configurar" in r, r)
async def foto(): u = Upd(); await bot_telegram.otro_mensaje(u, Ctx()); return u.effective_message.respuestas[0]
ok("foto: pide texto", "mensaje de texto" in run(foto()))
async def cmd(): u = Upd("/precio"); await bot_telegram.comando_desconocido(u, Ctx()); return u.effective_message.respuestas[0]
ok("comando desconocido orienta", "/start" in run(cmd()) and "/reset" in run(cmd()))
def groq_401(*a): raise HTTPError("u", 401, "x", {}, None)
chatbot_memoria.consultar_groq = groq_401
r = decir(Ctx(), "hola"); ok("error HTTP de Groq: mensaje controlado", "rechazó" in r, r)
chatbot_memoria.consultar_groq = groq_falso
servidor.shutdown(); servidor.server_close()
r = decir(Ctx(), "¿Qué servicios tienen?"); ok("FastAPI apagada: informa el fallo y no inventa", "No pude conectar" in r and "$" not in r, r)
async def largo(): m = Msg(); await bot_telegram.enviar_texto(m, "x" * 9000); return [len(t) for t in m.respuestas]
ok("mensajes largos se dividen en bloques de hasta 4000", run(largo()) == [4000, 4000, 1000], run(largo()))
print("\nRESULTADO:", "TODO OK" if not fallos else f"FALLARON {fallos}")
sys.exit(1 if fallos else 0)
