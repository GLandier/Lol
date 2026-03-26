"""
UI Overlay principal — fenêtre transparente qui se pose sur LoL.
Affiche en temps réel : ennemis, items, timers objectifs, recommandations.
"""

import sys
import os
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QGridLayout, QSizeGrip
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QColor, QPalette, QFont, QMouseEvent, QPainter, QBrush

import config
from core.live_game import LiveGameMonitor
from core.lcu import LCUClient
from core.pick_advisor import get_full_advice, format_advice, get_power_spike_alerts
from data.database import init_db, get_all_champions
from ui.champ_select import ChampSelectWidget
from ui.settings_panel import SettingsDialog, get_settings


# -------------------------------------------------------------------------
# Styles
# -------------------------------------------------------------------------

STYLE_MAIN = """
    QWidget {
        background-color: rgba(10, 12, 18, 220);
        color: #C8AA6E;
        font-family: 'Segoe UI', Arial;
        font-size: 12px;
    }
    QLabel { background: transparent; }
"""

STYLE_SECTION = """
    QFrame {
        background-color: rgba(20, 24, 35, 200);
        border: 1px solid rgba(200, 170, 110, 60);
        border-radius: 6px;
    }
"""

STYLE_TITLE = """
    QLabel {
        color: #C8AA6E;
        font-size: 13px;
        font-weight: bold;
        background: transparent;
    }
"""

STYLE_VALUE = """
    QLabel {
        color: #F0E6D3;
        font-size: 12px;
        background: transparent;
    }
"""

STYLE_GOOD = "color: #0AC8B9; background: transparent;"   # teal — bon
STYLE_BAD  = "color: #C83030; background: transparent;"   # rouge — mauvais
STYLE_WARN = "color: #E6A817; background: transparent;"   # orange — attention
STYLE_INFO = "color: #5B8DD9; background: transparent;"   # bleu — info

STYLE_BTN = """
    QPushButton {
        background-color: rgba(200, 170, 110, 30);
        color: #C8AA6E;
        border: 1px solid rgba(200, 170, 110, 80);
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 11px;
    }
    QPushButton:hover {
        background-color: rgba(200, 170, 110, 60);
    }
    QPushButton:pressed {
        background-color: rgba(200, 170, 110, 90);
    }
"""

STYLE_CLOSE = """
    QPushButton {
        background: rgba(180, 40, 40, 150);
        color: white;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 2px 7px;
    }
    QPushButton:hover { background: rgba(220, 60, 60, 200); }
"""


# -------------------------------------------------------------------------
# Signal bridge (thread-safe : monitor → UI)
# -------------------------------------------------------------------------

class Bridge(QObject):
    game_started  = pyqtSignal(dict)
    game_updated  = pyqtSignal(dict)
    game_ended    = pyqtSignal()


# -------------------------------------------------------------------------
# Widgets réutilisables
# -------------------------------------------------------------------------

def make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background: rgba(200,170,110,40); max-height: 1px;")
    return sep


def make_section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Crée un bloc avec titre et retourne (frame, layout_contenu)."""
    frame = QFrame()
    frame.setStyleSheet(STYLE_SECTION)
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(8, 6, 8, 6)
    outer.setSpacing(4)

    lbl = QLabel(title.upper())
    lbl.setStyleSheet(STYLE_TITLE)
    outer.addWidget(lbl)
    outer.addWidget(make_separator())

    inner = QVBoxLayout()
    inner.setSpacing(3)
    outer.addLayout(inner)
    return frame, inner


# -------------------------------------------------------------------------
# Section : Ennemis en temps réel
# -------------------------------------------------------------------------

class EnemySection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(4)

        title = QLabel("ENNEMIS")
        title.setStyleSheet(STYLE_TITLE)
        self._layout.addWidget(title)
        self._layout.addWidget(make_separator())

        self._rows: list[QHBoxLayout] = []
        self._grid = QGridLayout()
        self._grid.setSpacing(4)
        self._layout.addLayout(self._grid)

    def update_enemies(self, enemy_team: list[dict]):
        # Vide la grille
        for i in reversed(range(self._grid.count())):
            w = self._grid.itemAt(i).widget()
            if w:
                w.deleteLater()

        headers = ["Champion", "K/D/A", "CS", "Items"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("color: rgba(200,170,110,150); font-size: 10px; background:transparent;")
            self._grid.addWidget(lbl, 0, col)

        for row, p in enumerate(enemy_team, 1):
            champ = QLabel(p.get("champion_name", "?"))
            champ.setStyleSheet(STYLE_VALUE)

            kda_val = f"{p.get('kills',0)}/{p.get('deaths',0)}/{p.get('assists',0)}"
            kda = QLabel(kda_val)
            deaths = p.get("deaths", 0)
            kda.setStyleSheet(STYLE_GOOD if deaths == 0 else STYLE_BAD if deaths >= 5 else STYLE_VALUE)

            cs = QLabel(str(p.get("cs", 0)))
            cs.setStyleSheet(STYLE_VALUE)

            # Affiche les noms courts des items (3 premiers)
            items = p.get("items", [])[:3]
            item_names = [i.get("name", "?")[:14] for i in items]
            items_lbl = QLabel(", ".join(item_names) if item_names else "—")
            items_lbl.setStyleSheet("color: #A0B4D0; font-size: 10px; background:transparent;")

            # Indicateur mort
            if p.get("is_dead"):
                champ.setStyleSheet(STYLE_BAD)
                resp = p.get("respawn_timer", 0)
                champ.setText(f"💀 {p.get('champion_name','?')} ({resp:.0f}s)")

            self._grid.addWidget(champ,     row, 0)
            self._grid.addWidget(kda,       row, 1)
            self._grid.addWidget(cs,        row, 2)
            self._grid.addWidget(items_lbl, row, 3)


# -------------------------------------------------------------------------
# Section : Timers objectifs
# -------------------------------------------------------------------------

class TimerSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title = QLabel("OBJECTIFS")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._labels: dict[str, QLabel] = {}
        self._grid = QGridLayout()
        self._grid.setSpacing(4)
        layout.addLayout(self._grid)

        OBJECTIVE_NAMES = {
            "DragonKill":      "🐉 Dragon",
            "BaronKill":       "🟣 Baron",
            "HeraldKill":      "👁 Herald",
            "InhibitorKilled": "🏰 Inhib",
            "ElderDragonKill": "🔴 Elder",
        }
        for row, (key, name) in enumerate(OBJECTIVE_NAMES.items()):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color: rgba(200,170,110,180); background:transparent;")
            timer_lbl = QLabel("—")
            timer_lbl.setStyleSheet(STYLE_INFO)
            self._grid.addWidget(name_lbl,  row, 0)
            self._grid.addWidget(timer_lbl, row, 1)
            self._labels[key] = timer_lbl

    def update_timers(self, timers: list[dict]):
        # Remet tout à "—"
        for lbl in self._labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(STYLE_INFO)

        for t in timers:
            key = t.get("type", "")
            lbl = self._labels.get(key)
            if not lbl:
                continue
            if t.get("is_up"):
                lbl.setText("UP !")
                lbl.setStyleSheet(STYLE_GOOD)
            else:
                lbl.setText(t.get("time_left_str", "—"))
                lbl.setStyleSheet(STYLE_WARN)


# -------------------------------------------------------------------------
# Section : Recommandations build
# -------------------------------------------------------------------------

class BuildSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title = QLabel("BUILD RECOMMANDÉ")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._champion_lbl = QLabel("—")
        self._champion_lbl.setStyleSheet("color:#C8AA6E; font-weight:bold; background:transparent;")
        layout.addWidget(self._champion_lbl)

        self._items_lbl = QLabel("—")
        self._items_lbl.setStyleSheet(STYLE_VALUE)
        self._items_lbl.setWordWrap(True)
        layout.addWidget(self._items_lbl)

        self._wr_lbl = QLabel("")
        self._wr_lbl.setStyleSheet("color: rgba(200,170,110,150); font-size:10px; background:transparent;")
        layout.addWidget(self._wr_lbl)

        layout.addWidget(make_separator())

        adapt_title = QLabel("ADAPTATIONS (items ennemis)")
        adapt_title.setStyleSheet("color: #E6A817; font-size:11px; font-weight:bold; background:transparent;")
        layout.addWidget(adapt_title)

        self._adapt_lbl = QLabel("Aucune adaptation nécessaire")
        self._adapt_lbl.setStyleSheet(STYLE_INFO)
        self._adapt_lbl.setWordWrap(True)
        layout.addWidget(self._adapt_lbl)

    def update_build(self, advice: dict):
        champ = advice.get("my_champion_name", "—")
        role  = advice.get("role", "")
        self._champion_lbl.setText(f"{champ} — {role}")

        build = advice.get("recommended_build")
        if build:
            items = [i["name"] for i in build.get("core_items_named", [])]
            self._items_lbl.setText(" → ".join(items) if items else "Données insuffisantes")
            wr    = build.get("win_rate", 0) * 100
            games = build.get("games_played", 0)
            self._wr_lbl.setText(f"Win rate: {wr:.1f}%  ({games} parties)")
        else:
            self._items_lbl.setText("Pas encore de données → Lance la collecte")
            self._wr_lbl.setText("")

        adaptations = advice.get("adaptive_items", [])
        if adaptations:
            lines = []
            for a in adaptations[:3]:
                tip   = a.get("tip", "")
                items = [i["name"] for i in a.get("counter_items", [])[:2]]
                lines.append(f"⚠ {tip}")
                if items:
                    lines.append(f"   → {', '.join(items)}")
            self._adapt_lbl.setText("\n".join(lines))
            self._adapt_lbl.setStyleSheet(STYLE_WARN)
        else:
            self._adapt_lbl.setText("Aucune adaptation nécessaire")
            self._adapt_lbl.setStyleSheet(STYLE_INFO)


# -------------------------------------------------------------------------
# Section : Wards ennemies actives
# -------------------------------------------------------------------------

class WardSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        title = QLabel("WARDS ENNEMIES")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._content = QVBoxLayout()
        layout.addLayout(self._content)

        self._empty_lbl = QLabel("Aucune ward détectée")
        self._empty_lbl.setStyleSheet(STYLE_INFO)
        self._content.addWidget(self._empty_lbl)

    def update_wards(self, wards: list[dict]):
        for i in reversed(range(self._content.count())):
            w = self._content.itemAt(i).widget()
            if w:
                w.deleteLater()

        if not wards:
            lbl = QLabel("Aucune ward active")
            lbl.setStyleSheet(STYLE_INFO)
            self._content.addWidget(lbl)
            return

        for ward in wards[:6]:  # max 6 affiché
            time_left = ward.get("time_left_str", "?")
            placer    = ward.get("placer", "?")
            lbl = QLabel(f"🔭 {placer}  —  {time_left}")
            lbl.setStyleSheet(STYLE_WARN if ward.get("time_left", 0) > 30 else STYLE_BAD)
            self._content.addWidget(lbl)


# -------------------------------------------------------------------------
# Section : Alertes power spike
# -------------------------------------------------------------------------

class SpikeSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        title = QLabel("POWER SPIKES ENNEMIS")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._content = QVBoxLayout()
        layout.addLayout(self._content)

        self._alerts: list[str] = []

    def add_spike(self, alert_text: str):
        """Ajoute une nouvelle alerte (persistante jusqu'à fin de partie)."""
        if alert_text not in self._alerts:
            self._alerts.append(alert_text)
            lbl = QLabel(alert_text)
            lbl.setStyleSheet(STYLE_BAD)
            lbl.setWordWrap(True)
            self._content.addWidget(lbl)

    def reset(self):
        self._alerts.clear()
        for i in reversed(range(self._content.count())):
            w = self._content.itemAt(i).widget()
            if w:
                w.deleteLater()
        lbl = QLabel("Aucun spike détecté")
        lbl.setStyleSheet(STYLE_INFO)
        self._content.addWidget(lbl)


# -------------------------------------------------------------------------
# Section E : Carte ennemie (statut par lane)
# -------------------------------------------------------------------------

ROLE_LABELS = {"TOP": "Top", "JUNGLE": "Jgl", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Sup"}

class MapSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        title = QLabel("CARTE ENNEMIE")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._grid = QGridLayout()
        self._grid.setSpacing(2)
        self._grid.setColumnMinimumWidth(0, 30)
        self._grid.setColumnMinimumWidth(1, 90)
        layout.addLayout(self._grid)

        self._rows: dict[str, tuple[QLabel, QLabel]] = {}
        roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
        for i, role in enumerate(roles):
            pos_lbl   = QLabel(ROLE_LABELS.get(role, role))
            pos_lbl.setStyleSheet("color: rgba(200,170,110,150); font-size:11px; background:transparent;")
            champ_lbl = QLabel("—")
            champ_lbl.setStyleSheet(STYLE_INFO)
            status_lbl = QLabel("??")
            status_lbl.setStyleSheet(STYLE_INFO)
            self._grid.addWidget(pos_lbl,   i, 0)
            self._grid.addWidget(champ_lbl, i, 1)
            self._grid.addWidget(status_lbl, i, 2)
            self._rows[role] = (champ_lbl, status_lbl)

    def update_map(self, enemy_team: list[dict], last_seen: dict[str, float], game_time: float):
        # Remet à zéro
        for champ_lbl, status_lbl in self._rows.values():
            champ_lbl.setText("—")
            champ_lbl.setStyleSheet(STYLE_INFO)
            status_lbl.setText("??")
            status_lbl.setStyleSheet(STYLE_INFO)

        for p in enemy_team:
            role = p.get("position", "").upper()
            if role not in self._rows:
                continue
            champ_lbl, status_lbl = self._rows[role]
            champ_name = p.get("champion_name", "?")
            champ_lbl.setText(champ_name)

            if p.get("is_dead"):
                resp = int(p.get("respawn_timer", 0))
                status_lbl.setText(f"MORT {resp}s")
                status_lbl.setStyleSheet(STYLE_BAD)
            else:
                seen_at = last_seen.get(champ_name, game_time)
                mia_s   = int(game_time - seen_at)
                if mia_s >= 45:
                    status_lbl.setText(f"MIA {mia_s}s")
                    status_lbl.setStyleSheet(STYLE_WARN)
                else:
                    status_lbl.setText("En lane")
                    status_lbl.setStyleSheet(STYLE_GOOD)
                champ_lbl.setStyleSheet(STYLE_VALUE)


# -------------------------------------------------------------------------
# Section : CS & Gold diff vs adversaire de lane
# -------------------------------------------------------------------------

class DiffSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(STYLE_SECTION)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title = QLabel("CS & OR — vs LANE")
        title.setStyleSheet(STYLE_TITLE)
        layout.addWidget(title)
        layout.addWidget(make_separator())

        self._grid = QGridLayout()
        self._grid.setSpacing(3)
        self._grid.setColumnMinimumWidth(0, 70)
        layout.addLayout(self._grid)

        # Labels de valeurs
        self._vs_lbl      = QLabel("En attente...")
        self._cs_lbl      = QLabel("")
        self._cs_diff_lbl = QLabel("")
        self._gold_lbl    = QLabel("")
        self._lvl_lbl     = QLabel("")
        self._kda_lbl     = QLabel("")

        label_style = "color: rgba(200,170,110,150); font-size:11px; background:transparent;"

        rows = [
            ("",        self._vs_lbl),
            ("CS",      self._cs_lbl),
            ("Diff CS", self._cs_diff_lbl),
            ("Or est.", self._gold_lbl),
            ("Niveau",  self._lvl_lbl),
            ("K/D/A",   self._kda_lbl),
        ]
        for i, (key, val_lbl) in enumerate(rows):
            if key:
                key_lbl = QLabel(key)
                key_lbl.setStyleSheet(label_style)
                self._grid.addWidget(key_lbl, i, 0)
            val_lbl.setStyleSheet(STYLE_VALUE)
            self._grid.addWidget(val_lbl, i, 1)

    def update_diff(self, me: dict, opponent: dict, my_gold: int = 0):
        if not me or not opponent:
            self._vs_lbl.setText("Adversaire non détecté")
            self._vs_lbl.setStyleSheet(STYLE_WARN)
            for lbl in [self._cs_lbl, self._cs_diff_lbl, self._gold_lbl, self._lvl_lbl, self._kda_lbl]:
                lbl.setText("—")
                lbl.setStyleSheet(STYLE_INFO)
            return

        opp_name = opponent.get("champion_name", "?")
        self._vs_lbl.setText(f"vs {opp_name}")
        self._vs_lbl.setStyleSheet("color: #C8AA6E; font-weight: bold; background: transparent;")

        # CS
        my_cs  = me.get("cs", 0)
        opp_cs = opponent.get("cs", 0)
        cs_diff = my_cs - opp_cs
        self._cs_lbl.setText(f"{my_cs}  vs  {opp_cs}")
        self._cs_lbl.setStyleSheet(STYLE_VALUE)

        sign = "+" if cs_diff >= 0 else ""
        self._cs_diff_lbl.setText(f"{sign}{cs_diff} CS")
        if cs_diff >= 15:
            self._cs_diff_lbl.setStyleSheet(STYLE_GOOD)
        elif cs_diff <= -15:
            self._cs_diff_lbl.setStyleSheet(STYLE_BAD)
        else:
            self._cs_diff_lbl.setStyleSheet(STYLE_VALUE)

        # Or estimé (ma gold réelle si dispo, sinon estimée)
        my_gold_est  = my_gold if my_gold > 0 else (my_cs * 20 + me.get("kills", 0) * 300 + me.get("assists", 0) * 150)
        opp_gold_est = opp_cs * 20 + opponent.get("kills", 0) * 300 + opponent.get("assists", 0) * 150
        gold_diff = my_gold_est - opp_gold_est
        gsign = "+" if gold_diff >= 0 else ""
        self._gold_lbl.setText(f"{gsign}{gold_diff:,}g".replace(",", " "))
        if gold_diff >= 500:
            self._gold_lbl.setStyleSheet(STYLE_GOOD)
        elif gold_diff <= -500:
            self._gold_lbl.setStyleSheet(STYLE_BAD)
        else:
            self._gold_lbl.setStyleSheet(STYLE_VALUE)

        # Niveau
        my_lvl  = me.get("level", 1)
        opp_lvl = opponent.get("level", 1)
        lvl_diff = my_lvl - opp_lvl
        lvl_sign = "+" if lvl_diff > 0 else ""
        lvl_str = f"{my_lvl} vs {opp_lvl}"
        if lvl_diff != 0:
            lvl_str += f"  ({lvl_sign}{lvl_diff})"
        self._lvl_lbl.setText(lvl_str)
        self._lvl_lbl.setStyleSheet(STYLE_GOOD if lvl_diff > 0 else STYLE_BAD if lvl_diff < 0 else STYLE_VALUE)

        # K/D/A
        my_kda  = f"{me.get('kills',0)}/{me.get('deaths',0)}/{me.get('assists',0)}"
        opp_kda = f"{opponent.get('kills',0)}/{opponent.get('deaths',0)}/{opponent.get('assists',0)}"
        self._kda_lbl.setText(f"{my_kda}  vs  {opp_kda}")
        self._kda_lbl.setStyleSheet(STYLE_VALUE)


# -------------------------------------------------------------------------
# Fenêtre principale
# -------------------------------------------------------------------------

class OverlayWindow(QMainWindow):
    def __init__(self, summoner_name: str = ""):
        super().__init__()
        self.summoner_name = summoner_name
        self._drag_pos: QPoint = QPoint()
        self._advice_cache: dict = {}

        self._setup_window()
        self._build_ui()
        self._setup_monitor()
        self._setup_timer()

    # -------------------------------------------------------------------------
    # Setup fenêtre
    # -------------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("LoL Tool")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(380)
        self.resize(400, 600)
        self.move(20, 80)   # Position par défaut : coin supérieur gauche

    # -------------------------------------------------------------------------
    # Construction de l'UI
    # -------------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        root.setStyleSheet(STYLE_MAIN)
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # --- Barre de titre (draggable) ---
        title_bar = QWidget()
        title_bar.setFixedHeight(28)
        title_bar.setStyleSheet("background: rgba(200,170,110,20); border-radius:4px;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(8, 0, 4, 0)

        self._title_lbl = QLabel("⚔ LoL Tool  |  En attente de partie...")
        self._title_lbl.setStyleSheet("color:#C8AA6E; font-weight:bold; background:transparent;")
        title_layout.addWidget(self._title_lbl)
        title_layout.addStretch()

        settings_btn = QPushButton("Config")
        settings_btn.setFixedHeight(20)
        settings_btn.setStyleSheet(STYLE_BTN)
        settings_btn.clicked.connect(self._open_settings)
        title_layout.addWidget(settings_btn)

        self._compact_btn = QPushButton("Mini")
        self._compact_btn.setFixedHeight(20)
        self._compact_btn.setStyleSheet(STYLE_BTN)
        self._compact_btn.clicked.connect(self._toggle_compact)
        title_layout.addWidget(self._compact_btn)

        self._toggle_btn = QPushButton("−")
        self._toggle_btn.setFixedSize(22, 22)
        self._toggle_btn.setStyleSheet(STYLE_BTN)
        self._toggle_btn.clicked.connect(self._toggle_content)
        title_layout.addWidget(self._toggle_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(STYLE_CLOSE)
        close_btn.clicked.connect(self.close)
        title_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        # --- Contenu principal (masquable) ---
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        # Statut
        self._status_lbl = QLabel("Lance LoL et entre en partie")
        self._status_lbl.setStyleSheet(STYLE_INFO)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._status_lbl)

        # --- Écran Champion Select (masqué par défaut) ---
        self._champ_select = ChampSelectWidget()
        content_layout.addWidget(self._champ_select)

        # --- Sections In-Game ---
        self._ingame_widget = QWidget()
        ingame_layout = QVBoxLayout(self._ingame_widget)
        ingame_layout.setContentsMargins(0, 0, 0, 0)
        ingame_layout.setSpacing(6)

        self._enemy_section = EnemySection()
        self._map_section   = MapSection()
        self._diff_section  = DiffSection()
        self._timer_section = TimerSection()
        self._ward_section  = WardSection()
        self._spike_section = SpikeSection()
        self._build_section = BuildSection()

        ingame_layout.addWidget(self._enemy_section)
        ingame_layout.addWidget(self._map_section)
        ingame_layout.addWidget(self._diff_section)
        ingame_layout.addWidget(self._timer_section)
        ingame_layout.addWidget(self._ward_section)
        ingame_layout.addWidget(self._spike_section)
        ingame_layout.addWidget(self._build_section)
        content_layout.addWidget(self._ingame_widget)
        content_layout.addStretch()

        # Par défaut : affiche champion select, cache ingame
        self._show_champ_select()
        self._apply_settings()

        main_layout.addWidget(self._content)

        # Grip de redimensionnement
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        main_layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        self._content_visible = True
        self._compact_mode    = False

    def _toggle_content(self):
        self._content_visible = not self._content_visible
        self._content.setVisible(self._content_visible)
        self._toggle_btn.setText("−" if self._content_visible else "+")
        self.adjustSize()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._apply_settings()

    def _apply_settings(self):
        s = get_settings()
        self._enemy_section.setVisible(s["show_enemies"])
        self._map_section.setVisible(s["show_map"])
        self._diff_section.setVisible(s["show_diff"])
        self._timer_section.setVisible(s["show_timers"])
        self._ward_section.setVisible(s["show_wards"])
        self._spike_section.setVisible(s["show_spikes"])
        self._build_section.setVisible(s["show_build"])
        self._champ_select.apply_settings(s)
        self.adjustSize()

    def _toggle_compact(self):
        self._compact_mode = not self._compact_mode
        self._apply_compact()

    def _apply_compact(self):
        is_compact = self._compact_mode
        s = get_settings()

        # --- Sections in-game ---
        self._enemy_section.setVisible(s["show_enemies"] and not is_compact)
        self._diff_section.setVisible(s["show_diff"]     and not is_compact)
        self._ward_section.setVisible(s["show_wards"]    and not is_compact)
        self._spike_section.setVisible(s["show_spikes"]  and not is_compact)
        self._build_section.setVisible(s["show_build"]   and not is_compact)
        self._map_section.setVisible(s["show_map"])
        self._timer_section.setVisible(s["show_timers"])

        # --- Champion select : en mini, garde seulement les picks ennemis ---
        self._champ_select.apply_compact(is_compact, s)

        # Bouton visuel actif/inactif
        if is_compact:
            self._compact_btn.setText("Normal")
            self._compact_btn.setStyleSheet(
                "QPushButton { background-color: rgba(10,200,185,50); color:#0AC8B9; "
                "border:1px solid #0AC8B9; border-radius:4px; padding:0 8px; font-size:11px; }"
            )
        else:
            self._compact_btn.setText("Mini")
            self._compact_btn.setStyleSheet(STYLE_BTN)

        # Force le recalcul de la taille
        self._content.adjustSize()
        self.centralWidget().adjustSize()
        QTimer.singleShot(50, self.adjustSize)

    # -------------------------------------------------------------------------
    # Monitor live game
    # -------------------------------------------------------------------------

    def _setup_monitor(self):
        self._bridge = Bridge()
        self._bridge.game_started.connect(self._on_game_start)
        self._bridge.game_updated.connect(self._on_game_update)
        self._bridge.game_ended.connect(self._on_game_end)

        self._monitor = LiveGameMonitor(summoner_name=self.summoner_name)
        self._monitor.on_game_start(lambda d:  self._bridge.game_started.emit(d))
        self._monitor.on_game_update(lambda d: self._bridge.game_updated.emit(d))
        self._monitor.on_game_end(lambda d:    self._bridge.game_ended.emit())
        self._monitor.start()

    def _setup_timer(self):
        """Timer pour mettre à jour l'UI même sans nouvel événement."""
        self._ui_timer = QTimer()
        self._ui_timer.setInterval(1000)  # refresh toutes les secondes
        self._ui_timer.timeout.connect(self._refresh_timers)
        self._ui_timer.start()

    # -------------------------------------------------------------------------
    # Callbacks (appelés depuis le thread monitor via Bridge)
    # -------------------------------------------------------------------------

    def _show_champ_select(self):
        self._champ_select.setVisible(True)
        self._ingame_widget.setVisible(False)

    def _show_ingame(self):
        self._champ_select.setVisible(False)
        self._ingame_widget.setVisible(True)

    def _on_game_start(self, data: dict):
        self._show_ingame()
        self._title_lbl.setText(f"⚔ {data.get('my_champion','?')} | {data.get('game_time_str','')}")
        self._status_lbl.setText(f"En partie — {data.get('game_mode','')}")
        self._status_lbl.setStyleSheet(STYLE_GOOD)
        self._spike_section.reset()
        self._last_seen: dict[str, float] = {}   # reset tracking "last seen"
        get_power_spike_alerts([], reset=True)
        self._update_ui(data)

    def _on_game_update(self, data: dict):
        self._title_lbl.setText(f"⚔ {data.get('my_champion','?')} | {data.get('game_time_str','')}")
        self._update_ui(data)

    def _on_game_end(self):
        self._show_champ_select()
        self._title_lbl.setText("⚔ LoL Tool  |  En attente de partie...")
        self._status_lbl.setText("Partie terminée")
        self._status_lbl.setStyleSheet(STYLE_INFO)

    def _update_ui(self, data: dict):
        my_champ  = data.get("my_champion", "")
        role      = self._detect_role(data)
        game_time = data.get("game_time", 0)

        if not hasattr(self, "_last_seen"):
            self._last_seen = {}

        # Ennemis
        enemy_team = data.get("enemy_team", [])
        self._enemy_section.update_enemies(enemy_team)

        # Mise à jour last_seen pour les ennemis qui ne sont pas morts
        for p in enemy_team:
            name = p.get("champion_name", "")
            if name and not p.get("is_dead"):
                # Considère "vu" si kill/death récent (kills ou assists changent)
                activity = p.get("kills", 0) + p.get("deaths", 0) + p.get("assists", 0)
                prev = getattr(self, "_last_activity", {}).get(name, (-1, 0))
                if activity != prev[0]:
                    self._last_seen[name] = game_time
                    if not hasattr(self, "_last_activity"):
                        self._last_activity = {}
                    self._last_activity[name] = (activity, game_time)
                elif name not in self._last_seen:
                    self._last_seen[name] = game_time  # init

        # Carte ennemie
        self._map_section.update_map(enemy_team, self._last_seen, game_time)

        # CS & Gold diff vs adversaire de lane
        me_player = next(
            (p for p in data.get("my_team", []) if p.get("champion_name") == my_champ), None
        )
        role_to_pos = {
            "TOP": "TOP", "JUNGLE": "JUNGLE", "MID": "MIDDLE",
            "ADC": "BOTTOM", "SUPPORT": "UTILITY",
        }
        target_pos = role_to_pos.get(role, "")
        opponent = next(
            (p for p in data.get("enemy_team", [])
             if p.get("position", "").upper() == target_pos),
            None
        )
        my_gold = data.get("my_stats", {}).get("current_gold", 0)
        self._diff_section.update_diff(me_player, opponent, my_gold)

        # Timers objectifs
        self._timer_section.update_timers(data.get("objective_timers", []))

        # Wards ennemies
        game_time = data.get("game_time", 0)
        wards = self._monitor.event_tracker.get_active_wards(game_time)
        self._ward_section.update_wards(wards)

        # Power spikes
        enemy_items = self._monitor.get_enemy_item_ids()
        for spike in get_power_spike_alerts(enemy_items):
            self._spike_section.add_spike(spike["alert"])

        # Build + adaptations
        enemy_items  = self._monitor.get_enemy_item_ids()
        champs       = get_all_champions()
        my_champ_obj = next((c for c in champs if c["name"] == my_champ), None)

        if my_champ_obj:
            try:
                advice = get_full_advice(
                    my_champion_id=my_champ_obj["id"],
                    role=role,
                    enemy_champion_ids=[],
                    enemy_item_ids=enemy_items,
                )
                self._build_section.update_build(advice)
            except Exception as e:
                print(f"[Overlay] Erreur build advice: {e}")

    def _detect_role(self, data: dict) -> str:
        """Détecte le rôle du joueur depuis sa position dans l'équipe."""
        my_champ = data.get("my_champion", "")
        for p in data.get("my_team", []):
            if p.get("champion_name") == my_champ:
                pos = p.get("position", "").upper()
                role_map = {
                    "TOP": "TOP", "JUNGLE": "JUNGLE",
                    "MIDDLE": "MID", "BOTTOM": "ADC",
                    "UTILITY": "SUPPORT",
                }
                return role_map.get(pos, "MID")
        return "MID"

    def _refresh_timers(self):
        """Rafraîchit les timers sans poll API (chaque seconde)."""
        if self._monitor.is_in_game() and self._monitor.current_data:
            game_time = self._monitor.get_game_time()
            timers = self._monitor.event_tracker.get_active_timers(game_time)
            self._timer_section.update_timers(timers)

    # -------------------------------------------------------------------------
    # Drag pour déplacer la fenêtre
    # -------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = QPoint()


# -------------------------------------------------------------------------
# Lancement
# -------------------------------------------------------------------------

def run(summoner_name: str = ""):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    init_db()
    window = OverlayWindow(summoner_name=summoner_name)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    name = input("Ton nom d'invocateur (optionnel, Entrée pour skip): ").strip()
    run(summoner_name=name)
