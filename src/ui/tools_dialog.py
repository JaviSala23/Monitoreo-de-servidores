"""
Panel de herramientas rápidas para un servidor.
Permite ejecutar comandos predefinidos o personalizados via SSH.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..database import Server
from ..ssh_client import SSHClient


# ── comandos predefinidos ─────────────────────────────────────────────────────

@dataclass
class QuickCmd:
    label:   str
    icon:    str
    cmd:     str
    confirm: bool = False        # pide confirmación antes de ejecutar
    group:   str  = "General"


_QUICK_COMMANDS: List[QuickCmd] = [
    # Sistema
    QuickCmd("Limpiar RAM (cache)",   "🧹", "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches",
             confirm=True,  group="Sistema"),
    QuickCmd("Ver procesos (top 10)", "📋", "ps aux --sort=-%cpu | head -11",
             group="Sistema"),
    QuickCmd("Espacio en disco",      "💾", "df -h",
             group="Sistema"),
    QuickCmd("Uso de RAM",            "🧠", "free -h",
             group="Sistema"),
    QuickCmd("Uptime y carga",        "⏱",  "uptime",
             group="Sistema"),
    QuickCmd("Últimas entradas log",  "📜", "sudo journalctl -n 50 --no-pager",
             group="Sistema"),
    # Red
    QuickCmd("Reiniciar interfaz de red", "🔄",
             "sudo systemctl restart networking 2>/dev/null || sudo nmcli networking off && sudo nmcli networking on",
             confirm=True, group="Red"),
    QuickCmd("Flush DNS",             "🌐",
             "sudo systemd-resolve --flush-caches 2>/dev/null || sudo resolvectl flush-caches",
             group="Red"),
    QuickCmd("Ver conexiones activas","🔌", "ss -tuln",    group="Red"),
    QuickCmd("Ping a Google",         "📡", "ping -c 4 8.8.8.8", group="Red"),
    # Servicios
    QuickCmd("Reiniciar Nginx",       "🔁",
             "sudo systemctl restart nginx && sudo systemctl status nginx --no-pager",
             confirm=True, group="Servicios"),
    QuickCmd("Reiniciar Apache",      "🔁",
             "sudo systemctl restart apache2 && sudo systemctl status apache2 --no-pager",
             confirm=True, group="Servicios"),
    QuickCmd("Estado Docker",         "🐳", "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
             group="Servicios"),
    QuickCmd("Reiniciar Docker",      "🐳",
             "sudo systemctl restart docker",
             confirm=True, group="Servicios"),
    # Mantenimiento
    QuickCmd("Actualizar paquetes",   "📦",
             "sudo apt-get update && sudo apt-get upgrade -y",
             confirm=True, group="Mantenimiento"),
    QuickCmd("Limpiar paquetes viejos","🗑",
             "sudo apt-get autoremove -y && sudo apt-get autoclean",
             confirm=True, group="Mantenimiento"),
    QuickCmd("Ver logs de errores",   "⚠",
             "sudo journalctl -p err -n 30 --no-pager",
             group="Mantenimiento"),
]


# ── hilo de ejecución ─────────────────────────────────────────────────────────

class _RunThread(QThread):
    output  = pyqtSignal(str)   # fragmento de salida
    finished_cmd = pyqtSignal(int, str)  # exit_code, stderr

    def __init__(self, ssh: SSHClient, cmd: str) -> None:
        super().__init__()
        self._ssh = ssh
        self._cmd = cmd

    def run(self) -> None:
        out, err = self._ssh.execute(self._cmd, timeout=60)
        if out:
            self.output.emit(out)
        self.finished_cmd.emit(0 if not err else 1, err)


# ── diálogo principal ─────────────────────────────────────────────────────────

class ToolsDialog(QDialog):
    def __init__(self, server: Server, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._server = server
        self._ssh    = SSHClient(server.host, server.port, server.username, server.password)
        self._thread: Optional[_RunThread] = None

        self.setWindowTitle(f"🔧 Herramientas — {server.name}")
        self.setMinimumSize(820, 560)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # conexión
        root.addLayout(self._build_conn_bar())

        splitter = QSplitter(Qt.Horizontal)

        # izquierda: botones agrupados
        left = QWidget()
        left.setMaximumWidth(260)
        left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setSpacing(6)
        iv.setContentsMargins(4, 4, 4, 4)

        self._cmd_buttons: List[QPushButton] = []
        self._render_quick_buttons(iv)
        iv.addStretch()

        scroll.setWidget(inner)
        lv.addWidget(scroll)
        splitter.addWidget(left)

        # derecha: terminal + entrada libre
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)

        out_label = QLabel("Salida")
        out_label.setStyleSheet("color: #666666; font-size: 10px;")
        rv.addWidget(out_label)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Monospace", 9))
        self._output.setStyleSheet(
            "background-color: #0d0d0d; color: #d4d4d4; border: 1px solid #3e3e42;"
        )
        rv.addWidget(self._output, 1)

        # entrada de comando personalizado
        custom_box = QGroupBox("Comando personalizado")
        custom_box.setStyleSheet(
            "QGroupBox { color: #aaaaaa; border: 1px solid #3e3e42;"
            " border-radius: 4px; margin-top: 8px; padding-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #61dafb; }"
        )
        ch = QHBoxLayout(custom_box)
        self._custom_input = QLineEdit()
        self._custom_input.setPlaceholderText("Escribe un comando… (Enter para ejecutar)")
        self._custom_input.setFont(QFont("Monospace", 10))
        self._custom_input.returnPressed.connect(self._run_custom)
        btn_run = QPushButton("▶ Ejecutar")
        btn_run.setObjectName("btn_add")
        btn_run.clicked.connect(self._run_custom)
        btn_clear = QPushButton("🗑 Limpiar")
        btn_clear.clicked.connect(self._output.clear)
        ch.addWidget(self._custom_input, 1)
        ch.addWidget(btn_run)
        ch.addWidget(btn_clear)
        rv.addWidget(custom_box)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # estado
        self._lbl_status = QLabel("No conectado")
        self._lbl_status.setStyleSheet("color: #666666; font-size: 10px;")
        root.addWidget(self._lbl_status)

    def _build_conn_bar(self) -> QHBoxLayout:
        h = QHBoxLayout()
        title = QLabel(f"🔧  {self._server.host}:{self._server.port}  —  {self._server.username}")
        title.setStyleSheet("color: #61dafb; font-weight: bold;")

        self._dot = QLabel("⬤  Desconectado")
        self._dot.setStyleSheet("color: #f44336; font-size: 11px;")

        self._btn_connect    = QPushButton("🔌 Conectar")
        self._btn_connect.clicked.connect(self._do_connect)
        self._btn_disconnect = QPushButton("✖ Desconectar")
        self._btn_disconnect.clicked.connect(self._do_disconnect)
        self._btn_disconnect.setEnabled(False)

        h.addWidget(title)
        h.addStretch()
        h.addWidget(self._dot)
        h.addWidget(self._btn_connect)
        h.addWidget(self._btn_disconnect)
        return h

    def _render_quick_buttons(self, layout: QVBoxLayout) -> None:
        groups: dict = {}
        for qc in _QUICK_COMMANDS:
            groups.setdefault(qc.group, []).append(qc)

        for group_name, cmds in groups.items():
            grp = QGroupBox(group_name)
            grp.setStyleSheet(
                "QGroupBox { color: #aaaaaa; border: 1px solid #3e3e42;"
                " border-radius: 4px; margin-top: 8px; padding-top: 6px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #c792ea; }"
            )
            gv = QVBoxLayout(grp)
            gv.setSpacing(3)
            gv.setContentsMargins(4, 4, 4, 4)

            for qc in cmds:
                btn = QPushButton(f"{qc.icon}  {qc.label}")
                btn.setToolTip(qc.cmd)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.setEnabled(False)
                if qc.confirm:
                    btn.setObjectName("btn_warning")
                # capturar qc por valor con default arg
                btn.clicked.connect(lambda _, q=qc: self._on_quick(q))
                gv.addWidget(btn)
                self._cmd_buttons.append(btn)

            layout.addWidget(grp)

    # ── conexión ──────────────────────────────────────────────────────────

    def _do_connect(self) -> None:
        self._btn_connect.setText("Conectando…")
        self._btn_connect.setEnabled(False)
        self._lbl_status.setText("Conectando vía SSH…")
        self._append("--- Conectando… ---\n")

        # conexión en hilo para no bloquear UI
        class _ConnThread(QThread):
            done = pyqtSignal(bool)
            def __init__(self, ssh):
                super().__init__(); self._s = ssh
            def run(self): self.done.emit(self._s.connect())

        t = _ConnThread(self._ssh)
        t.done.connect(self._on_connected)
        t.finished.connect(t.deleteLater)
        t.start()
        self._thread = t

    def _on_connected(self, ok: bool) -> None:
        self._btn_connect.setText("🔌 Conectar")
        if ok:
            self._btn_connect.setEnabled(False)
            self._btn_disconnect.setEnabled(True)
            self._dot.setText("⬤  Conectado")
            self._dot.setStyleSheet("color: #4caf50; font-size: 11px;")
            self._lbl_status.setText("Conexión SSH activa")
            self._append("--- Conectado ✔ ---\n")
            for b in self._cmd_buttons:
                b.setEnabled(True)
            self._custom_input.setEnabled(True)
        else:
            self._btn_connect.setEnabled(True)
            self._dot.setText("⬤  Error")
            self._lbl_status.setText("No se pudo conectar")
            self._append("--- Error de conexión ✘ ---\n")

    def _do_disconnect(self) -> None:
        self._ssh.disconnect()
        self._btn_connect.setEnabled(True)
        self._btn_disconnect.setEnabled(False)
        self._dot.setText("⬤  Desconectado")
        self._dot.setStyleSheet("color: #f44336; font-size: 11px;")
        self._lbl_status.setText("Desconectado")
        for b in self._cmd_buttons:
            b.setEnabled(False)
        self._append("--- Desconectado ---\n")

    # ── ejecución ─────────────────────────────────────────────────────────

    def _on_quick(self, qc: QuickCmd) -> None:
        if qc.confirm:
            reply = QMessageBox.question(
                self, "Confirmar",
                f"¿Ejecutar «{qc.label}»?\n\n$ {qc.cmd}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._run_cmd(qc.cmd, label=qc.label)

    def _run_custom(self) -> None:
        cmd = self._custom_input.text().strip()
        if not cmd:
            return
        self._run_cmd(cmd)
        self._custom_input.clear()

    def _run_cmd(self, cmd: str, label: str = "") -> None:
        if not self._ssh.is_connected():
            self._append("⚠ No conectado. Usa el botón Conectar primero.\n")
            return

        header = f"\n{'─'*60}\n"
        header += f"$ {label + ' → ' if label else ''}{cmd}\n"
        header += f"{'─'*60}\n"
        self._append(header)
        self._lbl_status.setText(f"Ejecutando: {cmd[:60]}…")
        self._set_buttons_enabled(False)

        t = _RunThread(self._ssh, cmd)
        t.output.connect(self._append)
        t.finished_cmd.connect(self._on_cmd_done)
        t.finished.connect(t.deleteLater)
        t.start()
        self._thread = t

    def _on_cmd_done(self, code: int, err: str) -> None:
        if err:
            self._append(f"\n[stderr] {err}\n")
        self._append(f"[salida terminada — código {code}]\n")
        self._lbl_status.setText("Listo")
        self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for b in self._cmd_buttons:
            b.setEnabled(enabled)
        self._custom_input.setEnabled(enabled)

    def _append(self, text: str) -> None:
        self._output.moveCursor(QTextCursor.End)
        self._output.insertPlainText(text)
        self._output.moveCursor(QTextCursor.End)

    # ── limpieza ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        try:
            self._ssh.disconnect()
        except Exception:
            pass
        event.accept()
