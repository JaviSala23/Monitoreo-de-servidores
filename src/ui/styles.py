"""
Hoja de estilos QSS centralizada — tema oscuro estilo terminal.
"""

DARK_THEME = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #cccccc;
    font-family: "Segoe UI", "Ubuntu", sans-serif;
    font-size: 13px;
}

QToolBar {
    background-color: #252526;
    border-bottom: 1px solid #3e3e42;
    spacing: 6px;
    padding: 4px 8px;
}

QToolBar QToolButton {
    background-color: #3e3e42;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 5px 10px;
}

QToolBar QToolButton:hover {
    background-color: #4e4e52;
}

QPushButton {
    background-color: #3e3e42;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 10px;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #505054;
    border-color: #777777;
}

QPushButton:pressed {
    background-color: #2e2e31;
}

QPushButton#btn_add {
    background-color: #0078d4;
    border-color: #005fa3;
    color: white;
}

QPushButton#btn_add:hover {
    background-color: #1a8fe0;
}

QPushButton#btn_danger {
    color: #ff6b6b;
}

QPushButton#btn_danger:hover {
    background-color: #3a1a1a;
    border-color: #ff6b6b;
}

QPushButton#btn_warning {
    color: #f9c74f;
}

QPushButton#btn_warning:hover {
    background-color: #3a2f00;
    border-color: #f9c74f;
}

QScrollArea {
    border: none;
    background-color: #1e1e1e;
}

QScrollBar:vertical {
    background-color: #252526;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #555555;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QLineEdit, QSpinBox, QTextEdit {
    background-color: #3c3c3c;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 4px 6px;
    selection-background-color: #0078d4;
}

QLineEdit:focus, QSpinBox:focus, QTextEdit:focus {
    border-color: #0078d4;
}

QLabel {
    color: #cccccc;
    background-color: transparent;
}

QStatusBar {
    background-color: #007acc;
    color: white;
    font-size: 12px;
}

QDialog {
    background-color: #252526;
}

QCheckBox {
    color: #cccccc;
    spacing: 6px;
}

QCheckBox::indicator {
    width: 14px;
    height: 14px;
    background-color: #3c3c3c;
    border: 1px solid #555555;
    border-radius: 3px;
}

QCheckBox::indicator:checked {
    background-color: #0078d4;
    border-color: #0078d4;
}

QFormLayout QLabel {
    color: #aaaaaa;
}

QMessageBox {
    background-color: #252526;
}

QMessageBox QPushButton {
    min-width: 80px;
}
"""
