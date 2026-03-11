"""
Panel de monitoreo de conexiones activas.
Muestra: sesiones SSH/TTY, últimos accesos, conexiones TCP activas
y conexiones web (nginx/apache).  Solo lectura, sin modificaciones.
"""
from typing import Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..database import Server
from ..ssh_client import SSHClient


# ── hilo de recolección ───────────────────────────────────────────────────────

class _FetchThread(QThread):
    """ Ejecuta un comando SSH y devuelve la salida cruda. """
    done = pyqtSignal(str, str)   # (tag, output)

    def __init__(self, ssh: SSHClient, tag: str, cmd: str) -> None:
        super().__init__()
        self._ssh = ssh
        self._tag = tag
        self._cmd = cmd

    def run(self) -> None:
        out, err = self._ssh.execute(self._cmd, timeout=15)
        self.done.emit(self._tag, out if out else f"(sin datos)\n{err}")


# ── parsing helpers ───────────────────────────────────────────────────────────

def _parse_who(raw: str) -> list[list[str]]:
    """Parsea la salida de 'who -a' o 'w --no-header'."""
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            rows.append(parts)
    return rows


def _parse_ss(raw: str) -> list[list[str]]:
    """Parsea 'ss -tnp' — columnas: State, Recv-Q, Send-Q, Local, Peer, Process."""
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] not in ("State", "Netid"):
            rows.append(parts[:6])
    return rows


def _parse_last(raw: str) -> list[list[str]]:
    """Parsea 'last -n 30 -F' — usuario, tty, IP origen, fecha, duración."""
    rows = []
    for line in raw.splitlines():
        if not line.strip() or line.startswith("wtmp"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            rows.append(parts[:8])
    return rows


def _parse_access_log(raw: str) -> list[list[str]]:
    """
    Parsea líneas de acceso nginx/apache.
    Formato Combined: IP - user [fecha] "método path HTTP/ver" status bytes
    """
    import re
    pattern = re.compile(
        r'(\S+)\s+\S+\s+(\S+)\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s+\S+"\s+(\d+)\s+(\S+)'
    )
    rows = []
    for line in raw.splitlines():
        m = pattern.match(line)
        if m:
            ip, user, fecha, metodo, path, status, tamanio = m.groups()
            rows.append([ip, user, fecha, metodo, path, status, tamanio])
    return rows


# ── widget de tabla genérico ──────────────────────────────────────────────────

def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setStyleSheet(
        "QTableWidget { background:#111; gridline-color:#2a2a2a; font-size:11px; }"
        "QTableWidget::item { padding:3px 6px; }"
        "alternate-background-color: #191919;"
    )
    return t


def _fill_table(widget: QTableWidget, rows: list[list[str]]) -> None:
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if c < widget.columnCount():
                item = QTableWidgetItem(str(val))
                # resaltar IPs externas (no 127.x ni ::1)
                if c == 0 and val and not val.startswith("127.") and val != "::1":
                    item.setForeground(Qt.yellow)
                widget.setItem(r, c, item)
    widget.resizeColumnsToContents()


# ── comandos remotos ──────────────────────────────────────────────────────────

_CMDS = {
    "sessions": "w --no-header 2>/dev/null || who -a 2>/dev/null",
    "last":     "last -n 30 -F 2>/dev/null || last -n 30 2>/dev/null",
    "tcp":      "ss -tnp 2>/dev/null || netstat -tnp 2>/dev/null | head -80",
    "udp":      "ss -unp 2>/dev/null | head -60",
    "web":      (
        "tail -n 40 /var/log/nginx/access.log 2>/dev/null || "
        "tail -n 40 /var/log/apache2/access.log 2>/dev/null || "
        "tail -n 40 /var/log/httpd/access_log 2>/dev/null || "
        "echo 'No se encontró log de acceso web'"
    ),
    "failed":   (
        "grep 'Failed password\\|Invalid user\\|authentication failure' "
        "/var/log/auth.log 2>/dev/null | tail -n 40 || "
        "grep 'Failed password\\|Invalid user' /var/log/secure 2>/dev/null | tail -n 40 || "
        "journalctl _SYSTEMD_UNIT=sshd.service -n 40 --no-pager 2>/dev/null | "
        "grep -i 'failed\\|invalid'"
    ),
}


# ── diálogo principal ─────────────────────────────────────────────────────────

class ConnectionsDialog(QDialog):

    def __init__(self, server: Server, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._server = server
        self._ssh    = SSHClient(server.host, server.port,
                                 server.username, server.password)
        self._threads: list[_FetchThread] = []
        self._auto_refresh = False

        self.setWindowTitle(f"🌐  Conexiones activas — {server.name}  ({server.host})")
        self.setMinimumSize(1000, 680)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._build_ui()

    # ── construcción ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # encabezado
        hdr = QHBoxLayout()
        lbl = QLabel(f"🌐  {self._server.host}  —  {self._server.name}")
        lbl.setFont(QFont("Ubuntu", 12, QFont.Bold))
        lbl.setStyleSheet("color:#61dafb;")

        self._dot = QLabel("⬤  Sin conexión")
        self._dot.setStyleSheet("color:#f44336; font-size:11px;")

        self._btn_connect = QPushButton("🔌  Conectar y cargar")
        self._btn_connect.clicked.connect(self._connect_and_load)

        self._btn_refresh = QPushButton("🔄  Actualizar todo")
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.clicked.connect(self._load_all)

        self._chk_auto = QCheckBox("Auto cada 30s")
        self._chk_auto.setEnabled(False)
        self._chk_auto.stateChanged.connect(self._toggle_auto)

        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self._dot)
        hdr.addWidget(self._btn_connect)
        hdr.addWidget(self._btn_refresh)
        hdr.addWidget(self._chk_auto)
        root.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#3e3e42;")
        root.addWidget(sep)

        # pestañas
        self._tabs = QTabWidget()
        self._tabs.setEnabled(False)

        # ── pestaña: Sesiones activas ──
        self._tbl_sessions = _make_table(
            ["Usuario", "TTY", "IP / Host", "Login", "Idle", "JCPU", "PCPU", "Comando"]
        )
        self._tabs.addTab(self._wrap(self._tbl_sessions), "👤  Sesiones SSH")

        # ── pestaña: Últimos accesos ──
        self._tbl_last = _make_table(
            ["Usuario", "TTY", "IP / Host", "Fecha inicio", "D1", "D2", "D3", "Duración"]
        )
        self._tabs.addTab(self._wrap(self._tbl_last), "📋  Historial accesos")

        # ── pestaña: Conexiones TCP ──
        self._tbl_tcp = _make_table(
            ["Estado", "Recv-Q", "Send-Q", "Local", "Remoto", "Proceso"]
        )
        self._tabs.addTab(self._wrap(self._tbl_tcp), "🔗  Conexiones TCP")

        # ── pestaña: Conexiones UDP ──
        self._tbl_udp = _make_table(
            ["Estado", "Recv-Q", "Send-Q", "Local", "Remoto", "Proceso"]
        )
        self._tabs.addTab(self._wrap(self._tbl_udp), "📡  UDP activo")

        # ── pestaña: Accesos web ──
        self._tbl_web = _make_table(
            ["IP", "Usuario", "Fecha", "Método", "Ruta", "Código", "Bytes"]
        )
        self._tabs.addTab(self._wrap(self._tbl_web), "🌍  Accesos Web")

        # ── pestaña: Intentos fallidos ──
        self._tbl_failed = _make_table(["Línea de log"])
        self._tabs.addTab(self._wrap(self._tbl_failed, raw=True), "🚨  Intentos fallidos SSH")

        root.addWidget(self._tabs, 1)

        # barra inferior
        bot = QHBoxLayout()
        self._lbl_status = QLabel("—")
        self._lbl_status.setStyleSheet("color:#666; font-size:10px;")
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.close)
        bot.addWidget(self._lbl_status)
        bot.addStretch()
        bot.addWidget(btn_close)
        root.addLayout(bot)

        # timer para auto-refresh
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_all)

    # ── helpers ───────────────────────────────────────────────────────────

    def _wrap(self, table: QTableWidget, raw: bool = False) -> QWidget:
        """Envuelve la tabla en un QWidget con relleno."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        if raw:
            # para logs de texto largo, horizontalHeader visible
            table.horizontalHeader().setVisible(False)
        v.addWidget(table)
        return w

    def _status(self, msg: str) -> None:
        self._lbl_status.setText(msg)

    # ── conexión ──────────────────────────────────────────────────────────

    def _connect_and_load(self) -> None:
        self._status("Conectando por SSH…")
        self._btn_connect.setEnabled(False)

        def _do():
            return self._ssh.connect()

        t = QThread(self)
        t.started.connect(lambda: None)
        # usamos _FetchThread para reutilizar el hilo
        ft = _FetchThread(self._ssh, "_connect", "echo OK")
        ft.done.connect(self._after_connect)
        ft.finished.connect(ft.deleteLater)
        ft.start()
        self._threads.append(ft)

    def _after_connect(self, tag: str, out: str) -> None:
        if "OK" in out:
            self._dot.setText("⬤  Conectado")
            self._dot.setStyleSheet("color:#4caf50; font-size:11px;")
            self._tabs.setEnabled(True)
            self._btn_refresh.setEnabled(True)
            self._chk_auto.setEnabled(True)
            self._status("Conectado — cargando datos…")
            self._load_all()
        else:
            self._dot.setText("⬤  Error")
            self._dot.setStyleSheet("color:#f44336; font-size:11px;")
            self._btn_connect.setEnabled(True)
            self._status(f"Error de conexión: {out[:120]}")

    # ── carga de datos ────────────────────────────────────────────────────

    def _load_all(self) -> None:
        self._status("Actualizando…")
        for tag, cmd in _CMDS.items():
            ft = _FetchThread(self._ssh, tag, cmd)
            ft.done.connect(self._on_data)
            ft.finished.connect(ft.deleteLater)
            ft.start()
            self._threads.append(ft)

    def _on_data(self, tag: str, raw: str) -> None:
        if tag == "sessions":
            _fill_table(self._tbl_sessions, _parse_who(raw))
            n = self._tbl_sessions.rowCount()
            tab_idx = 0
            self._tabs.setTabText(tab_idx, f"👤  Sesiones SSH ({n})")
            self._status(f"Sesiones activas: {n}")

        elif tag == "last":
            rows = _parse_last(raw)
            _fill_table(self._tbl_last, rows)
            self._tabs.setTabText(1, f"📋  Historial accesos ({len(rows)})")

        elif tag == "tcp":
            rows = _parse_ss(raw)
            _fill_table(self._tbl_tcp, rows)
            n = len(rows)
            self._tabs.setTabText(2, f"🔗  Conexiones TCP ({n})")

        elif tag == "udp":
            rows = _parse_ss(raw)
            _fill_table(self._tbl_udp, rows)
            self._tabs.setTabText(3, f"📡  UDP activo ({len(rows)})")

        elif tag == "web":
            if raw.startswith("No se encontró"):
                self._tbl_web.setRowCount(1)
                self._tbl_web.setItem(0, 0, QTableWidgetItem(raw))
                self._tabs.setTabText(4, "🌍  Accesos Web (—)")
            else:
                rows = _parse_access_log(raw)
                _fill_table(self._tbl_web, rows)
                self._tabs.setTabText(4, f"🌍  Accesos Web ({len(rows)})")

        elif tag == "failed":
            lines = [l for l in raw.splitlines() if l.strip()]
            self._tbl_failed.setColumnCount(1)
            self._tbl_failed.setRowCount(len(lines))
            for r, line in enumerate(lines):
                item = QTableWidgetItem(line)
                # colorear IPs sospechosas en rojo
                if any(k in line.lower() for k in ("failed", "invalid", "error")):
                    item.setForeground(Qt.red)
                self._tbl_failed.setItem(r, 0, item)
            self._tbl_failed.resizeColumnsToContents()
            self._tabs.setTabText(5, f"🚨  Intentos fallidos SSH ({len(lines)})")

    # ── auto-refresh ──────────────────────────────────────────────────────

    def _toggle_auto(self, state: int) -> None:
        if state == Qt.Checked:
            self._timer.start(30_000)
            self._status("Auto-actualización cada 30 s activada")
        else:
            self._timer.stop()
            self._status("Auto-actualización desactivada")

    # ── cerrar ────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._ssh.disconnect()
        event.accept()
