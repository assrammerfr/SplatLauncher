"""Original dark theme for SplatLaunch. Colors and styling are our own."""

ACCENT = "#b8ff1f"   # ink green
INK_PINK = "#ff3d9a"  # hover / secondary

BG = "#0c0e13"
PANEL = "#161a23"
TEXT = "#e8eaf0"
MUTED = "#8e97ab"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}}
QLineEdit, QSpinBox {{
    background: {PANEL};
    border: 1px solid #2b3040;
    border-radius: 6px;
    padding: 6px 8px;
}}
QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QListWidget {{
    background: {PANEL};
    border: 1px solid #2b3040;
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 10px;
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background: {ACCENT};
    color: #111;
}}
QPushButton {{
    background: {PANEL};
    border: 1px solid #2b3040;
    border-radius: 6px;
    padding: 8px 14px;
}}
QPushButton:hover {{
    border: 1px solid {INK_PINK};
}}
QPushButton#launch {{
    background: {ACCENT};
    color: #111;
    font-weight: bold;
    border: none;
}}
QCheckBox {{
    color: {MUTED};
}}
QLabel#title {{
    font-family: "Consolas", monospace;
    font-size: 16pt;
    font-weight: bold;
    color: {ACCENT};
}}
QMessageBox {{
    background: {BG};
}}
"""
