"""
Écran Champion Select — s'affiche pendant le pick/ban.
Montre les counter-picks recommandés selon les picks ennemis.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from core.lcu import LCUClient, parse_champ_select
from core.pick_advisor import get_counters, get_best_pick_vs_team, get_recommended_build, get_ban_suggestions
from data.database import get_champion_name, get_all_champions
import config

# Réutilise les styles de l'overlay
STYLE_MAIN = """
    QWidget {
        background-color: rgba(10, 12, 18, 230);
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
STYLE_GOOD  = "color: #0AC8B9; background: transparent;"
STYLE_BAD   = "color: #C83030; background: transparent;"
STYLE_WARN  = "color: #E6A817; background: transparent;"
STYLE_INFO  = "color: #5B8DD9; background: transparent;"
STYLE_VALUE = "color: #F0E6D3; background: transparent;"
STYLE_TITLE = "color: #C8AA6E; font-weight: bold; font-size: 13px; background: transparent;"


def make_sep():
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background: rgba(200,170,110,40); max-height:1px;")
    return sep


class ChampSelectWidget(QWidget):
    """
    Widget principal du champion select.
    Intégré dans l'overlay principal.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE_MAIN)
        self._lcu = LCUClient()
        self._all_champs = get_all_champions()
        self._last_enemy_ids: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- Statut LCU ---
        self._status = QLabel("⏳ En attente du champion select...")
        self._status.setStyleSheet(STYLE_INFO)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        # --- Mon rôle ---
        self._role_lbl = QLabel("")
        self._role_lbl.setStyleSheet("color:#C8AA6E; font-weight:bold; font-size:13px; background:transparent;")
        self._role_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._role_lbl)

        # --- Bans recommandés ---
        ban_frame = QFrame()
        ban_frame.setStyleSheet(STYLE_SECTION)
        ban_layout = QVBoxLayout(ban_frame)
        ban_layout.setContentsMargins(8, 6, 8, 6)
        ban_layout.setSpacing(4)

        ban_title = QLabel("BANS RECOMMANDÉS")
        ban_title.setStyleSheet(STYLE_TITLE)
        ban_layout.addWidget(ban_title)
        ban_layout.addWidget(make_sep())

        self._ban_grid = QGridLayout()
        self._ban_grid.setSpacing(3)
        self._ban_grid.setColumnStretch(0, 1)
        self._ban_grid.setColumnMinimumWidth(1, 45)

        self._ban_rows: list[tuple[QLabel, QLabel]] = []
        for i in range(5):
            name_lbl = QLabel("—")
            name_lbl.setStyleSheet("color: #F0E6D3; background:transparent;")
            wr_lbl   = QLabel("")
            wr_lbl.setStyleSheet("color: rgba(200,170,110,150); font-size:11px; background:transparent;")
            wr_lbl.setFixedWidth(45)
            wr_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._ban_grid.addWidget(name_lbl, i, 0)
            self._ban_grid.addWidget(wr_lbl,   i, 1)
            self._ban_rows.append((name_lbl, wr_lbl))

        ban_layout.addLayout(self._ban_grid)
        self._ban_frame = ban_frame
        layout.addWidget(ban_frame)

        # --- Picks ennemis ---
        enemy_frame = QFrame()
        enemy_frame.setStyleSheet(STYLE_SECTION)
        enemy_layout = QVBoxLayout(enemy_frame)
        enemy_layout.setContentsMargins(8, 6, 8, 6)
        enemy_layout.setSpacing(4)

        enemy_title = QLabel("PICKS ENNEMIS")
        enemy_title.setStyleSheet(STYLE_TITLE)
        enemy_layout.addWidget(enemy_title)
        enemy_layout.addWidget(make_sep())

        self._enemy_grid = QGridLayout()
        self._enemy_grid.setSpacing(3)
        self._enemy_grid.setColumnMinimumWidth(0, 55)
        self._enemy_grid.setColumnStretch(0, 0)
        self._enemy_grid.setColumnStretch(1, 1)

        # Lignes persistantes (5 joueurs)
        self._enemy_rows: list[tuple[QLabel, QLabel]] = []
        POS_STYLE  = "color: rgba(200,170,110,150); font-size:11px; background:transparent; padding: 0px;"
        CHAMP_STYLE = "color: #F0E6D3; font-size:12px; background:transparent; padding: 0px;"
        for i in range(5):
            pos_lbl   = QLabel("—")
            pos_lbl.setStyleSheet(POS_STYLE)
            pos_lbl.setFixedWidth(55)
            champ_lbl = QLabel("—")
            champ_lbl.setStyleSheet(CHAMP_STYLE)
            self._enemy_grid.addWidget(pos_lbl,   i, 0)
            self._enemy_grid.addWidget(champ_lbl, i, 1)
            self._enemy_rows.append((pos_lbl, champ_lbl))

        enemy_layout.addLayout(self._enemy_grid)
        self._enemy_frame = enemy_frame
        layout.addWidget(enemy_frame)

        # --- Counters recommandés ---
        counter_frame = QFrame()
        counter_frame.setStyleSheet(STYLE_SECTION)
        counter_layout = QVBoxLayout(counter_frame)
        counter_layout.setContentsMargins(8, 6, 8, 6)
        counter_layout.setSpacing(4)

        counter_title = QLabel("COUNTERS RECOMMANDÉS")
        counter_title.setStyleSheet(STYLE_TITLE)
        counter_layout.addWidget(counter_title)
        counter_layout.addWidget(make_sep())

        self._counter_grid = QGridLayout()
        self._counter_grid.setSpacing(4)
        counter_layout.addLayout(self._counter_grid)
        self._counter_frame = counter_frame
        layout.addWidget(counter_frame)

        # --- Meilleur pick vs équipe ---
        team_frame = QFrame()
        team_frame.setStyleSheet(STYLE_SECTION)
        team_layout = QVBoxLayout(team_frame)
        team_layout.setContentsMargins(8, 6, 8, 6)
        team_layout.setSpacing(4)

        team_title = QLabel("MEILLEUR PICK VS LEUR ÉQUIPE")
        team_title.setStyleSheet(STYLE_TITLE)
        team_layout.addWidget(team_title)
        team_layout.addWidget(make_sep())

        self._team_lbl = QLabel("En attente des picks ennemis...")
        self._team_lbl.setStyleSheet(STYLE_INFO)
        self._team_lbl.setWordWrap(True)
        team_layout.addWidget(self._team_lbl)
        self._team_frame = team_frame
        layout.addWidget(team_frame)

        # --- Runes recommandées ---
        runes_frame = QFrame()
        runes_frame.setStyleSheet(STYLE_SECTION)
        runes_layout = QVBoxLayout(runes_frame)
        runes_layout.setContentsMargins(8, 6, 8, 6)
        runes_layout.setSpacing(3)

        runes_title = QLabel("RUNES RECOMMANDÉES")
        runes_title.setStyleSheet(STYLE_TITLE)
        runes_layout.addWidget(runes_title)
        runes_layout.addWidget(make_sep())

        # Arbre primaire
        self._primary_tree_lbl = QLabel("")
        self._primary_tree_lbl.setStyleSheet("color: rgba(200,170,110,200); font-size:11px; font-weight:bold; background:transparent;")
        runes_layout.addWidget(self._primary_tree_lbl)

        # Keystone
        self._keystone_lbl = QLabel("—")
        self._keystone_lbl.setStyleSheet("color: #0AC8B9; font-weight: bold; font-size:12px; background:transparent;")
        runes_layout.addWidget(self._keystone_lbl)

        # Runes primaires (slots 1-3)
        self._primary_rune_lbls = []
        for _ in range(3):
            lbl = QLabel("")
            lbl.setStyleSheet(STYLE_VALUE)
            runes_layout.addWidget(lbl)
            self._primary_rune_lbls.append(lbl)

        runes_layout.addWidget(make_sep())

        # Arbre secondaire
        self._secondary_tree_lbl = QLabel("")
        self._secondary_tree_lbl.setStyleSheet("color: rgba(200,170,110,200); font-size:11px; font-weight:bold; background:transparent;")
        runes_layout.addWidget(self._secondary_tree_lbl)

        # Runes secondaires (slots 4-5)
        self._secondary_rune_lbls = []
        for _ in range(2):
            lbl = QLabel("")
            lbl.setStyleSheet(STYLE_VALUE)
            runes_layout.addWidget(lbl)
            self._secondary_rune_lbls.append(lbl)

        self._wr_lbl = QLabel("")
        self._wr_lbl.setStyleSheet("color: rgba(200,170,110,120); font-size:10px; background:transparent;")
        runes_layout.addWidget(self._wr_lbl)

        self._runes_frame = runes_frame
        layout.addWidget(runes_frame)

        # --- Timer pick ---
        self._timer_lbl = QLabel("")
        self._timer_lbl.setStyleSheet(STYLE_WARN)
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._timer_lbl)

        # Poll toutes les secondes
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    def apply_settings(self, s):
        self._ban_frame.setVisible(s["cs_show_bans"])
        self._enemy_frame.setVisible(s["cs_show_enemies"])
        self._counter_frame.setVisible(s["cs_show_counters"])
        self._team_frame.setVisible(s["cs_show_best_pick"])
        self._runes_frame.setVisible(s["cs_show_runes"])

    def apply_compact(self, is_compact: bool, s):
        """Mode mini : garde seulement picks ennemis + timer."""
        if is_compact:
            self._ban_frame.setVisible(False)
            self._enemy_frame.setVisible(True)
            self._counter_frame.setVisible(False)
            self._team_frame.setVisible(False)
            self._runes_frame.setVisible(False)
        else:
            self.apply_settings(s)

    def _poll(self):
        """Interroge le LCU et met à jour l'UI."""
        phase = self._lcu.get_phase()

        if phase != "ChampSelect":
            if phase in ("InProgress", "None", "Lobby"):
                self._status.setText(f"Phase: {phase}")
                self._status.setStyleSheet(STYLE_INFO)
            return

        session = self._lcu.get_champ_select_session()
        if not session:
            return

        data = parse_champ_select(session)
        self._update(data)

    def _update(self, data: dict):
        self._status.setText("🎮 Champion Select en cours")
        self._status.setStyleSheet(STYLE_GOOD)

        pos = data.get("my_position", "")
        my_champ_id = data.get("my_champion_id", 0)
        my_champ = get_champion_name(my_champ_id) if my_champ_id else "?"
        self._role_lbl.setText(f"{pos}  |  {my_champ}" if my_champ_id else f"{pos}")

        # Mise à jour build si le champion a changé
        if my_champ_id and my_champ_id != getattr(self, "_last_champ_id", None):
            self._last_champ_id = my_champ_id
            self._update_build(my_champ_id, pos or "MID")

        # Timer
        timer_left = data.get("timer_left", 0)
        self._timer_lbl.setText(f"⏱ {int(timer_left)}s — {data.get('phase','')}")

        role = data.get("my_position", "MID") or "MID"

        # Bans (mise à jour quand le rôle change)
        if role != getattr(self, "_last_role", None):
            self._last_role = role
            self._update_bans(role)

        # Picks ennemis (confirmed + hover)
        their_team = data.get("their_team", [])
        enemy_ids = [p["champion_id"] for p in their_team if p["champion_id"] != 0]

        self._update_enemy_grid(their_team)

        # Ne recalcule les counters que si les picks ont changé
        if enemy_ids != self._last_enemy_ids:
            self._last_enemy_ids = enemy_ids
            self._update_counters(enemy_ids, role)

    def _update_enemy_grid(self, their_team: list[dict]):
        pos_labels = {"TOP": "Top", "JUNGLE": "Jgl", "MID": "Mid",
                      "BOTTOM": "ADC", "UTILITY": "Sup", "": "?"}

        for i, (pos_lbl, champ_lbl) in enumerate(self._enemy_rows):
            if i >= len(their_team):
                pos_lbl.setText("—")
                champ_lbl.setText("—")
                champ_lbl.setStyleSheet("color: rgba(200,170,110,60); background:transparent;")
                continue

            player       = their_team[i]
            confirmed_id = player.get("champion_id", 0)
            intent_id    = player.get("intent_id", 0)
            pos          = player.get("position", "")

            pos_lbl.setText(pos_labels.get(pos, pos or "?"))

            if confirmed_id:
                champ_lbl.setText(get_champion_name(confirmed_id))
                champ_lbl.setStyleSheet("color: #F0E6D3; background:transparent;")
            elif intent_id:
                champ_lbl.setText(f"~ {get_champion_name(intent_id)} ?")
                champ_lbl.setStyleSheet("color: #E6A817; background:transparent;")
            else:
                champ_lbl.setText("—")
                champ_lbl.setStyleSheet("color: rgba(91,141,217,150); background:transparent;")

    def _update_bans(self, role: str):
        bans = get_ban_suggestions(role, top_n=5)
        for i, (name_lbl, wr_lbl) in enumerate(self._ban_rows):
            if i < len(bans):
                ban = bans[i]
                wr  = ban.get("avg_win_rate", 0) * 100
                name_lbl.setText(ban["champion_name"])
                wr_lbl.setText(f"{wr:.1f}%")
                wr_lbl.setStyleSheet(
                    "color: #C83030; font-size:11px; background:transparent;" if wr > 52
                    else "color: #E6A817; font-size:11px; background:transparent;"
                )
            else:
                name_lbl.setText("—")
                wr_lbl.setText("")

    def _update_build(self, champion_id: int, role: str):
        def clear_runes():
            self._primary_tree_lbl.setText("")
            self._keystone_lbl.setText("Pas de données → Lance la collecte")
            for lbl in self._primary_rune_lbls + self._secondary_rune_lbls:
                lbl.setText("")
            self._secondary_tree_lbl.setText("")
            self._wr_lbl.setText("")

        build = get_recommended_build(champion_id, role)
        if not build:
            clear_runes()
            return

        rune_names = build.get("rune_names", [])
        if not rune_names:
            clear_runes()
            return

        # Arbre primaire
        primary_tree = build.get("primary_tree", "Primaire")
        self._primary_tree_lbl.setText(f"── {primary_tree} ──")

        # Keystone (index 0)
        self._keystone_lbl.setText(f"⬡  {rune_names[0]}" if rune_names else "—")

        # Runes primaires (index 1-3)
        for i, lbl in enumerate(self._primary_rune_lbls):
            idx = i + 1
            lbl.setText(f"   {rune_names[idx]}" if idx < len(rune_names) else "")

        # Arbre secondaire — déduit du nom de la 4e rune via cache Data Dragon
        from core.pick_advisor import get_rune_tree_name
        sec_tree = ""
        if len(rune_names) > 4:
            # On cherche l'arbre de la 5e rune (index 4)
            perk_ids = build.get("perk_ids", [])
            if len(perk_ids) > 4:
                sec_tree = get_rune_tree_name(perk_ids[4]) or "Secondaire"
        self._secondary_tree_lbl.setText(f"── {sec_tree} ──" if sec_tree else "── Secondaire ──")

        # Runes secondaires (index 4-5)
        for i, lbl in enumerate(self._secondary_rune_lbls):
            idx = i + 4
            lbl.setText(f"   {rune_names[idx]}" if idx < len(rune_names) else "")

        wr    = build.get("win_rate", 0) * 100
        games = build.get("games_played", 0)
        self._wr_lbl.setText(f"Win rate: {wr:.1f}%  ({games} parties)")

    def _update_counters(self, enemy_ids: list[int], role: str):
        # Vide la grille des counters
        for i in reversed(range(self._counter_grid.count())):
            w = self._counter_grid.itemAt(i).widget()
            if w:
                w.deleteLater()

        if not enemy_ids:
            lbl = QLabel("En attente des picks ennemis...")
            lbl.setStyleSheet(STYLE_INFO)
            self._counter_grid.addWidget(lbl, 0, 0)
            self._team_lbl.setText("En attente des picks ennemis...")
            return

        # Counter vs ennemi direct (même rôle)
        primary_enemy = enemy_ids[0]
        counters = get_counters(primary_enemy, role, top_n=5)

        if counters:
            headers = ["Champion", "Avantage", "Parties"]
            for col, h in enumerate(headers):
                lbl = QLabel(h)
                lbl.setStyleSheet("color: rgba(200,170,110,150); font-size:10px; background:transparent;")
                self._counter_grid.addWidget(lbl, 0, col)

            for row, c in enumerate(counters, 1):
                adv  = c.get("advantage", 0)
                sign = "+" if adv >= 0 else ""
                style = STYLE_GOOD if adv > 2 else STYLE_BAD if adv < -2 else STYLE_VALUE

                name_lbl = QLabel(c["champion_name"])
                name_lbl.setStyleSheet(STYLE_VALUE)

                adv_lbl = QLabel(f"{sign}{adv:.1f}%")
                adv_lbl.setStyleSheet(style)

                games_lbl = QLabel(str(c.get("games_played", 0)))
                games_lbl.setStyleSheet("color: rgba(200,170,110,120); background:transparent; font-size:10px;")

                self._counter_grid.addWidget(name_lbl,  row, 0)
                self._counter_grid.addWidget(adv_lbl,   row, 1)
                self._counter_grid.addWidget(games_lbl, row, 2)
        else:
            lbl = QLabel("Pas assez de données → Lance la collecte")
            lbl.setStyleSheet(STYLE_WARN)
            self._counter_grid.addWidget(lbl, 0, 0)

        # Meilleur pick vs toute l'équipe
        best = get_best_pick_vs_team(enemy_ids, role, top_n=3)
        if best:
            lines = []
            for b in best:
                wr = b.get("avg_win_rate", 0) * 100
                lines.append(f"  {b['champion_name']:<15} {wr:.1f}% win rate moyen")
            self._team_lbl.setText("\n".join(lines))
            self._team_lbl.setStyleSheet(STYLE_VALUE)
        else:
            self._team_lbl.setText("Pas assez de données")
            self._team_lbl.setStyleSheet(STYLE_WARN)
