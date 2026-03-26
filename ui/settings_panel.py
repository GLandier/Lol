"""
Settings panel — permet d'activer/désactiver les sections de l'overlay.
Les paramètres sont sauvegardés dans settings.json.
"""

import json
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QCheckBox,
    QPushButton, QLabel, QFrame, QWidget
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QFont

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")

# -------------------------------------------------------------------------
# Définition des settings
# -------------------------------------------------------------------------

SETTINGS_SCHEMA = {
    "show_enemies":      ("Ennemis (K/D/A, CS, items)",   True),
    "show_map":          ("Carte ennemie (MIA / mort)",    True),
    "show_diff":         ("CS & Gold diff vs lane",        True),
    "show_timers":       ("Timers objectifs",              True),
    "show_wards":        ("Wards ennemies",                True),
    "show_spikes":       ("Power spikes",                  True),
    "show_build":        ("Build recommandé",              True),
    "cs_show_bans":      ("Bans recommandés",              True),
    "cs_show_enemies":   ("Picks ennemis",                 True),
    "cs_show_counters":  ("Counters recommandés",          True),
    "cs_show_best_pick": ("Meilleur pick vs équipe",       True),
    "cs_show_runes":     ("Runes recommandées",            True),
}

SECTION_GROUPS = {
    "EN PARTIE": [
        "show_enemies", "show_map", "show_diff",
        "show_timers", "show_wards", "show_spikes", "show_build",
    ],
    "CHAMPION SELECT": [
        "cs_show_bans", "cs_show_enemies", "cs_show_counters",
        "cs_show_best_pick", "cs_show_runes",
    ],
}


# -------------------------------------------------------------------------
# Gestionnaire de settings
# -------------------------------------------------------------------------

class SettingsManager:
    def __init__(self):
        self._data: dict = {}
        self.load()

    def load(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}
        for key, (_, default) in SETTINGS_SCHEMA.items():
            if key not in self._data:
                self._data[key] = default

    def save(self):
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str) -> bool:
        return self._data.get(key, SETTINGS_SCHEMA.get(key, ("", True))[1])

    def set(self, key: str, value: bool):
        self._data[key] = value

    def __getitem__(self, key: str) -> bool:
        return self.get(key)


_settings: SettingsManager | None = None

def get_settings() -> SettingsManager:
    global _settings
    if _settings is None:
        _settings = SettingsManager()
    return _settings


# -------------------------------------------------------------------------
# Dialog settings
# -------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paramètres")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedWidth(280)

        self._settings  = get_settings()
        self._checkboxes: dict[str, QCheckBox] = {}
        self._drag_pos  = QPoint()
        self._build_ui()

        # Centre sur le parent
        if parent:
            geo = parent.geometry()
            self.move(geo.right() + 6, geo.top())

    def _build_ui(self):
        # Conteneur principal avec fond opaque
        self.setStyleSheet("background-color: rgb(13, 15, 22);")
        root = QFrame(self)
        root.setObjectName("root")
        root.setStyleSheet("""
            QFrame#root {
                background-color: rgb(13, 15, 22);
                border: 1px solid rgba(200, 170, 110, 80);
                border-radius: 6px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- Titlebar ----
        titlebar = QWidget()
        titlebar.setFixedHeight(36)
        titlebar.setStyleSheet("""
            background: rgba(200, 170, 110, 15);
            border-radius: 8px 8px 0 0;
            border-bottom: 1px solid rgba(200, 170, 110, 40);
        """)
        tb = QHBoxLayout(titlebar)
        tb.setContentsMargins(12, 0, 8, 0)

        title = QLabel("Paramètres")
        title.setStyleSheet("color: #C8AA6E; font-weight: bold; font-size: 13px; background: transparent;")
        tb.addWidget(title)
        tb.addStretch()

        close = QPushButton("✕")
        close.setFixedSize(24, 24)
        close.setStyleSheet("""
            QPushButton {
                background: rgba(200, 50, 50, 0);
                color: rgba(200, 170, 110, 150);
                border: none;
                font-size: 13px;
                border-radius: 4px;
            }
            QPushButton:hover { background: rgba(200, 50, 50, 180); color: white; }
        """)
        close.clicked.connect(self.reject)
        tb.addWidget(close)
        layout.addWidget(titlebar)

        # ---- Contenu ----
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(2)

        for group_name, keys in SECTION_GROUPS.items():
            # Groupe header
            group_lbl = QLabel(group_name)
            group_lbl.setStyleSheet("""
                color: rgba(200, 170, 110, 180);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
                padding-top: 8px;
                padding-bottom: 2px;
            """)
            cl.addWidget(group_lbl)

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("background: rgba(200,170,110,30); max-height: 1px; border: none;")
            sep.setFixedHeight(1)
            cl.addWidget(sep)

            for key in keys:
                label, _ = SETTINGS_SCHEMA[key]
                cb = QCheckBox(label)
                cb.setChecked(self._settings.get(key))
                cb.setStyleSheet("""
                    QCheckBox {
                        color: #C8C8C8;
                        font-size: 12px;
                        spacing: 10px;
                        background: transparent;
                        padding: 3px 0px;
                    }
                    QCheckBox::indicator {
                        width: 15px;
                        height: 15px;
                        border: 1px solid rgba(200, 170, 110, 100);
                        border-radius: 3px;
                        background: rgba(20, 24, 35, 200);
                    }
                    QCheckBox::indicator:checked {
                        background-color: #0AC8B9;
                        border-color: #0AC8B9;
                        image: none;
                    }
                    QCheckBox::indicator:hover {
                        border-color: rgba(200, 170, 110, 200);
                    }
                """)
                cl.addWidget(cb)
                self._checkboxes[key] = cb

        layout.addWidget(content)

        # ---- Footer boutons ----
        footer = QWidget()
        footer.setFixedHeight(44)
        footer.setStyleSheet("""
            background: rgba(200, 170, 110, 8);
            border-top: 1px solid rgba(200, 170, 110, 30);
            border-radius: 0 0 8px 8px;
        """)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 0, 12, 0)
        fl.setSpacing(8)

        reset_btn = QPushButton("Réinitialiser")
        reset_btn.setFixedHeight(28)
        reset_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(200, 170, 110, 150);
                border: 1px solid rgba(200, 170, 110, 50);
                border-radius: 4px;
                font-size: 11px;
                padding: 0 10px;
            }
            QPushButton:hover {
                color: #C8AA6E;
                border-color: rgba(200, 170, 110, 120);
            }
        """)
        reset_btn.clicked.connect(self._reset)
        fl.addWidget(reset_btn)
        fl.addStretch()

        apply_btn = QPushButton("Appliquer")
        apply_btn.setFixedHeight(28)
        apply_btn.setStyleSheet("""
            QPushButton {
                background: rgba(10, 200, 185, 40);
                color: #0AC8B9;
                border: 1px solid rgba(10, 200, 185, 120);
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 0 14px;
            }
            QPushButton:hover { background: rgba(10, 200, 185, 70); }
            QPushButton:pressed { background: rgba(10, 200, 185, 100); }
        """)
        apply_btn.clicked.connect(self._save)
        fl.addWidget(apply_btn)

        layout.addWidget(footer)

    # ---- Drag ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = QPoint()

    def _save(self):
        for key, cb in self._checkboxes.items():
            self._settings.set(key, cb.isChecked())
        self._settings.save()
        self.accept()

    def _reset(self):
        for key, cb in self._checkboxes.items():
            _, default = SETTINGS_SCHEMA[key]
            cb.setChecked(default)
