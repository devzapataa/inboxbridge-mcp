"""Comandos que el dueño manda por WhatsApp para gestionar sus seguimientos.

Solo tocan la memoria de seguimientos, nunca el correo: aunque alguien lograra mandar un
comando, lo peor que podría hacer es marcar un recordatorio como hecho.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from inboxbridge.errores import ErrorCorreo
from inboxbridge.seguimientos import RepoSeguimientos

AYUDA = (
    "Comandos: *lista* · *hecho 12* · *descartar 12* · *posponer 12 lunes* "
    "(también: mañana, 3d o una fecha como 15/10)"
)
_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_MAX_LISTA = 15

Accion = Literal["lista", "hecho", "descartar", "posponer", "ayuda"]


@dataclass(frozen=True)
class Comando:
    accion: Accion
    numeros: tuple[int, ...] = ()
    hasta: date | None = None


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip(" .!")


def _fecha(arg: str, hoy: date) -> date | None:
    if arg in ("", "manana"):
        return hoy + timedelta(days=1)
    if arg in _DIAS:
        # El próximo día con ese nombre, nunca hoy.
        return hoy + timedelta(days=(_DIAS.index(arg) - hoy.weekday() - 1) % 7 + 1)
    if m := re.fullmatch(r"(\d{1,3})\s*d(?:ias?)?", arg):
        return hoy + timedelta(days=int(m.group(1)))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})", arg):
        try:
            fecha = date(hoy.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return fecha if fecha > hoy else fecha.replace(year=hoy.year + 1)
    return None


def interpretar(texto: str, hoy: date) -> Comando | None:
    t = _normalizar(texto)
    if t in ("lista", "pendientes", "ver"):
        return Comando("lista")
    if t in ("ayuda", "help", "?"):
        return Comando("ayuda")
    if m := re.fullmatch(r"(?:hecho|listo|ok|descartar|no) ((?:#?\d+[ ,]*)+)", t):
        verbo = t.split(" ", 1)[0]
        accion: Accion = "descartar" if verbo in ("descartar", "no") else "hecho"
        numeros = tuple(dict.fromkeys(int(n) for n in re.findall(r"\d+", m.group(1))))
        return Comando(accion, numeros)
    if m := re.fullmatch(r"(?:posponer|recordar) #?(\d+)(?: (.+))?", t):
        hasta = _fecha((m.group(2) or "").strip(), hoy)
        return Comando("posponer", (int(m.group(1)),), hasta) if hasta else None
    return None


def _dia(fecha: date) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha.day}-{_MESES[fecha.month - 1]}".replace(
        "miercoles", "miércoles"
    ).replace("sabado", "sábado")


async def ejecutar(texto: str, repo: RepoSeguimientos) -> str:
    """Interpreta y aplica el comando, y devuelve la respuesta para el WhatsApp."""
    hoy = repo.hoy()
    comando = interpretar(texto, hoy)
    if comando is None:
        return f"No entendí. {AYUDA}"
    if comando.accion == "ayuda":
        return AYUDA
    if comando.accion == "lista":
        activos = await repo.listar(solo_activos=True)
        if not activos:
            return "✅ No tienes seguimientos pendientes."
        lineas = []
        for s in activos[:_MAX_LISTA]:
            linea = f"#{s.numero} {s.titulo}"
            if s.vence_en:
                linea += f" · vence {_dia(s.vence_en.date())}"
            if s.recordar_desde and s.recordar_desde > hoy:
                linea += f" · pospuesto hasta {_dia(s.recordar_desde)}"
            lineas.append(linea)
        extra = len(activos) - _MAX_LISTA
        return "\n".join(lineas + ([f"…y {extra} más"] if extra > 0 else []))

    respuestas = []
    for numero in comando.numeros:
        try:
            if comando.accion == "posponer":
                hasta = comando.hasta or hoy + timedelta(days=1)
                s = await repo.actualizar(numero, recordar_desde=hasta)
                respuestas.append(f"⏰ #{numero} vuelve el {_dia(hasta)}: {s.titulo}")
            else:
                s = await repo.actualizar(numero, estado=comando.accion)  # type: ignore[arg-type]
                marca = "✅" if comando.accion == "hecho" else "🗑️"
                respuestas.append(f"{marca} #{numero} {comando.accion}: {s.titulo}")
        except ErrorCorreo as e:
            respuestas.append(str(e))
    return "\n".join(respuestas)
