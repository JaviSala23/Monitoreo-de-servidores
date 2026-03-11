"""
Diálogo de gestión de base de datos remota.
Permite conectar (vía túnel SSH), explorar tablas/colecciones, ver estadísticas
y ejecutar consultas en tiempo real.
"""
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..database import Server
from ..db_client import DBClient, DBResult

# ── hilos de trabajo ──────────────────────────────────────────────────────────

class _ConnectThread(QThread):
    done = pyqtSignal(str)     # "" = ok, else mensaje de error

    def __init__(self, client: DBClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        err = self._client.connect()
        self.done.emit(err or "")


class _WorkThread(QThread):
    done = pyqtSignal(object)  # DBResult

    def __init__(self, client: DBClient, mode: str, query: str = "") -> None:
        super().__init__()
        self._client = client
        self._mode   = mode
        self._query  = query

    def run(self) -> None:
        if self._mode == "tables":
            self.done.emit(self._client.list_tables())
        elif self._mode == "stats":
            self.done.emit(self._client.get_stats())
        else:
            self.done.emit(self._client.execute_query(self._query))


# ── diálogo principal ─────────────────────────────────────────────────────────

class DBDialog(QDialog):
    _DB_ICONS = {"postgresql": "🐘", "mysql": "🐬", "mongodb": "🍃", "redis": "🔴"}

    def __init__(self, server: Server, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._server = server
        self._client = DBClient(server)
        self._active_thread: Optional[QThread] = None

        ico   = self._DB_ICONS.get(server.db_type.lower(), "🗄")
        label = server.db_type.upper() if server.db_type else "BD"
        self.setWindowTitle(f"{ico} {label} — {server.name}")
        self.setMinimumSize(860, 620)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._build_ui()

    # ── construcción de UI ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(10, 8, 10, 8)

        root.addLayout(self._build_header())

        self._tabs = QTabWidget()
        self._tabs.setEnabled(False)
        self._tabs.addTab(self._build_tables_tab(), "📋  Tablas / Colecciones")
        self._tabs.addTab(self._build_query_tab(),  "⌨   Consultas")
        self._tabs.addTab(self._build_stats_tab(),  "📊  Estadísticas")
        root.addWidget(self._tabs, 1)

        # barra de estado
        self._lbl_status = QLabel("Listo — no conectado")
        self._lbl_status.setStyleSheet("color: #666666; font-size: 10px;")
        root.addWidget(self._lbl_status)

        btns = QHBoxLayout()
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.close)
        btns.addStretch()
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def _build_header(self) -> QHBoxLayout:
        hdr = QHBoxLayout()

        ico   = self._DB_ICONS.get(self._server.db_type.lower(), "🗄")
        title = QLabel(
            f"{ico}  {self._server.db_type.upper()}  —  "
            f"{self._server.db_name or '(sin BD especificada)'}  "
            f"@ {self._server.host}"
        )
        title.setFont(QFont("Ubuntu", 12, QFont.Bold))
        title.setStyleSheet("color: #61dafb;")

        self._dot_conn = QLabel("⬤  Desconectado")
        self._dot_conn.setStyleSheet("color: #f44336; font-size: 11px;")

        self._btn_connect = QPushButton("🔌  Conectar")
        self._btn_connect.clicked.connect(self._on_connect)

        self._btn_disconnect = QPushButton("✖  Desconectar")
        self._btn_disconnect.clicked.connect(self._on_disconnect)
        self._btn_disconnect.setEnabled(False)

        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._dot_conn)
        hdr.addWidget(self._btn_connect)
        hdr.addWidget(self._btn_disconnect)
        return hdr

    def _build_tables_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)

        tb = QHBoxLayout()
        btn = QPushButton("🔄  Actualizar")
        btn.clicked.connect(self._load_tables)
        tb.addWidget(btn)
        tb.addStretch()
        v.addLayout(tb)

        self._tbl_tables = self._make_table()
        v.addWidget(self._tbl_tables)
        return w

    def _build_query_tab(self) -> QWidget:
        db_type = self._server.db_type.lower()
        hints = {
            "postgresql": "SELECT * FROM mi_tabla LIMIT 100;\nSHOW TABLES;\nDESCRIBE mi_tabla;",
            "mysql":      "SELECT * FROM mi_tabla LIMIT 100;\nSHOW TABLES;\nDESCRIBE mi_tabla;",
            "mongodb":    "nombre_coleccion   ← muestra hasta 100 documentos de esa colección",
            "redis":      "KEYS *\nGET clave\nHGETALL mi_hash\nLRANGE mi_lista 0 -1",
        }

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)

        self._query_edit = QTextEdit()
        self._query_edit.setPlaceholderText(hints.get(db_type, "Escribe tu consulta aquí…"))
        self._query_edit.setFont(QFont("Monospace", 10))
        self._query_edit.setMaximumHeight(110)
        self._query_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v.addWidget(self._query_edit)

        h = QHBoxLayout()
        lbl = QLabel("Ctrl+Enter para ejecutar")
        lbl.setStyleSheet("color: #555555; font-size: 10px;")
        btn_run = QPushButton("▶  Ejecutar")
        btn_run.setObjectName("btn_add")
        btn_run.setShortcut("Ctrl+Return")
        btn_run.clicked.connect(self._run_query)
        h.addWidget(lbl)
        h.addStretch()
        h.addWidget(btn_run)
        v.addLayout(h)

        self._tbl_results = self._make_table()
        v.addWidget(self._tbl_results, 1)
        return w

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)

        tb = QHBoxLayout()
        btn = QPushButton("🔄  Actualizar")
        btn.clicked.connect(self._load_stats)
        tb.addWidget(btn)
        tb.addStretch()
        v.addLayout(tb)

        self._tbl_stats = self._make_table()
        v.addWidget(self._tbl_stats)
        return w

    @staticmethod
    def _make_table() -> QTableWidget:
        t = QTableWidget()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setStretchLastSection(True)
        t.setStyleSheet("alternate-background-color: #1a1a1a;")
        return t

    # ── helpers de tabla ─────────────────────────────────────────────────

    def _fill_table(self, widget: QTableWidget, result: DBResult) -> None:
        widget.clear()
        if result.error:
            self._lbl_status.setText(f"⚠  {result.error[:120]}")
            return

        widget.setColumnCount(len(result.columns))
        widget.setHorizontalHeaderLabels(result.columns)
        widget.setRowCount(len(result.rows))

        for r, row in enumerate(result.rows):
            for c, val in enumerate(row):
                widget.setItem(r, c, QTableWidgetItem(str(val) if val is not None else ""))

        widget.resizeColumnsToContents()

        msg = result.message or f"{len(result.rows)} fila(s)"
        self._lbl_status.setText(msg)

    # ── slots ─────────────────────────────────────────────────────────────

    def _on_connect(self) -> None:
        self._btn_connect.setText("Conectando…")
        self._btn_connect.setEnabled(False)
        self._lbl_status.setText("Abriendo túnel SSH y conectando a la BD…")

        t = _ConnectThread(self._client)
        t.done.connect(self._on_connected)
        t.finished.connect(t.deleteLater)
        t.start()
        self._active_thread = t

    def _on_connected(self, err: str) -> None:
        self._btn_connect.setText("🔌  Conectar")
        if err:
            self._btn_connect.setEnabled(True)
            self._dot_conn.setText("⬤  Error")
            self._dot_conn.setStyleSheet("color: #f44336; font-size: 11px;")
            self._tabs.setEnabled(False)
            self._lbl_status.setText(f"⚠  {err[:150]}")
        else:
            self._btn_disconnect.setEnabled(True)
            self._dot_conn.setText("⬤  Conectado")
            self._dot_conn.setStyleSheet("color: #4caf50; font-size: 11px;")
            self._tabs.setEnabled(True)
            self._lbl_status.setText("Conexión establecida")
            self._load_tables()

    def _on_disconnect(self) -> None:
        self._client.disconnect()
        self._btn_connect.setEnabled(True)
        self._btn_disconnect.setEnabled(False)
        self._dot_conn.setText("⬤  Desconectado")
        self._dot_conn.setStyleSheet("color: #f44336; font-size: 11px;")
        self._tabs.setEnabled(False)
        self._lbl_status.setText("Desconectado")

    def _load_tables(self) -> None:
        self._lbl_status.setText("Cargando tablas…")
        t = _WorkThread(self._client, "tables")
        t.done.connect(lambda r: self._fill_table(self._tbl_tables, r))
        t.finished.connect(t.deleteLater)
        t.start()
        self._active_thread = t

    def _load_stats(self) -> None:
        self._lbl_status.setText("Cargando estadísticas…")
        t = _WorkThread(self._client, "stats")
        t.done.connect(lambda r: self._fill_table(self._tbl_stats, r))
        t.finished.connect(t.deleteLater)
        t.start()
        self._active_thread = t

    def _run_query(self) -> None:
        query = self._query_edit.toPlainText().strip()
        if not query:
            return
        self._lbl_status.setText("Ejecutando consulta…")
        t = _WorkThread(self._client, "query", query)
        t.done.connect(lambda r: self._fill_table(self._tbl_results, r))
        t.finished.connect(t.deleteLater)
        t.start()
        self._active_thread = t

    # ── limpieza ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._client.disconnect()
        event.accept()
