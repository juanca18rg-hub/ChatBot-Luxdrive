"""Reservas SQLite para una agenda. Cada operación utiliza su propia conexión."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4


class ConflictoReserva(ValueError):
    pass


class ReservaNoEncontrada(ValueError):
    pass


def minutos(hora):
    if not isinstance(hora, str) or not re.fullmatch(r"\d{2}:\d{2}", hora):
        raise ValueError("Usa una hora HH:MM")
    horas, minutos_hora = map(int, hora.split(":"))
    if not 0 <= horas <= 23 or not 0 <= minutos_hora <= 59:
        raise ValueError("Hora inválida")
    return horas * 60 + minutos_hora


def texto_hora(valor):
    return f"{valor // 60:02d}:{valor % 60:02d}"


class AgendaSQLite:
    def __init__(self, ruta, servicios, apertura="09:00", cierre="18:00",
                 dias=(0, 1, 2, 3, 4, 5), paso=30):
        self.ruta = Path(ruta).resolve()
        self.servicios = servicios
        self.apertura, self.cierre = minutos(apertura), minutos(cierre)
        self.dias, self.paso = tuple(dias), paso
        if self.apertura >= self.cierre or not isinstance(paso, int) or paso <= 0:
            raise ValueError("Revisa el horario y el paso de la agenda")
        with self.conexion(escritura=True) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS reservas (
                codigo TEXT PRIMARY KEY,
                cliente TEXT NOT NULL,
                servicio TEXT NOT NULL,
                fecha TEXT NOT NULL,
                hora TEXT NOT NULL,
                duracion_minutos INTEGER NOT NULL CHECK (duracion_minutos > 0),
                estado TEXT NOT NULL CHECK (estado IN ('activa', 'cancelada'))
            )""")
            con.execute("CREATE INDEX IF NOT EXISTS idx_fecha_estado ON reservas(fecha, estado)")

    @contextmanager
    def conexion(self, escritura=False):
        con = sqlite3.connect(self.ruta, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            if escritura:
                # La comprobación del horario y el guardado van en la misma transacción.
                con.execute("BEGIN IMMEDIATE")
            yield con
            if escritura:
                con.commit()
        except Exception:
            if escritura:
                con.rollback()
            raise
        finally:
            con.close()

    def duracion(self, servicio):
        if servicio not in self.servicios:
            raise ValueError("Servicio desconocido")
        valor = self.servicios[servicio].get("duracion_minutos")
        if type(valor) is not int or valor <= 0:
            raise ValueError("El catálogo debe indicar duracion_minutos como entero positivo")
        return valor

    def validar_fecha(self, fecha, permitir_pasado=False):
        valor = date.fromisoformat(fecha)
        if valor.isoformat() != fecha:
            raise ValueError("Usa fecha AAAA-MM-DD")
        if not permitir_pasado and valor < date.today():
            raise ValueError("No se puede reservar en una fecha pasada")
        if valor.weekday() not in self.dias:
            raise ValueError("El negocio está cerrado ese día")
        return valor

    def validar_intervalo(self, fecha, hora, duracion, permitir_pasado=False):
        self.validar_fecha(fecha, permitir_pasado)
        inicio = minutos(hora)
        if inicio < self.apertura or inicio + duracion > self.cierre:
            raise ValueError("La atención debe terminar dentro del horario del negocio")
        if (inicio - self.apertura) % self.paso:
            raise ValueError(f"Selecciona una hora en pasos de {self.paso} minutos")
        return inicio

    def ocupado(self, con, fecha, inicio, duracion, excluir=""):
        filas = con.execute(
            "SELECT hora, duracion_minutos FROM reservas WHERE fecha=? AND estado='activa' AND codigo<>?",
            (fecha, excluir),
        ).fetchall()
        # Una sola agenda: dos intervalos que se cruzan no pueden estar activos.
        return any(inicio < minutos(fila["hora"]) + fila["duracion_minutos"]
                   and inicio + duracion > minutos(fila["hora"]) for fila in filas)

    def disponibilidad(self, fecha, servicio, excluir=""):
        self.validar_fecha(fecha)
        duracion = self.duracion(servicio)
        with self.conexion() as con:
            horas = [texto_hora(inicio)
                     for inicio in range(self.apertura, self.cierre - duracion + 1, self.paso)
                     if not self.ocupado(con, fecha, inicio, duracion, excluir)]
        return {"fecha": fecha, "servicio": servicio, "duracion_minutos": duracion,
                "horas": horas}

    def listar(self):
        with self.conexion() as con:
            return [dict(fila) for fila in con.execute("SELECT * FROM reservas ORDER BY fecha, hora, codigo")]

    def obtener(self, con, codigo):
        fila = con.execute("SELECT * FROM reservas WHERE codigo=?", (codigo,)).fetchone()
        if fila is None:
            raise ReservaNoEncontrada("Reserva no encontrada")
        return dict(fila)

    def consultar(self, codigo):
        with self.conexion() as con:
            return self.obtener(con, codigo)

    def crear(self, cliente, servicio, fecha, hora):
        if not isinstance(cliente, str) or len(cliente.strip()) < 2:
            raise ValueError("Indica el nombre del cliente")
        duracion = self.duracion(servicio)
        inicio = self.validar_intervalo(fecha, hora, duracion)
        codigo = uuid4().hex[:8].upper()
        with self.conexion(escritura=True) as con:
            if self.ocupado(con, fecha, inicio, duracion):
                raise ConflictoReserva("El intervalo solicitado se cruza con una reserva activa")
            con.execute(
                "INSERT INTO reservas VALUES (?, ?, ?, ?, ?, ?, ?)",
                (codigo, cliente.strip(), servicio, fecha, hora, duracion, "activa"),
            )
            return self.obtener(con, codigo)

    def modificar(self, codigo, servicio, fecha, hora):
        duracion = self.duracion(servicio)
        inicio = self.validar_intervalo(fecha, hora, duracion)
        with self.conexion(escritura=True) as con:
            actual = self.obtener(con, codigo)
            if actual["estado"] != "activa":
                raise ConflictoReserva("La reserva ya no está activa")
            if self.ocupado(con, fecha, inicio, duracion, excluir=codigo):
                raise ConflictoReserva("El nuevo intervalo está ocupado")
            con.execute(
                "UPDATE reservas SET servicio=?, fecha=?, hora=?, duracion_minutos=? WHERE codigo=?",
                (servicio, fecha, hora, duracion, codigo),
            )
            return self.obtener(con, codigo)

    def cancelar(self, codigo):
        with self.conexion(escritura=True) as con:
            actual = self.obtener(con, codigo)
            if actual["estado"] != "activa":
                raise ConflictoReserva("La reserva ya está cancelada")
            con.execute("UPDATE reservas SET estado='cancelada' WHERE codigo=?", (codigo,))
            return self.obtener(con, codigo)

    def importar_json(self, ruta):
        registros = json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
        if isinstance(registros, dict):
            registros = registros.get("reservas")
        if not isinstance(registros, list):
            raise ValueError("Adapta el JSON a una lista de reservas o a {'reservas': [...]}")
        nuevos = existentes = 0
        with self.conexion(escritura=True) as con:
            for registro in registros:
                if not isinstance(registro, dict):
                    raise ValueError("Cada reserva debe ser un objeto")
                try:
                    datos = {campo: registro[campo] for campo in
                             ("codigo", "cliente", "servicio", "fecha", "hora", "estado")}
                except KeyError as error:
                    raise ValueError(f"Adapta el campo que falta: {error}") from error
                if any(not isinstance(valor, str) or not valor.strip() for valor in datos.values()):
                    raise ValueError("Los campos de la reserva deben ser textos no vacíos")
                duracion = registro.get("duracion_minutos", self.duracion(datos["servicio"]))
                if type(duracion) is not int or duracion <= 0:
                    raise ValueError("Duración inválida en el JSON")
                if datos["estado"] not in {"activa", "cancelada"}:
                    raise ValueError("Adapta el estado a activa o cancelada")
                # Importamos el histórico aunque su fecha haya pasado.
                fecha = date.fromisoformat(datos["fecha"])
                if fecha.isoformat() != datos["fecha"]:
                    raise ValueError("Fecha inválida en el JSON")
                inicio = minutos(datos["hora"])
                if inicio + duracion > 24 * 60:
                    raise ValueError("La reserva histórica termina fuera del día")
                fila = {**datos, "duracion_minutos": duracion}
                previa = con.execute("SELECT * FROM reservas WHERE codigo=?", (datos["codigo"],)).fetchone()
                if previa is not None:
                    if dict(previa) != fila:
                        raise ConflictoReserva("El código ya existe con datos distintos; revisa la migración")
                    existentes += 1
                    continue
                if datos["estado"] == "activa" and self.ocupado(con, datos["fecha"], inicio, duracion):
                    raise ConflictoReserva("El JSON contiene un intervalo activo que se cruza con otro")
                con.execute("INSERT INTO reservas VALUES (?, ?, ?, ?, ?, ?, ?)",
                            tuple(fila[campo] for campo in
                                  ("codigo", "cliente", "servicio", "fecha", "hora", "duracion_minutos", "estado")))
                nuevos += 1
        return {"importadas": nuevos, "ya_existentes": existentes}


if __name__ == "__main__":
    import argparse
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Importar reservas JSON a SQLite")
    parser.add_argument("--migrar", required=True, type=Path)
    parser.add_argument("--negocio", type=Path, default=base / "negocio.json")
    parser.add_argument("--base", type=Path, default=base / "reservas.db")
    args = parser.parse_args()
    catalogo = json.loads(args.negocio.read_text(encoding="utf-8-sig"))["servicios"]
    agenda = AgendaSQLite(args.base, catalogo)
    try:
        print(json.dumps(agenda.importar_json(args.migrar), ensure_ascii=False, indent=2))
    except ValueError as error:
        parser.exit(1, f"Migración detenida: {error}\n")
