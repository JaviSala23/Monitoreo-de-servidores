"""
Ventana principal del dashboard — muestra todos los servidores en una cuadrícula.
"""
from typing import Dict

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QAction, QGridLayout, QLabel, QMainWindow, QMessageBox,
    QScrollArea, QScrollBar, QSizePolicy, QStatusBar,
    QToolBar, QWidget,
)

from ..database import Server, add_server, delete_server, get_all_servers, save_server_order, update_server
from .add_server_dialog import AddServerDialog
from .server_card import ServerCard


# Ancho mínimo de cada tarjeta para calcular columnas dinámicas
_CARD_MIN_W = 300


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Server Monitor — Dashboard")
        self.setMinimumSize(700, 550)

        self._cards: Dict[int, ServerCard] = {}
        self._cols: int = 3

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self.load_servers()

    # ── toolbar ─────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Principal")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        act_add = QAction("➕  Agregar servidor", self)
        act_add.setShortcut("Ctrl+N")
        act_add.triggered.connect(self._on_add)
        bar.addAction(act_add)

        bar.addSeparator()

        act_ref = QAction("🔄  Actualizar todo", self)
        act_ref.setShortcut("F5")
        act_ref.triggered.connect(self._on_refresh_all)
        bar.addAction(act_ref)

        bar.addSeparator()

        act_about = QAction("ℹ  Acerca de", self)
        act_about.triggered.connect(self._on_about)
        bar.addAction(act_about)

    # ── central ──────────────────────────────────

    def _build_central(self) -> None:
        from PyQt5.QtWidgets import QVBoxLayout

        # Header
        header = QLabel("🖥  Server Monitor Dashboard")
        header.setFont(QFont("Ubuntu", 16, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("color: #61dafb; padding: 6px 0;")

        # Grid de tarjetas
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._grid_widget)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(self._scroll.NoFrame)

        # Placeholder cuando no hay servidores
        self._empty_label = QLabel(
            "No hay servidores configurados.\n\n"
            "Haz clic en ➕ Agregar servidor para empezar."
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #555555; font-size: 14px;")

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(header)
        vbox.addWidget(self._empty_label)
        vbox.addWidget(self._scroll, 1)   # stretch=1 → ocupa todo el espacio sobrante
        self.setCentralWidget(container)

    # ── status bar ───────────────────────────────

    def _build_statusbar(self) -> None:
        self._status = self.statusBar()
        self._update_status()

    def _update_status(self) -> None:
        total   = len(self._cards)
        online  = sum(1 for c in self._cards.values() if c.is_connected)
        offline = total - online
        self._status.showMessage(
            f"  Servidores: {total}   |   En línea: {online}   |   Desconectados: {offline}"
        )
        self._empty_label.setVisible(total == 0)

    # ── carga inicial ────────────────────────────

    def load_servers(self) -> None:
        for card in list(self._cards.values()):
            card.stop_monitoring()
            card.setParent(None)
        self._cards.clear()

        for i, srv in enumerate(get_all_servers()):
            self._add_card(srv, i)

        self._update_status()

    # ── helpers ───────────────────────────────────

    def _calc_cols(self) -> int:
        """Calcula cuántas columnas caben según el ancho actual del scroll."""
        spacing = self._grid.spacing()
        margins = self._grid.contentsMargins()
        available = self._scroll.viewport().width() - margins.left() - margins.right()
        cols = max(1, (available + spacing) // (_CARD_MIN_W + spacing))
        return cols

    def _add_card(self, server: Server, index: int) -> None:
        card = ServerCard(server, self)
        card.status_changed.connect(self._update_status)
        card.edit_requested.connect(self._on_edit)
        card.delete_requested.connect(self._on_delete)
        card.swap_requested.connect(self._on_swap)

        row, col = divmod(index, self._cols)
        self._grid.addWidget(card, row, col)
        self._grid.setColumnStretch(col, 1)
        self._cards[server.id] = card
        card.start_monitoring()

    def _rebuild_grid(self) -> None:
        for card in self._cards.values():
            self._grid.removeWidget(card)
        # Resetear stretch de columnas anteriores
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for i, card in enumerate(self._cards.values()):
            row, col = divmod(i, self._cols)
            self._grid.addWidget(card, row, col)
        # Distribuir el ancho disponible en partes iguales
        for c in range(self._cols):
            self._grid.setColumnStretch(c, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        new_cols = self._calc_cols()
        if new_cols != self._cols:
            self._cols = new_cols
            self._rebuild_grid()

    # ── acciones ─────────────────────────────────

    def _on_add(self) -> None:
        dlg = AddServerDialog(parent=self)
        if dlg.exec_():
            srv = dlg.get_server()
            srv.id = add_server(srv)
            self._add_card(srv, len(self._cards) - 1)
            self._update_status()

    def _on_edit(self, server_id: int) -> None:
        card = self._cards.get(server_id)
        if not card:
            return
        dlg = AddServerDialog(server=card.server, parent=self)
        if dlg.exec_():
            updated = dlg.get_server()
            updated.id = server_id
            updated.monitoring_enabled = card.server.monitoring_enabled
            update_server(updated)
            card.server = updated
            card.update_header()
            # reiniciar monitoreo con nuevas credenciales
            card.force_refresh()

    def _on_delete(self, server_id: int) -> None:
        card = self._cards.get(server_id)
        name = card.server.name if card else "este servidor"
        reply = QMessageBox.question(
            self,
            "Eliminar servidor",
            f"¿Eliminar «{name}»? Esta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        card = self._cards.pop(server_id, None)
        if card:
            card.stop_monitoring()
            card.setParent(None)
        delete_server(server_id)
        self._rebuild_grid()
        self._update_status()

    def _on_swap(self, from_id: int, to_id: int) -> None:
        keys = list(self._cards.keys())
        fi, ti = keys.index(from_id), keys.index(to_id)
        keys[fi], keys[ti] = keys[ti], keys[fi]
        self._cards = {k: self._cards[k] for k in keys}
        self._rebuild_grid()
        save_server_order(keys)

    def _on_refresh_all(self) -> None:
        for card in self._cards.values():
            card.force_refresh()

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "Server Monitor",
            "<b>Server Monitor</b> v1.0<br><br>"
            "Dashboard de monitoreo multi-servidor vía SSH.<br>"
            "Muestra CPU, RAM, Disco y Tráfico de red en tiempo real.<br><br>"
            "Credenciales cifradas con AES-128 (Fernet).<br>"
            "Stack: Python · PyQt5 · Paramiko · Matplotlib",
        )

    # ── limpieza al cerrar ────────────────────────

    def closeEvent(self, event) -> None:
        for card in self._cards.values():
            card.stop_monitoring()
        event.accept()
