"""
Diálogo para agregar o editar un servidor.
"""
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout,
)

from ..database import Server


class AddServerDialog(QDialog):
    def __init__(self, server: Optional[Server] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._editing = server is not None
        self._source  = server
        self._result: Optional[Server] = None

        self.setWindowTitle("Editar servidor" if self._editing else "Agregar servidor")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui()

        if self._editing:
            self._fill(server)

    # ── UI ──────────────────────────────────────

    def _build_ui(self) -> None:
        root  = QVBoxLayout(self)
        form  = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Mi servidor web")
        form.addRow("Nombre:", self._name)

        self._host = QLineEdit()
        self._host.setPlaceholderText("192.168.1.100  ó  mi.dominio.com")
        form.addRow("Host / IP:", self._host)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(22)
        form.addRow("Puerto SSH:", self._port)

        self._user = QLineEdit()
        self._user.setPlaceholderText("ubuntu")
        form.addRow("Usuario:", self._user)

        self._passw = QLineEdit()
        self._passw.setEchoMode(QLineEdit.Password)
        self._passw.setPlaceholderText("Contraseña SSH")
        form.addRow("Contraseña:", self._passw)

        show_cb = QCheckBox("Mostrar contraseña")
        show_cb.toggled.connect(
            lambda v: self._passw.setEchoMode(
                QLineEdit.Normal if v else QLineEdit.Password
            )
        )
        form.addRow("", show_cb)

        self._desc = QTextEdit()
        self._desc.setPlaceholderText("Descripción opcional (ej.: Servidor de producción EU)")
        self._desc.setMaximumHeight(60)
        form.addRow("Descripción:", self._desc)

        root.addLayout(form)

        # ── nota de seguridad ──
        note = QLabel("🔒 La contraseña se almacena cifrada localmente con AES-128.")
        note.setStyleSheet("color: #666666; font-size: 10px;")
        note.setWordWrap(True)
        root.addWidget(note)

        # ── botones ──
        btns = QHBoxLayout()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)

        save = QPushButton("Guardar" if self._editing else "Agregar servidor")
        save.setObjectName("btn_add")
        save.setDefault(True)
        save.clicked.connect(self._save)

        btns.addWidget(cancel)
        btns.addStretch()
        btns.addWidget(save)
        root.addLayout(btns)

    def _fill(self, s: Server) -> None:
        self._name.setText(s.name)
        self._host.setText(s.host)
        self._port.setValue(s.port)
        self._user.setText(s.username)
        self._passw.setText(s.password)
        self._desc.setPlainText(s.description)

    # ── validación y guardado ────────────────────

    def _mark_error(self, widget: QLineEdit) -> None:
        widget.setStyleSheet("border: 1px solid #ff6b6b;")

    def _clear_errors(self) -> None:
        for w in (self._name, self._host, self._user, self._passw):
            w.setStyleSheet("")

    def _save(self) -> None:
        self._clear_errors()

        name  = self._name.text().strip()
        host  = self._host.text().strip()
        user  = self._user.text().strip()
        passw = self._passw.text()
        errors = []

        if not name:
            self._mark_error(self._name)
            errors.append("nombre")
        if not host:
            self._mark_error(self._host)
            errors.append("host")
        if not user:
            self._mark_error(self._user)
            errors.append("usuario")
        if not passw:
            self._mark_error(self._passw)
            errors.append("contraseña")

        if errors:
            QMessageBox.warning(self, "Campos requeridos",
                                f"Por favor completa: {', '.join(errors)}.")
            return

        self._result = Server(
            id          = self._source.id if self._editing else None,
            name        = name,
            host        = host,
            port        = self._port.value(),
            username    = user,
            password    = passw,
            description = self._desc.toPlainText().strip(),
        )
        self.accept()

    def get_server(self) -> Server:
        return self._result
