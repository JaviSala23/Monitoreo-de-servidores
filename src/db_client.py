"""
Cliente de bases de datos remotas con túnel SSH.
Soporta: PostgreSQL, MySQL, MongoDB, Redis.
Las importaciones de cada driver son lazy: sólo fallan si usas ese motor concreto.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DEFAULT_PORTS: Dict[str, int] = {
    "postgresql": 5432,
    "mysql":      3306,
    "mongodb":    27017,
    "redis":      6379,
}


@dataclass
class DBResult:
    columns:  List[str]   = field(default_factory=list)
    rows:     List[tuple] = field(default_factory=list)
    affected: int         = 0
    error:    Optional[str] = None
    message:  str         = ""


class DBClient:
    """Conexión a BD remota a través de túnel SSH."""

    def __init__(self, server) -> None:   # server: database.Server
        self._server = server
        self._tunnel = None
        self._conn   = None
        self._type   = server.db_type.lower()

    @property
    def _eff_port(self) -> int:
        return self._server.db_port or _DEFAULT_PORTS.get(self._type, 5432)

    # ── ciclo de vida ──────────────────────────────────────────────────────

    def connect(self) -> Optional[str]:
        """Abre túnel SSH y conexión a la BD. Devuelve mensaje de error o None."""
        try:
            self._open_tunnel()
            self._open_conn()
            return None
        except Exception as exc:
            self.disconnect()
            return str(exc)

    def disconnect(self) -> None:
        try:
            if self._conn is not None:
                if self._type in ("postgresql", "mysql"):
                    self._conn.close()
                elif self._type == "mongodb":
                    self._conn.close()
                # redis-py no necesita close explícito
        except Exception:
            pass
        self._conn = None

        try:
            if self._tunnel is not None:
                self._tunnel.stop()
        except Exception:
            pass
        self._tunnel = None

    def is_connected(self) -> bool:
        return self._conn is not None

    # ── tunnel + conexión ──────────────────────────────────────────────────

    def _open_tunnel(self) -> None:
        from sshtunnel import SSHTunnelForwarder   # type: ignore
        self._tunnel = SSHTunnelForwarder(
            (self._server.host, self._server.port),
            ssh_username=self._server.username,
            ssh_password=self._server.password,
            remote_bind_address=("127.0.0.1", self._eff_port),
        )
        self._tunnel.start()

    def _open_conn(self) -> None:
        lp = self._tunnel.local_bind_port

        if self._type == "postgresql":
            import psycopg2   # type: ignore
            self._conn = psycopg2.connect(
                host="127.0.0.1", port=lp,
                dbname=self._server.db_name or "postgres",
                user=self._server.db_user,
                password=self._server.db_password,
                connect_timeout=10,
            )
            self._conn.autocommit = False

        elif self._type == "mysql":
            import pymysql   # type: ignore
            self._conn = pymysql.connect(
                host="127.0.0.1", port=lp,
                db=self._server.db_name or None,
                user=self._server.db_user,
                password=self._server.db_password,
                connect_timeout=10,
                charset="utf8mb4",
            )

        elif self._type == "mongodb":
            from pymongo import MongoClient   # type: ignore
            user = self._server.db_user
            pw   = self._server.db_password
            db   = self._server.db_name or "admin"
            if user and pw:
                uri = f"mongodb://{user}:{pw}@127.0.0.1:{lp}/{db}"
            else:
                uri = f"mongodb://127.0.0.1:{lp}/{db}"
            self._conn = MongoClient(uri, serverSelectionTimeoutMS=10_000)
            self._conn.server_info()   # lanza excepción si falla

        elif self._type == "redis":
            import redis as rlib   # type: ignore
            self._conn = rlib.Redis(
                host="127.0.0.1", port=lp,
                password=self._server.db_password or None,
                socket_timeout=10,
                decode_responses=True,
            )
            self._conn.ping()

        else:
            raise ValueError(f"Tipo de BD no soportado: {self._type!r}")

    # ── operaciones ────────────────────────────────────────────────────────

    def list_tables(self) -> DBResult:
        """Devuelve tablas/colecciones/keys del servidor."""
        try:
            if self._type == "postgresql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT table_schema, table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                    "ORDER BY table_schema, table_name"
                )
                return DBResult(columns=["Schema", "Tabla", "Tipo"], rows=cur.fetchall())

            elif self._type == "mysql":
                cur = self._conn.cursor()
                cur.execute("SHOW FULL TABLES")
                return DBResult(columns=["Tabla", "Tipo"], rows=cur.fetchall())

            elif self._type == "mongodb":
                db   = self._conn[self._server.db_name or "admin"]
                cols = db.list_collection_names()
                return DBResult(
                    columns=["Colección"],
                    rows=[(c,) for c in sorted(cols)],
                )

            elif self._type == "redis":
                keys  = self._conn.keys("*")[:200]
                types = [self._conn.type(k) for k in keys]
                return DBResult(columns=["Key", "Tipo"], rows=list(zip(keys, types)))

        except Exception as exc:
            return DBResult(error=str(exc))

        return DBResult()

    def get_stats(self) -> DBResult:
        """Estadísticas básicas de la BD."""
        try:
            if self._type == "postgresql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT datname, pg_size_pretty(pg_database_size(datname)), "
                    "numbackends FROM pg_stat_database WHERE datname = current_database()"
                )
                return DBResult(
                    columns=["Base de datos", "Tamaño", "Conexiones"],
                    rows=cur.fetchall(),
                )

            elif self._type == "mysql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT table_schema, "
                    "ROUND(SUM(data_length + index_length) / 1024 / 1024, 2), COUNT(*) "
                    "FROM information_schema.tables WHERE table_schema = DATABASE() "
                    "GROUP BY table_schema"
                )
                return DBResult(
                    columns=["Base de datos", "Tamaño (MB)", "Tablas"],
                    rows=cur.fetchall(),
                )

            elif self._type == "mongodb":
                db    = self._conn[self._server.db_name or "admin"]
                st    = db.command("dbStats")
                rows  = [
                    ("Colecciones",    st.get("collections", "?")),
                    ("Objetos",        st.get("objects",     "?")),
                    ("Tamaño datos",   f"{st.get('dataSize',  0) / 1024:.1f} KB"),
                    ("Tamaño índices", f"{st.get('indexSize', 0) / 1024:.1f} KB"),
                ]
                return DBResult(columns=["Métrica", "Valor"], rows=rows)

            elif self._type == "redis":
                info = self._conn.info()
                db0  = info.get("db0", {})
                rows = [
                    ("Versión Redis",  info.get("redis_version", "?")),
                    ("Claves (db0)",   db0.get("keys", "0")),
                    ("Memoria usada",  info.get("used_memory_human", "?")),
                    ("Memoria pico",   info.get("used_memory_peak_human", "?")),
                    ("Clientes",       info.get("connected_clients", "?")),
                    ("Uptime",         f"{info.get('uptime_in_days', 0)} días"),
                ]
                return DBResult(columns=["Métrica", "Valor"], rows=rows)

        except Exception as exc:
            return DBResult(error=str(exc))

        return DBResult()

    def execute_query(self, query: str) -> DBResult:
        """Ejecuta SQL / comando y devuelve resultados (máx. 500 filas)."""
        try:
            if self._type == "postgresql":
                cur = self._conn.cursor()
                cur.execute(query)
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    return DBResult(columns=cols, rows=cur.fetchmany(500))
                self._conn.commit()
                return DBResult(affected=cur.rowcount,
                                message=f"{cur.rowcount} fila(s) afectadas")

            elif self._type == "mysql":
                cur = self._conn.cursor()
                cur.execute(query)
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    return DBResult(columns=cols, rows=cur.fetchmany(500))
                self._conn.commit()
                return DBResult(affected=cur.rowcount,
                                message=f"{cur.rowcount} fila(s) afectadas")

            elif self._type == "mongodb":
                # query = nombre de colección; muestra hasta 100 documentos
                db   = self._conn[self._server.db_name or "admin"]
                docs = list(db[query.strip()].find().limit(100))
                if not docs:
                    return DBResult(message="Colección vacía o no encontrada")
                cols = list(docs[0].keys())
                rows = [tuple(str(d.get(c, "")) for c in cols) for d in docs]
                return DBResult(columns=cols, rows=rows)

            elif self._type == "redis":
                parts  = query.strip().split(maxsplit=1)
                cmd    = parts[0].upper()
                args   = parts[1].split() if len(parts) > 1 else []
                result = self._conn.execute_command(cmd, *args)
                if isinstance(result, list):
                    return DBResult(columns=["Resultado"],
                                    rows=[(r,) for r in result[:200]])
                return DBResult(columns=["Resultado"], rows=[(str(result),)])

        except Exception as exc:
            return DBResult(error=str(exc))

        return DBResult()
