"""
Panel de monitoreo de conexiones activas.
Muestra: sesiones SSH/TTY, últimos accesos, conexiones TCP activas
y conexiones web (nginx/apache).  Solo lectura, sin modificaciones.
"""
import json
import urllib.request
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


class _GeoThread(QThread):
    """Resuelve geolocalización de IPs públicas en segundo plano."""
    done = pyqtSignal(str, dict)  # (tag, {ip: 'País / Ciudad'})

    def __init__(self, tag: str, ips: list[str]) -> None:
        super().__init__()
        self._tag = tag
        self._ips = list(dict.fromkeys(ips))  # deduplica conservando orden

    def run(self) -> None:
        result = _geolocate_batch(self._ips)
        self.done.emit(self._tag, result)


# ── parsing helpers ───────────────────────────────────────────────────────────

def _parse_who(raw: str) -> list[list[str]]:
    """Parsea la salida de 'w --no-header' o 'who'."""
    _TTY_PREFIXES = ("pts/", "tty", "console", "vc/", ":")
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        # necesita al menos user + tty + from/date
        if len(parts) < 3:
            continue
        # filtrar líneas de ruido de 'who -a' ("system boot", "run-level", etc.)
        tty = parts[1]
        if not any(tty.startswith(p) for p in _TTY_PREFIXES):
            continue
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


def _geolocate_batch(ips: list[str]) -> dict[str, str]:
    """
    Consulta ip-api.com (gratuito) para obtener país y ciudad de IPs públicas.
    Retorna {ip: 'País / Ciudad'} o {} si falla.
    """
    _private = (
        '10.', '172.16.', '172.17.', '172.18.', '172.19.',
        '172.2', '172.3', '192.168.', '127.', '::1', 'localhost',
    )
    public = [ip for ip in dict.fromkeys(ips)
              if ip and not any(ip.startswith(p) for p in _private)]
    if not public:
        return {}
    payload = json.dumps(
        [{"query": ip, "fields": "country,city,query"} for ip in public[:100]]
    )
    try:
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=country,city,query",
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return {
            item["query"]: f"{item.get('country', '?')} / {item.get('city', '?')}"
            for item in data
            if item.get("status") == "success"
        }
    except Exception:
        return {}


def _extract_ip(addr: str) -> str:
    """Extrae la IP pura (sin puerto ni brackets) de 'IP:port', '[IPv6]:port' o '::ffff:IP'."""
    if not addr or addr in ("-", "*", "0.0.0.0"):
        return ""
    if addr.startswith("["):
        # [IPv6]:port
        ip = addr[1:addr.index("]")] if "]" in addr else addr[1:]
    elif addr.count(":") == 1:
        # IPv4:port
        ip = addr.split(":")[0]
    else:
        # IPv6 puro o IPv4 sin puerto
        ip = addr
    if ip.startswith("::ffff:") and "." in ip:
        ip = ip[7:]  # IPv4-mapped IPv6
    return ip


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
    "sessions": "w --no-header 2>/dev/null || who 2>/dev/null",
    "last":     "last -n 30 -F 2>/dev/null || last -n 30 2>/dev/null",
    "tcp":      "ss -tnp 2>/dev/null || netstat -tnp 2>/dev/null | head -80",
    "udp":      "ss -unp 2>/dev/null | head -60",
    "web":      (
        # Busca el primer log web con contenido real ([ -s ] = existe Y no-vacío)
        "_wf=''; "
        "for _l in "
        "  /var/log/nginx/access.log /var/log/nginx/*.log "
        "  /var/log/apache2/access.log /var/log/apache2/*.log "
        "  /var/log/httpd/access_log /var/log/httpd/access.log; do "
        "  [ -s \"$_l\" ] && _wf=\"$_l\" && break; "
        "done 2>/dev/null; "
        "if [ -n \"$_wf\" ]; then "
        "  tail -n 100 \"$_wf\"; "
        "else "
        "  _af=$(find /var/log -maxdepth 5 -name '*.log' -size +1k 2>/dev/null "
        "    | xargs grep -rl 'HTTP/' 2>/dev/null | head -1); "
        "  if [ -n \"$_af\" ]; then "
        "    echo \"__LOG__$_af\"; tail -n 100 \"$_af\"; "
        "  else "
        "    echo '__NO_WEB_LOG__'; "
        "  fi; "
        "fi"
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
        self._geo_threads: list[_GeoThread] = []
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
            ["Usuario", "TTY", "IP / Host", "Login", "Idle", "JCPU", "PCPU", "Comando", "País"]
        )
        self._tabs.addTab(self._wrap(self._tbl_sessions), "👤  Sesiones SSH")

        # ── pestaña: Últimos accesos ──
        self._tbl_last = _make_table(
            ["Usuario", "TTY", "IP / Host", "Fecha inicio", "D1", "D2", "D3", "Duración"]
        )
        self._tabs.addTab(self._wrap(self._tbl_last), "📋  Historial accesos")

        # ── pestaña: Conexiones TCP ──
        self._tbl_tcp = _make_table(
            ["Estado", "Recv-Q", "Send-Q", "Local", "Remoto", "Proceso", "País"]
        )
        self._tabs.addTab(self._wrap(self._tbl_tcp), "🔗  Conexiones TCP")

        # ── pestaña: Conexiones UDP ──
        self._tbl_udp = _make_table(
            ["Estado", "Recv-Q", "Send-Q", "Local", "Remoto", "Proceso", "País"]
        )
        self._tabs.addTab(self._wrap(self._tbl_udp), "📡  UDP activo")

        # ── pestaña: Accesos web ──
        self._tbl_web = _make_table(
            ["IP", "Usuario", "Fecha", "Método", "Ruta", "Código", "Bytes", "País"]
        )
        self._tabs.addTab(self._wrap(self._tbl_web), "🌍  Accesos Web")

        # ── pestaña: Intentos fallidos ──
        self._tbl_failed = _make_table(["Línea de log"])
        self._tabs.addTab(self._wrap(self._tbl_failed, raw=True), "🚨  Intentos fallidos SSH")

        root.addWidget(self._tabs, 1)

        # mapa tag → (tabla, columna_ip, columna_país)
        self._geo_map: dict = {
            "sessions_geo": (self._tbl_sessions, 2, 8),
            "tcp_geo":      (self._tbl_tcp,      4, 6),
            "udp_geo":      (self._tbl_udp,      4, 6),
            "web_geo":      (self._tbl_web,      0, 7),
        }

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
            rows = _parse_who(raw)
            _fill_table(self._tbl_sessions, rows)
            n = self._tbl_sessions.rowCount()
            tab_idx = 0
            self._tabs.setTabText(tab_idx, f"👤  Sesiones SSH ({n})")
            self._status(f"Sesiones activas: {n}")
            self._start_geo("sessions_geo", [r[2] for r in rows if len(r) > 2])

        elif tag == "last":
            rows = _parse_last(raw)
            _fill_table(self._tbl_last, rows)
            self._tabs.setTabText(1, f"📋  Historial accesos ({len(rows)})")

        elif tag == "tcp":
            rows = _parse_ss(raw)
            _fill_table(self._tbl_tcp, rows)
            n = len(rows)
            self._tabs.setTabText(2, f"🔗  Conexiones TCP ({n})")
            self._start_geo("tcp_geo", [_extract_ip(r[4]) for r in rows if len(r) > 4])

        elif tag == "udp":
            rows = _parse_ss(raw)
            _fill_table(self._tbl_udp, rows)
            self._tabs.setTabText(3, f"📡  UDP activo ({len(rows)})")
            self._start_geo("udp_geo", [_extract_ip(r[4]) for r in rows if len(r) > 4])

        elif tag == "web":
            no_log = raw.startswith("__NO_WEB_LOG__") or not raw.strip() or raw.startswith("(sin datos)")
            if no_log:
                msg = "No se encontró ningún log de acceso web (nginx/apache) con contenido en este servidor."
                self._tbl_web.setColumnCount(1)
                self._tbl_web.setRowCount(1)
                self._tbl_web.setItem(0, 0, QTableWidgetItem(msg))
                self._tabs.setTabText(4, "🌍  Accesos Web (—)")
                self._status("Accesos web: sin log encontrado")
            else:
                # quitar la línea informativa __LOG__/ruta si aparece
                lines = [l for l in raw.splitlines() if not l.startswith("__LOG__")]
                clean = "\n".join(lines)
                rows = _parse_access_log(clean)
                if rows:
                    _fill_table(self._tbl_web, rows)
                    self._tabs.setTabText(4, f"🌍  Accesos Web ({len(rows)})")
                    self._start_geo("web_geo", [r[0] for r in rows if r])
                else:
                    # el archivo existe pero el formato no coincide con Combined Log
                    self._tbl_web.setColumnCount(1)
                    self._tbl_web.setRowCount(min(50, len(lines)))
                    for i, line in enumerate(lines[:50]):
                        self._tbl_web.setItem(i, 0, QTableWidgetItem(line))
                    self._tabs.setTabText(4, "🌍  Accesos Web (formato desconocido)")

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
    # ── geolocalización ───────────────────────────────────────────────────────

    def _start_geo(self, tag: str, ips: list[str]) -> None:
        """Lanza un hilo de geolocalización para las IPs dadas."""
        filtered = [ip for ip in ips if ip and ip not in ("-", "*", "0.0.0.0")]
        if not filtered:
            return
        gt = _GeoThread(tag, filtered)
        gt.done.connect(self._on_geo)
        gt.finished.connect(gt.deleteLater)
        gt.start()
        self._geo_threads.append(gt)

    def _on_geo(self, tag: str, geo: dict) -> None:
        """Callback cuando llegan los datos de geolocalización."""
        if not geo or tag not in self._geo_map:
            return
        table, ip_col, geo_col = self._geo_map[tag]
        for r in range(table.rowCount()):
            ip_item = table.item(r, ip_col)
            if ip_item:
                ip = _extract_ip(ip_item.text())
                country = geo.get(ip)
                if country:
                    item = QTableWidgetItem(country)
                    item.setForeground(Qt.cyan)
                    table.setItem(r, geo_col, item)
        table.resizeColumnsToContents()
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
        for gt in self._geo_threads:
            gt.quit()
            gt.wait(500)
        self._ssh.disconnect()
        event.accept()
