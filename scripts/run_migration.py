"""Aplica un archivo .sql de migrations/ usando las credenciales de .env.

Existe porque en Windows no siempre hay cliente `mysql` en el PATH, que es lo
que asume el README. Ejecuta sentencia por sentencia y trata como
no-op los errores de "ya existe" (duplicate key/column/constraint), asi la
migracion es idempotente igual que los `IF NOT EXISTS` del propio SQL.

Entiende los bloques `DELIMITER $$ ... $$`, asi que tambien sirve para
importar db/schema.sql completo, con su trigger.

    python scripts/run_migration.py db/schema.sql
    python scripts/run_migration.py migrations/2026-10-07_sedes_planes_roles.sql

Toma las credenciales del entorno, y las variables del shell tienen prioridad
sobre el .env: para apuntar a otra base no hace falta tocar el .env local.
"""

from __future__ import annotations

import os
import sys

import mysql.connector
from dotenv import load_dotenv

# Errores que significan "ya estaba aplicado", no un fallo real.
# 1050 tabla, 1060 columna, 1061 clave, 1022/1826/121 restriccion, 1091 no
# existe lo que se iba a borrar, 1359 trigger.
#
# Deliberadamente NO incluye 1062 (fila duplicada): una migracion repetida
# puede volver a crear una columna sin consecuencias, pero una fila duplicada
# significa que se esta reimportando datos sobre datos, y eso hay que verlo,
# no silenciarlo.
_YA_EXISTE = {1050, 1060, 1061, 1022, 1826, 121, 1091, 1359}


def _ya_aplicada(exc: mysql.connector.Error) -> bool:
    if exc.errno in _YA_EXISTE:
        return True
    # MariaDB envuelve "constraint duplicada" en 1005 + errno 121 en el mensaje.
    return exc.errno == 1005 and ("121" in (exc.msg or "") or "Duplicate key" in (exc.msg or ""))


def _sentencias(sql: str):
    """Divide el archivo en sentencias, respetando los bloques DELIMITER.

    Se recorre linea a linea y se corta donde termina el delimitador vigente,
    en vez de partir el texto por ';'. La diferencia importa con los triggers:
    su cuerpo lleva ';' dentro (un SET, un SELECT, un SIGNAL), y por eso los
    volcados los envuelven en `DELIMITER $$ ... $$ DELIMITER ;`. Partiendo por
    ';' el trigger llegaria al servidor troceado y la importacion fallaria a
    mitad, dejando la base a medio crear.

    `DELIMITER` no es SQL: es una instruccion del cliente `mysql`. Se
    interpreta aqui y no se envia al servidor, que la rechazaria.

    ponytail: el corte es por final de linea, no un analizador de SQL. Una
    sentencia que termine en medio de una linea, o un ';' dentro de una cadena
    con el delimitador justo al final de la linea, se partirian mal. Los
    volcados de phpMyAdmin y las migraciones del repo escriben una sentencia
    por linea o la cierran al final, asi que no se da. Si algun dia hace falta
    importar SQL escrito a mano de otra procedencia, el reemplazo es
    sqlparse.split().
    """
    delimitador = ";"
    acumulado: list[str] = []

    def _vaciar():
        texto = "\n".join(acumulado).strip()
        acumulado.clear()
        return texto

    for linea in sql.splitlines():
        desnuda = linea.strip()

        if desnuda.upper().startswith("DELIMITER "):
            # Lo que quedara pendiente se cierra antes de cambiar de delimitador.
            pendiente = _vaciar()
            if pendiente:
                yield pendiente
            delimitador = desnuda.split(None, 1)[1].strip()
            continue

        # Comentarios de linea completa y lineas en blanco: no aportan nada.
        # Los comentarios condicionales /*!40101 ... */; si se envian: el
        # servidor decide si los ejecuta segun su version.
        if not desnuda or desnuda.startswith("--"):
            continue

        acumulado.append(linea)

        if desnuda.endswith(delimitador):
            completo = _vaciar()
            completo = completo[: -len(delimitador)].strip()
            if completo:
                yield completo

    resto = _vaciar()
    if resto:
        yield resto


def main(ruta: str) -> int:
    load_dotenv()
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT") or 3306),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        autocommit=True,
    )
    with open(ruta, "r", encoding="utf-8") as fh:
        sql = fh.read()

    cur = conn.cursor()
    aplicadas = omitidas = 0
    try:
        for sentencia in _sentencias(sql):
            try:
                cur.execute(sentencia)
                cur.fetchall() if cur.with_rows else None
                aplicadas += 1
            except mysql.connector.Error as exc:
                if _ya_aplicada(exc):
                    omitidas += 1
                    continue
                print(f"ERROR {exc.errno}: {exc.msg}\n  en: {sentencia[:120]}...")
                return 1
    finally:
        cur.close()
        conn.close()

    print(f"OK {ruta}: {aplicadas} sentencias aplicadas, {omitidas} ya existian.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
