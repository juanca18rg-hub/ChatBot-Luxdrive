"""API de reservas de LuxDrive Taller. Guarda las reservas en SQLite (reservas.db)."""

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from almacen_sqlite import AgendaSQLite, ConflictoReserva, ReservaNoEncontrada

BASE = Path(__file__).resolve().parent
NEGOCIO = json.loads((BASE / "negocio.json").read_text(encoding="utf-8-sig"))
SERVICIOS = NEGOCIO["servicios"]
HORARIO = NEGOCIO["horario"]

agenda = AgendaSQLite(
    BASE / "reservas.db",
    SERVICIOS,
    apertura=HORARIO["apertura"],
    cierre=HORARIO["cierre"],
    dias=HORARIO["dias"],
    paso=HORARIO["paso"],
)
app = FastAPI(title="LuxDrive Taller · Reservas")


class ReservaNueva(BaseModel):
    cliente: str = Field(min_length=2)
    servicio: str
    fecha: date
    hora: str
    confirmada: bool


class CambioReserva(BaseModel):
    servicio: str
    fecha: date
    hora: str


def responder(funcion, *args):
    """Convierte los errores del almacén en respuestas HTTP."""
    try:
        return funcion(*args)
    except ReservaNoEncontrada as error:
        raise HTTPException(404, str(error)) from error
    except ConflictoReserva as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/negocio")
def negocio():
    return NEGOCIO


@app.get("/api/servicios")
def servicios():
    return SERVICIOS


@app.get("/api/disponibilidad")
def disponibilidad(fecha: date, servicio: str, excluir: str = ""):
    # "excluir" permite ignorar una reserva propia al calcular un cambio de hora.
    return responder(agenda.disponibilidad, fecha.isoformat(), servicio, excluir)


@app.get("/api/reservas")
def reservas():
    return agenda.listar()


@app.get("/api/reservas/{codigo}")
def consultar(codigo: str):
    return responder(agenda.consultar, codigo)


@app.post("/api/reservas", status_code=201)
def crear(datos: ReservaNueva):
    if not datos.confirmada:
        raise HTTPException(400, "La operación debe estar confirmada")
    return responder(
        agenda.crear, datos.cliente, datos.servicio, datos.fecha.isoformat(), datos.hora
    )


@app.put("/api/reservas/{codigo}")
def modificar(codigo: str, datos: CambioReserva):
    return responder(
        agenda.modificar, codigo, datos.servicio, datos.fecha.isoformat(), datos.hora
    )


@app.delete("/api/reservas/{codigo}")
def cancelar(codigo: str):
    # Cancelar cambia el estado a "cancelada": la fila se conserva como histórico.
    return responder(agenda.cancelar, codigo)
