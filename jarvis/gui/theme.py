"""Stark-style dark theme: deep navy, cyan glow, amber accents."""

BG = "#070b14"
PANEL = "#0d1424"
PANEL_2 = "#121b30"
BORDER = "#1d2a45"
CYAN = "#33d6ff"
CYAN_DIM = "#1b7fa0"
AMBER = "#ffb347"
RED = "#ff4d5e"
GREEN = "#3ddc97"
TEXT = "#d8e6ff"
TEXT_DIM = "#7f8fb0"

STATE_COLORS = {
    "off": "#3a4660",
    "starting": AMBER,
    "sleep": CYAN_DIM,
    "ready": CYAN_DIM,
    "active": CYAN,
    "processing": AMBER,
    "busy": AMBER,
    "speaking": GREEN,
    "error": RED,
}

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: "Segoe UI", "DejaVu Sans", sans-serif; font-size: 14px; }}
QFrame#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QFrame#sidebar QLabel {{ background: transparent; }}
QPushButton {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {CYAN_DIM}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {PANEL_2}; }}
QPushButton#nav {{ text-align: left; border: none; border-radius: 6px; padding: 10px 14px; background: transparent; }}
QPushButton#nav:checked {{ background: {PANEL_2}; color: {CYAN}; border-left: 3px solid {CYAN}; }}
QPushButton#primary {{ background: {CYAN_DIM}; border-color: {CYAN}; color: white; font-weight: 600; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QPushButton#mic[active="true"] {{ background: {CYAN_DIM}; border-color: {CYAN}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px; selection-background-color: {CYAN_DIM};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {CYAN_DIM}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {CYAN_DIM}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLabel#h1 {{ font-size: 22px; font-weight: 600; color: {CYAN}; }}
QLabel#h2 {{ font-size: 16px; font-weight: 600; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#bubbleUser {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 12px; padding: 10px 14px; }}
QLabel#bubbleBot {{ background: #0b1a2a; border: 1px solid {CYAN_DIM}; border-radius: 12px; padding: 10px 14px; }}
QLabel#bubbleTool {{ color: {TEXT_DIM}; font-size: 12px; padding: 2px 14px; }}
QProgressBar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: {CYAN_DIM}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {CYAN}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QTableWidget {{ background: {PANEL}; border: 1px solid {BORDER}; gridline-color: {BORDER}; }}
QHeaderView::section {{ background: {PANEL_2}; border: none; padding: 6px; color: {TEXT_DIM}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""
