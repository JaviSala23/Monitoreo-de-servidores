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
    description: str  = ""
    # Umbrales de alerta (0.0 = desactivado)
    cpu_alert:  float = 85.0
    ram_alert:  float = 90.0
    disk_alert: float = 90.0
    # Configuración de base de datos remota
    db_type:     str = ""    # "postgresql" | "mysql" | "mongodb" | "redis" | ""
    db_port:     int = 0     # 0 = usar el puerto por defecto del motor
    db_name:     str = ""
    db_user:     str = ""
    db_password: str = ""    # texto plano en uso, cifrado en disco
    monitoring_enabled: bool = True  # False = monitoreo pausado


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
                description TEXT    NOT NULL DEFAULT '',
                cpu_alert   REAL    NOT NULL DEFAULT 85.0,
                ram_alert   REAL    NOT NULL DEFAULT 90.0,
                disk_alert  REAL    NOT NULL DEFAULT 90.0,
                db_type     TEXT    NOT NULL DEFAULT '',
                db_port     INTEGER NOT NULL DEFAULT 0,
                db_name     TEXT    NOT NULL DEFAULT '',
                db_user     TEXT    NOT NULL DEFAULT '',
                db_password TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        # Migración automática: agrega columnas si la DB ya existía
        existing = {row[1] for row in conn.execute("PRAGMA table_info(servers)")}
        for col, dfn in [
            ("cpu_alert",   "REAL    NOT NULL DEFAULT 85.0"),
            ("ram_alert",   "REAL    NOT NULL DEFAULT 90.0"),
            ("disk_alert",  "REAL    NOT NULL DEFAULT 90.0"),
            ("db_type",            "TEXT    NOT NULL DEFAULT ''"),
            ("db_port",            "INTEGER NOT NULL DEFAULT 0"),
            ("db_name",            "TEXT    NOT NULL DEFAULT ''"),
            ("db_user",            "TEXT    NOT NULL DEFAULT ''"),
            ("db_password",        "TEXT    NOT NULL DEFAULT ''"),
            ("monitoring_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("sort_order",         "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE servers ADD COLUMN {col} {dfn}")
                if col == "sort_order":
                    conn.execute("UPDATE servers SET sort_order = rowid")
        conn.commit()


def get_all_servers() -> List[Server]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, host, port, username, password, description, "
            "cpu_alert, ram_alert, disk_alert, "
            "db_type, db_port, db_name, db_user, db_password, monitoring_enabled "
            "FROM servers ORDER BY sort_order, name"
        ).fetchall()
    return [
        Server(
            id=r[0], name=r[1], host=r[2], port=r[3],
            username=r[4], password=decrypt_password(r[5]), description=r[6],
            cpu_alert=float(r[7]), ram_alert=float(r[8]), disk_alert=float(r[9]),
            db_type=r[10] or "", db_port=int(r[11]),
            db_name=r[12] or "", db_user=r[13] or "",
            db_password=decrypt_password(r[14]) if r[14] else "",
            monitoring_enabled=bool(r[15]),
        )
        for r in rows
    ]


def add_server(server: Server) -> int:
    with _get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO servers "
            "(name, host, port, username, password, description,"
            " db_type, db_port, db_name, db_user, db_password) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                server.name, server.host, server.port, server.username,
                encrypt_password(server.password), server.description,
                server.db_type, server.db_port, server.db_name,
                server.db_user,
                encrypt_password(server.db_password) if server.db_password else "",
            ),
        )
        conn.commit()
        return cursor.lastrowid


def update_server(server: Server) -> None:
    with _get_connection() as conn:
        conn.execute(
            "UPDATE servers SET "
            "name=?, host=?, port=?, username=?, password=?, description=?,"
            " db_type=?, db_port=?, db_name=?, db_user=?, db_password=? "
            "WHERE id=?",
            (
                server.name, server.host, server.port, server.username,
                encrypt_password(server.password), server.description,
                server.db_type, server.db_port, server.db_name,
                server.db_user,
                encrypt_password(server.db_password) if server.db_password else "",
                server.id,
            ),
        )
        conn.commit()


def delete_server(server_id: int) -> None:
    with _get_connection() as conn:
        conn.execute("DELETE FROM servers WHERE id=?", (server_id,))
        conn.commit()


def set_monitoring_enabled(server_id: int, enabled: bool) -> None:
    with _get_connection() as conn:
        conn.execute(
            "UPDATE servers SET monitoring_enabled=? WHERE id=?",
            (1 if enabled else 0, server_id),
        )
        conn.commit()


def save_server_order(server_ids: List[int]) -> None:
    with _get_connection() as conn:
        for i, sid in enumerate(server_ids):
            conn.execute("UPDATE servers SET sort_order=? WHERE id=?", (i, sid))
        conn.commit()
