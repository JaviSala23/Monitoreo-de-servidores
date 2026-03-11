"""
Módulo de base de datos SQLite para almacenar configuración de servidores.
Las contraseñas se guardan cifradas con Fernet.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .crypto import decrypt_password, encrypt_password

_DB_PATH = Path.home() / ".server_monitor" / "servers.db"


@dataclass
class Server:
    id: Optional[int]
    name: str
    host: str
    port: int
    username: str
    password: str        # Texto plano cuando está en uso
    description: str = ""


def _get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(_DB_PATH))


def init_db() -> None:
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS servers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                host        TEXT    NOT NULL,
                port        INTEGER NOT NULL DEFAULT 22,
                username    TEXT    NOT NULL,
                password    TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()


def get_all_servers() -> List[Server]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, host, port, username, password, description FROM servers ORDER BY name"
        ).fetchall()
    return [
        Server(
            id=r[0], name=r[1], host=r[2], port=r[3],
            username=r[4], password=decrypt_password(r[5]), description=r[6],
        )
        for r in rows
    ]


def add_server(server: Server) -> int:
    with _get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO servers (name, host, port, username, password, description) VALUES (?, ?, ?, ?, ?, ?)",
            (server.name, server.host, server.port, server.username,
             encrypt_password(server.password), server.description),
        )
        conn.commit()
        return cursor.lastrowid


def update_server(server: Server) -> None:
    with _get_connection() as conn:
        conn.execute(
            "UPDATE servers SET name=?, host=?, port=?, username=?, password=?, description=? WHERE id=?",
            (server.name, server.host, server.port, server.username,
             encrypt_password(server.password), server.description, server.id),
        )
        conn.commit()


def delete_server(server_id: int) -> None:
    with _get_connection() as conn:
        conn.execute("DELETE FROM servers WHERE id=?", (server_id,))
        conn.commit()
