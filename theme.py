"""Splatoon-themed palette and styling for SplatLaunch."""

S2_PINK = "#FF2E88"
S2_TURQ = "#00E5C0"
S3_YELLOW = "#F5E600"
S3_BLUE = "#1B2BFF"
BG = "#071827"
BG_ALT = "#0D1F31"
PANEL = "#11263D"
PANEL_ALT = "#183451"
TEXT = "#F5FAFF"
MUTED = "#B9C8D9"
WHITE = "#FFFFFF"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}}
QMainWindow {{
    background: {BG};
}}
QTabWidget::pane {{
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    background: {BG};
    padding: 8px;
}}
QTabBar::tab {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.08);
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    padding: 10px 18px;
    margin-right: 6px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {S2_PINK}, stop:0.5 {S2_TURQ}, stop:1 {S3_YELLOW});
    color: #071827;
    border: 1px solid rgba(255,255,255,0.2);
}}
QLineEdit, QSpinBox, QTextBrowser {{
    background: {PANEL};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 8px 10px;
    color: {TEXT};
}}
QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {S2_TURQ};
}}
QPushButton {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 700;
}}
QPushButton:hover {{
    border: 1px solid {S2_PINK};
}}
QPushButton#launch, QPushButton#launch-card {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {S2_PINK}, stop:0.5 {S2_TURQ}, stop:1 {S3_YELLOW});
    color: #06141f;
    border: none;
}}
QPushButton#secondary-card, QPushButton#library-action {{
    background: {PANEL_ALT};
    border: 1px solid rgba(255,255,255,0.08);
}}
QLabel#brand-title {{
    font-size: 30px;
    font-weight: 900;
    letter-spacing: 1px;
    color: {TEXT};
}}
QLabel#empty-title {{
    font-size: 20px;
    font-weight: 800;
    color: {TEXT};
}}
QWidget#empty-state {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,46,136,0.12), stop:0.5 rgba(0,229,192,0.08), stop:1 rgba(245,230,0,0.08));
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QListWidget {{
    background: transparent;
    border: none;
}}
QFrame#game-card {{
    background: {PANEL};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}}
QLabel#game-name {{
    font-size: 24px;
    font-weight: 900;
}}
QLabel#game-status {{
    color: {MUTED};
}}
QMessageBox {{
    background: {BG};
}}
"""
