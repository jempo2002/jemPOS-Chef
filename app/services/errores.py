"""Error de negocio con su codigo HTTP, para los servicios de salon y caja."""
from __future__ import annotations


class ErrorServicio(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class NoEncontrado(ErrorServicio):
    def __init__(self, msg: str = "Registro no encontrado."):
        super().__init__(msg, 404)


class Conflicto(ErrorServicio):
    def __init__(self, msg: str):
        super().__init__(msg, 409)
