"""
Diálogo para agregar o editar un servidor.
"""
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout,
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
        self.setMinimumWidth(460)
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
        # ── sección base de datos (opcional) ──
        grp = QGroupBox("🗄  Base de datos remota (opcional)")
        grp.setCheckable(True)
        grp.setChecked(False)
        grp.setStyleSheet(
            "QGroupBox { color: #aaaaaa; border: 1px solid #3e3e42;"
            " border-radius: 4px; margin-top: 8px; padding-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #61dafb; }"
        )
        self._grp_db = grp

        db_form = QFormLayout(grp)
        db_form.setLabelAlignment(Qt.AlignRight)
        db_form.setSpacing(6)

        self._db_type = QComboBox()
        self._db_type.addItems(["", "postgresql", "mysql", "mongodb", "redis"])
        self._db_type.currentTextChanged.connect(self._on_db_type_changed)
        db_form.addRow("Motor:", self._db_type)

        self._db_port = QSpinBox()
        self._db_port.setRange(0, 65535)
        self._db_port.setValue(0)
        self._db_port.setSpecialValueText("Puerto por defecto")
        db_form.addRow("Puerto BD:", self._db_port)

        self._db_name = QLineEdit()
        self._db_name.setPlaceholderText("nombre_base_de_datos")
        db_form.addRow("Base de datos:", self._db_name)

        self._db_user = QLineEdit()
        self._db_user.setPlaceholderText("usuario_bd")
        db_form.addRow("Usuario BD:", self._db_user)

        self._db_pass = QLineEdit()
        self._db_pass.setEchoMode(QLineEdit.Password)
        self._db_pass.setPlaceholderText("Contraseña BD")
        db_form.addRow("Contraseña BD:", self._db_pass)

        show_db = QCheckBox("Mostrar contraseña BD")
        show_db.toggled.connect(
            lambda v: self._db_pass.setEchoMode(
                QLineEdit.Normal if v else QLineEdit.Password
            )
        )
        db_form.addRow("", show_db)

        root.addWidget(grp)
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

    def _on_db_type_changed(self, db_type: str) -> None:
        defaults = {"postgresql": 5432, "mysql": 3306, "mongodb": 27017, "redis": 6379}
        if db_type in defaults:
            self._db_port.setValue(defaults[db_type])
        else:
            self._db_port.setValue(0)

    def _fill(self, s: Server) -> None:
        self._name.setText(s.name)
        self._host.setText(s.host)
        self._port.setValue(s.port)
        self._user.setText(s.username)
        self._passw.setText(s.password)
        self._desc.setPlainText(s.description)
        # BD
        if s.db_type:
            self._grp_db.setChecked(True)
            idx = self._db_type.findText(s.db_type)
            if idx >= 0:
                self._db_type.setCurrentIndex(idx)
            self._db_port.setValue(s.db_port)
            self._db_name.setText(s.db_name)
            self._db_user.setText(s.db_user)
            self._db_pass.setText(s.db_password)

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
            db_type     = self._db_type.currentText() if self._grp_db.isChecked() else "",
            db_port     = self._db_port.value()       if self._grp_db.isChecked() else 0,
            db_name     = self._db_name.text().strip() if self._grp_db.isChecked() else "",
            db_user     = self._db_user.text().strip() if self._grp_db.isChecked() else "",
            db_password = self._db_pass.text()         if self._grp_db.isChecked() else "",
        )
        self.accept()

    def get_server(self) -> Server:
        return self._result
