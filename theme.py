"""Splatoon-themed palette and styling for the SplatLaunch-inspired launcher layout."""

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
    border: none;
    background: {BG};
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-radius: 14px;
    padding: 10px 18px;
    margin: 4px 6px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    background: rgba(255,255,255,0.08);
    color: {TEXT};
}}
QLineEdit, QSpinBox, QTextBrowser, QListWidget, QComboBox {{
    background: {PANEL};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 8px 10px;
    color: {TEXT};
}}
QPushButton {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 9px 18px;
    font-weight: 700;
}}
QPushButton:hover {{
    border: 1px solid {S2_PINK};
}}
QPushButton#play-btn {{
    background: {S2_PINK};
    color: white;
    border: none;
    border-radius: 12px;
    padding: 12px 22px;
    font-size: 14px;
}}
QPushButton#settings-btn {{
    background: rgba(255,255,255,0.12);
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 12px 22px;
    font-size: 14px;
}}
QPushButton#add-game-tile {{
    background: rgba(255,255,255,0.02);
    border: 2px dashed rgba(255,255,255,0.26);
    border-radius: 18px;
    min-height: 140px;
    font-size: 48px;
    color: {MUTED};
}}
QPushButton#game-tile {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    min-height: 140px;
    text-align: left;
    padding: 16px;
}}
QWidget#top-bar {{
    background: rgba(7,24,39,0.65);
    border-bottom: 1px solid rgba(255,255,255,0.08);
}}
QLabel#brand-mini {{
    font-size: 20px;
    font-weight: 900;
    letter-spacing: 0.5px;
    color: {TEXT};
}}
QLabel#user-name {{
    color: {TEXT};
    font-weight: 700;
    padding-right: 12px;
}}
QWidget#hero-panel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(7,24,39,0.84), stop:0.45 rgba(7,24,39,0.7), stop:1 rgba(7,24,39,0.82));
    border-radius: 22px;
    min-height: 290px;
}}
QLabel#hero-title {{
    font-size: 42px;
    font-weight: 900;
    color: {TEXT};
}}
QLabel#hero-subtitle {{
    font-size: 15px;
    color: {TEXT};
    opacity: 0.9;
}}
QLabel#games-label {{
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1px;
    color: {MUTED};
}}
QWidget#bottom-bar {{
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}}
QLabel#preset-label, QLabel#esc-label {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 700;
}}
QListWidget {{
    background: transparent; border: none;
}}
QLabel#empty-state {{
    font-size: 20px; font-weight: 700;
}}
"""
