"""
Cliente de bases de datos remotas con túnel SSH.
Soporta: PostgreSQL, MySQL, MongoDB, Redis.
El túnel se implementa directamente con paramiko (sin sshtunnel),
compatible con paramiko 3.x.
"""
import select
import socket
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import paramiko

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


# ── Túnel SSH puro con paramiko ───────────────────────────────────────────────

class _SSHTunnel:
    """
    Túnel SSH implementado directamente con paramiko.Transport.
    Abre un puerto local libre y reenvía el tráfico a (remote_host, remote_port)
    a través del servidor SSH.
    """

    def __init__(self, ssh_host: str, ssh_port: int,
                 ssh_user: str, ssh_pass: str,
                 remote_host: str, remote_port: int) -> None:
        self._ssh_host    = ssh_host
        self._ssh_port    = ssh_port
        self._ssh_user    = ssh_user
        self._ssh_pass    = ssh_pass
        self._remote_host = remote_host
        self._remote_port = remote_port

        self._transport: Optional[paramiko.Transport] = None
        self._server_sock: Optional[socket.socket]    = None
        self._running = False
        self.local_bind_port: int = 0

    def start(self) -> None:
        self._transport = paramiko.Transport((self._ssh_host, self._ssh_port))
        self._transport.connect(
            username=self._ssh_user,
            password=self._ssh_pass,
        )

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(10)
        self._server_sock.settimeout(1.0)
        self.local_bind_port = self._server_sock.getsockname()[1]

        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            threading.Thread(
                target=self._forward,
                args=(client_sock, addr),
                daemon=True,
            ).start()

    def _forward(self, local_sock: socket.socket, addr) -> None:
        try:
            chan = self._transport.open_channel(
                "direct-tcpip",
                (self._remote_host, self._remote_port),
                addr,
            )
        except Exception:
            local_sock.close()
            return
        try:
            while True:
                r, _, _ = select.select([local_sock, chan], [], [], 5.0)
                if local_sock in r:
                    data = local_sock.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    local_sock.sendall(data)
        except Exception:
            pass
        finally:
            try:
                local_sock.close()
            except Exception:
                pass
            try:
                chan.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass


# ── DBClient ──────────────────────────────────────────────────────────────────

class DBClient:
    """Conexión a BD remota a través de túnel SSH (paramiko nativo)."""

    def __init__(self, server) -> None:   # server: database.Server
        self._server = server
        self._tunnel: Optional[_SSHTunnel] = None
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
        self._tunnel = _SSHTunnel(
            ssh_host=self._server.host,
            ssh_port=self._server.port,
            ssh_user=self._server.username,
            ssh_pass=self._server.password,
            remote_host="127.0.0.1",
            remote_port=self._eff_port,
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

    # ── exploración paginada ───────────────────────────────────────────────

    def describe_table(self, table_name: str):
        """Devuelve (DBResult columnas, DBResult índices)."""
        try:
            if self._type == "postgresql":
                cur = self._conn.cursor()
                cur.execute(
                    """
                    SELECT
                        c.column_name                                               AS "Columna",
                        c.data_type || CASE
                            WHEN c.character_maximum_length IS NOT NULL
                            THEN '(' || c.character_maximum_length || ')'
                            ELSE '' END                                             AS "Tipo",
                        c.is_nullable                                               AS "Nulable",
                        COALESCE((
                            SELECT '✓'
                            FROM information_schema.key_column_usage k
                            JOIN information_schema.table_constraints tc
                                ON k.constraint_name = tc.constraint_name
                               AND k.table_name      = tc.table_name
                            WHERE tc.constraint_type = 'PRIMARY KEY'
                              AND k.table_name        = c.table_name
                              AND k.column_name       = c.column_name
                            LIMIT 1
                        ), '')                                                      AS "PK",
                        COALESCE(c.column_default, '')                             AS "Default"
                    FROM information_schema.columns c
                    WHERE c.table_name = %s
                    ORDER BY c.ordinal_position
                    """,
                    (table_name,),
                )
                cols = DBResult(columns=[d[0] for d in cur.description],
                                rows=cur.fetchall())
                cur.execute(
                    "SELECT indexname AS \"Índice\", indexdef AS \"Definición\" "
                    "FROM pg_indexes WHERE tablename = %s ORDER BY indexname",
                    (table_name,),
                )
                idxs = DBResult(columns=[d[0] for d in cur.description],
                                rows=cur.fetchall())
                return cols, idxs

            elif self._type == "mysql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
                    "COLUMN_KEY, COLUMN_DEFAULT, EXTRA "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (table_name,),
                )
                cols = DBResult(
                    columns=["Columna", "Tipo", "Nulable", "Clave", "Default", "Extra"],
                    rows=cur.fetchall(),
                )
                safe = table_name.replace("`", "")
                cur.execute(f"SHOW INDEX FROM `{safe}`")
                idxs = DBResult(
                    columns=[d[0] for d in cur.description] if cur.description else [],
                    rows=cur.fetchall(),
                )
                return cols, idxs

            elif self._type == "mongodb":
                db   = self._conn[self._server.db_name or "admin"]
                coll = db[table_name]
                doc  = coll.find_one()
                if doc:
                    cols = DBResult(
                        columns=["Campo", "Tipo Python", "Ejemplo"],
                        rows=[(k, type(v).__name__, str(v)[:80])
                              for k, v in doc.items()],
                    )
                else:
                    cols = DBResult(columns=["Info"], rows=[("Colección vacía",)])
                idx_info = coll.index_information()
                idxs = DBResult(
                    columns=["Nombre", "Clave", "Único"],
                    rows=[
                        (name,
                         str(info.get("key", "")),
                         "✓" if info.get("unique") else "")
                        for name, info in idx_info.items()
                    ],
                )
                return cols, idxs

            elif self._type == "redis":
                exists = self._conn.exists(table_name)
                if exists:
                    ktype    = self._conn.type(table_name)
                    ttl      = self._conn.ttl(table_name)
                    try:    enc = self._conn.object("encoding", table_name)
                    except Exception: enc = "?"
                    cols = DBResult(
                        columns=["Propiedad", "Valor"],
                        rows=[("Tipo", ktype), ("TTL (seg)", str(ttl)),
                              ("Encoding", str(enc))],
                    )
                else:
                    cols = DBResult(columns=["Info"], rows=[("Clave no encontrada",)])
                idxs = DBResult(columns=["Nota"], rows=[("Redis no tiene índices",)])
                return cols, idxs

        except Exception as exc:
            err = DBResult(error=str(exc))
            return err, DBResult()

        return DBResult(), DBResult()

    def count_rows(self, table_name: str) -> int:
        """Estimación rápida del número de filas/documentos. -1 si error."""
        try:
            if self._type == "postgresql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
                    (table_name,),
                )
                row = cur.fetchone()
                return max(0, int(row[0])) if row else 0

            elif self._type == "mysql":
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                    (table_name,),
                )
                row = cur.fetchone()
                return max(0, int(row[0])) if row and row[0] is not None else 0

            elif self._type == "mongodb":
                db = self._conn[self._server.db_name or "admin"]
                return db[table_name].estimated_document_count()

            elif self._type == "redis":
                ktype = self._conn.type(table_name)
                if ktype == "list":   return self._conn.llen(table_name)
                if ktype == "hash":   return self._conn.hlen(table_name)
                if ktype == "set":    return self._conn.scard(table_name)
                if ktype == "zset":   return self._conn.zcard(table_name)
                return 1

        except Exception:
            return -1
        return -1

    def browse_table(self, table_name: str,
                     page: int, page_size: int) -> DBResult:
        """Devuelve una página de filas de una tabla."""
        try:
            offset = page * page_size

            if self._type == "postgresql":
                from psycopg2 import sql as pgsql  # type: ignore
                cur = self._conn.cursor()
                cur.execute(
                    pgsql.SQL("SELECT * FROM {} LIMIT %s OFFSET %s").format(
                        pgsql.Identifier(table_name)
                    ),
                    (page_size, offset),
                )
                cols = [d[0] for d in cur.description]
                return DBResult(columns=cols, rows=cur.fetchall())

            elif self._type == "mysql":
                safe = table_name.replace("`", "")
                cur  = self._conn.cursor()
                cur.execute(
                    f"SELECT * FROM `{safe}` LIMIT %s OFFSET %s",
                    (page_size, offset),
                )
                cols = [d[0] for d in cur.description]
                return DBResult(columns=cols, rows=cur.fetchall())

            elif self._type == "mongodb":
                db   = self._conn[self._server.db_name or "admin"]
                docs = list(db[table_name].find().skip(offset).limit(page_size))
                if not docs:
                    return DBResult(columns=["Info"],
                                    rows=[("Sin más documentos",)])
                keys = list(docs[0].keys())
                rows = [tuple(str(d.get(k, "")) for k in keys) for d in docs]
                return DBResult(columns=keys, rows=rows)

            elif self._type == "redis":
                keys = self._conn.keys("*")
                page_keys = keys[offset: offset + page_size]
                rows = [(k, self._conn.type(k)) for k in page_keys]
                return DBResult(columns=["Key", "Tipo"], rows=rows)

        except Exception as exc:
            return DBResult(error=str(exc))

        return DBResult()

    def execute_read_query(self, query: str,
                           page: int, page_size: int) -> DBResult:
        """Ejecuta una consulta (solo lectura) con paginación automática."""
        import re
        q = query.strip().rstrip(";")
        if re.match(r"^\s*(delete|drop|truncate)\b", q, re.I):
            kw = q.split()[0].upper()
            return DBResult(error=f"🚫 {kw} está bloqueado — esta herramienta es de solo lectura")

        try:
            if self._type in ("postgresql", "mysql"):
                # Paginar automáticamente SELECT sin LIMIT
                is_select = bool(re.match(r"^\s*select\b", q, re.I))
                has_limit = bool(re.search(r"\blimit\b", q, re.I))
                if is_select and not has_limit:
                    q = f"{q} LIMIT {page_size} OFFSET {page * page_size}"
                cur = self._conn.cursor()
                cur.execute(q)
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    return DBResult(columns=cols,
                                    rows=cur.fetchall()[:page_size])
                if self._type == "postgresql":
                    self._conn.commit()
                else:
                    self._conn.commit()
                return DBResult(message=f"{cur.rowcount} fila(s) afectadas")

            elif self._type == "mongodb":
                return self.browse_table(q, page, page_size)

            elif self._type == "redis":
                parts  = q.split(maxsplit=1)
                cmd    = parts[0].upper()
                args   = parts[1].split() if len(parts) > 1 else []
                result = self._conn.execute_command(cmd, *args)
                if isinstance(result, list):
                    sliced = result[page * page_size: (page + 1) * page_size]
                    return DBResult(columns=["Resultado"],
                                    rows=[(r,) for r in sliced])
                return DBResult(columns=["Resultado"], rows=[(str(result),)])

        except Exception as exc:
            return DBResult(error=str(exc))

        return DBResult()
