"""
Widget de tarjeta para un servidor individual.
Incluye hilo de monitoreo en background y gráficos en tiempo real.
"""
import shutil
import subprocess
from collections import deque
from typing import Optional

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..database import Server
from ..monitor import ServerMetrics, ServerMonitor
from ..ssh_client import SSHClient

_MAX_PTS = 60          # puntos en el eje X de los gráficos
_POLL_SECS = 6         # segundos entre recolecciones

# ──────────────────────────────────────────────
#  Hilo de monitoreo (background thread)
# ──────────────────────────────────────────────

class _MonitorThread(QThread):
    metrics_ready      = pyqtSignal(object)   # ServerMetrics
    connection_changed = pyqtSignal(bool)

    def __init__(self, server: Server) -> None:
        super().__init__()
        self.server   = server
        self._running = True

    def run(self) -> None:
        ssh     = SSHClient(self.server.host, self.server.port,
                            self.server.username, self.server.password)
        monitor = ServerMonitor(ssh)

        ok = ssh.connect()
        self.connection_changed.emit(ok)

        while self._running:
            if not ssh.is_connected():
                ok = ssh.connect()
                self.connection_changed.emit(ok)

            if ssh.is_connected() and self._running:
                metrics = monitor.collect()
                if self._running:
                    self.metrics_ready.emit(metrics)

            # espera fraccionada para poder interrumpir
            for _ in range(_POLL_SECS * 10):
                if not self._running:
                    break
                self.msleep(100)

        ssh.disconnect()

    def stop(self) -> None:
        self._running = False
        self.wait(8000)


# ──────────────────────────────────────────────
#  Gráfico mini embebido en matplotlib
# ──────────────────────────────────────────────

class _MiniGraph(FigureCanvasQTAgg):
    def __init__(self, title: str, color: str, y_max: float = 100.0) -> None:
        fig = Figure(figsize=(3.2, 1.4), facecolor="#1e1e1e")
        super().__init__(fig)

        self._color = color
        self._y_max = y_max
        self._data: deque[float] = deque([0.0] * _MAX_PTS, maxlen=_MAX_PTS)

        ax = fig.add_subplot(111)
        ax.set_facecolor("#151515")
        ax.set_title(title, color="#aaaaaa", fontsize=8, pad=2)
        ax.set_xlim(0, _MAX_PTS - 1)
        ax.set_ylim(0, y_max if y_max > 0 else 100)
        ax.tick_params(colors="#666666", labelsize=6)
        for spine in ax.spines.values():
            spine.set_color("#333333")
        ax.xaxis.set_visible(False)
        ax.yaxis.set_tick_params(labelsize=6)

        self._ax   = ax
        self._line, = ax.plot(list(self._data), color=color, linewidth=1.5)
        self._fill  = ax.fill_between(range(_MAX_PTS), list(self._data),
                                      alpha=0.25, color=color)
        fig.tight_layout(pad=0.4)

        self.setMinimumHeight(100)
        self.setMaximumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background-color: #1e1e1e;")

    def push(self, value: float, dynamic_scale: bool = False) -> None:
        self._data.append(value)
        y = list(self._data)
        self._line.set_ydata(y)
        self._fill.remove()
        self._fill = self._ax.fill_between(range(_MAX_PTS), y,
                                           alpha=0.25, color=self._color)
        if dynamic_scale:
            top = max(max(y) * 1.3, 1.0)
            self._ax.set_ylim(0, top)
        self.draw_idle()


# ──────────────────────────────────────────────
#  Tarjeta de servidor
# ──────────────────────────────────────────────

class ServerCard(QFrame):
    status_changed  = pyqtSignal()
    edit_requested  = pyqtSignal(int)
    delete_requested = pyqtSignal(int)

    def __init__(self, server: Server, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.server       = server
        self.is_connected = False
        self._thread: Optional[_MonitorThread] = None

        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(1)
        self.setMinimumWidth(360)
        self.setMaximumWidth(520)
        self.setStyleSheet(
            "ServerCard {"
            "  background-color: #252526;"
            "  border: 1px solid #3e3e42;"
            "  border-radius: 8px;"
            "}"
        )
        self._build_ui()

    # ── construcción de la interfaz ──────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(5)

        # ── cabecera ──
        hdr = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setFixedWidth(18)
        self._set_dot(False)

        self._lbl_name = QLabel(self.server.name)
        self._lbl_name.setFont(QFont("Ubuntu", 11, QFont.Bold))

        self._lbl_host = QLabel(f"{self.server.host}:{self.server.port}")
        self._lbl_host.setStyleSheet("color: #777777; font-size: 10px;")

        hdr.addWidget(self._dot)
        hdr.addWidget(self._lbl_name)
        hdr.addStretch()
        hdr.addWidget(self._lbl_host)
        root.addLayout(hdr)

        # ── separador ──
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #3e3e42;")
        root.addWidget(sep)

        # ── métricas rápidas ──
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._lbl_cpu  = self._stat_widget(stats, "CPU",   "#61dafb")
        self._lbl_ram  = self._stat_widget(stats, "RAM",   "#c792ea")
        self._lbl_disk = self._stat_widget(stats, "DISCO", "#f9c74f")
        self._lbl_net  = self._stat_widget(stats, "RED",   "#43d9ad", mono=True)
        root.addLayout(stats)

        # ── load / uptime ──
        self._lbl_info = QLabel("Conectando…")
        self._lbl_info.setStyleSheet("color: #555555; font-size: 9px;")
        root.addWidget(self._lbl_info)

        # ── gráficos ──
        self._g_cpu = _MiniGraph("CPU %",     "#61dafb", 100)
        self._g_ram = _MiniGraph("RAM %",     "#c792ea", 100)
        self._g_net = _MiniGraph("Red KB/s",  "#43d9ad", 0)   # escala dinámica
        root.addWidget(self._g_cpu)
        root.addWidget(self._g_ram)
        root.addWidget(self._g_net)

        # ── botones ──
        btns = QHBoxLayout()
        b_ssh  = QPushButton("💻 SSH")
        b_ssh.setToolTip("Abrir terminal SSH")
        b_ssh.clicked.connect(self._open_terminal)

        b_edit = QPushButton("✏ Editar")
        b_edit.clicked.connect(lambda: self.edit_requested.emit(self.server.id))

        b_del  = QPushButton("🗑 Eliminar")
        b_del.setObjectName("btn_danger")
        b_del.clicked.connect(lambda: self.delete_requested.emit(self.server.id))

        btns.addWidget(b_ssh)
        btns.addWidget(b_edit)
        btns.addStretch()
        btns.addWidget(b_del)
        root.addLayout(btns)

        # ── mensaje de error ──
        self._lbl_err = QLabel("")
        self._lbl_err.setStyleSheet("color: #ff6b6b; font-size: 9px;")
        self._lbl_err.setAlignment(Qt.AlignCenter)
        root.addWidget(self._lbl_err)

    def _stat_widget(self, layout: QHBoxLayout, label: str,
                     color: str, mono: bool = False) -> QLabel:
        col = QVBoxLayout()
        col.setSpacing(1)
        lbl_title = QLabel(label)
        lbl_title.setStyleSheet("color: #666666; font-size: 9px;")
        lbl_val = QLabel("—")
        font = QFont("Monospace" if mono else "Ubuntu", 12, QFont.Bold)
        lbl_val.setFont(font)
        lbl_val.setStyleSheet(f"color: {color};")
        col.addWidget(lbl_title)
        col.addWidget(lbl_val)
        layout.addLayout(col)
        return lbl_val

    # ── utilidades de UI ─────────────────────────

    def update_header(self) -> None:
        self._lbl_name.setText(self.server.name)
        self._lbl_host.setText(f"{self.server.host}:{self.server.port}")

    def _set_dot(self, connected: bool) -> None:
        color = "#4caf50" if connected else "#f44336"
        self._dot.setStyleSheet(f"color: {color}; font-size: 16px;")

    @staticmethod
    def _fmt_speed(kbs: float) -> str:
        if kbs >= 1024:
            return f"{kbs / 1024:.1f}MB/s"
        return f"{kbs:.1f}KB/s"

    # ── slots ────────────────────────────────────

    @pyqtSlot(bool)
    def _on_connection_changed(self, connected: bool) -> None:
        self.is_connected = connected
        self._set_dot(connected)
        if not connected:
            self._lbl_err.setText("Sin conexión — reintentando…")
        else:
            self._lbl_err.setText("")
        self.status_changed.emit()

    @pyqtSlot(object)
    def _on_metrics_ready(self, m: ServerMetrics) -> None:
        if m.error:
            self._lbl_err.setText(f"⚠ {m.error[:70]}")
            self._on_connection_changed(False)
            return

        self._on_connection_changed(True)

        self._lbl_cpu.setText(f"{m.cpu_percent:.1f}%")
        self._lbl_ram.setText(f"{m.mem_percent:.1f}%")
        self._lbl_disk.setText(f"{m.disk_percent:.1f}%")
        self._lbl_net.setText(
            f"↓{self._fmt_speed(m.net_rx_kbs)}  ↑{self._fmt_speed(m.net_tx_kbs)}"
        )
        self._lbl_info.setText(
            f"Carga: {m.load_avg}  |  "
            f"RAM {m.mem_used_mb:.0f}/{m.mem_total_mb:.0f} MB  |  "
            f"Disco {m.disk_used_gb:.1f}/{m.disk_total_gb:.1f} GB  |  "
            f"{m.uptime}"
        )

        self._g_cpu.push(m.cpu_percent)
        self._g_ram.push(m.mem_percent)
        self._g_net.push(m.net_rx_kbs + m.net_tx_kbs, dynamic_scale=True)

    # ── control del hilo ─────────────────────────

    def start_monitoring(self) -> None:
        self._thread = _MonitorThread(self.server)
        self._thread.metrics_ready.connect(self._on_metrics_ready)
        self._thread.connection_changed.connect(self._on_connection_changed)
        self._thread.start()

    def stop_monitoring(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None

    def force_refresh(self) -> None:
        self.stop_monitoring()
        self.start_monitoring()

    # ── abrir terminal SSH ───────────────────────

    def _open_terminal(self) -> None:
        user = self.server.username
        host = self.server.host
        port = self.server.port
        ssh_cmd = f"ssh -p {port} {user}@{host}"

        terminals = [
            ["gnome-terminal", "--",   "bash", "-c", f"{ssh_cmd}; exec bash"],
            ["xterm",          "-e",   ssh_cmd],
            ["konsole",        "-e",   ssh_cmd],
            ["xfce4-terminal", "-e",   ssh_cmd],
            ["lxterminal",     "-e",   ssh_cmd],
            ["tilix",          "-e",   ssh_cmd],
        ]

        for term in terminals:
            if shutil.which(term[0]):
                try:
                    subprocess.Popen(term)
                    return
                except Exception:
                    continue

        QMessageBox.information(
            self, "Comando SSH",
            f"No se encontró emulador de terminal.\n\nEjecuta manualmente:\n\n{ssh_cmd}",
        )
