"""
LCU (League Client Update) API — accède au client LoL local.
Permet de lire le champion select en temps réel.

Le client LoL expose une API REST locale sur un port aléatoire.
Les credentials sont dans le lockfile de League of Legends.
Format URL: /lol-{service}/v1/{endpoint}  (ex: /lol-gameflow/v1/gameflow-phase)
"""

import os
import re
import base64
import subprocess
import requests
import urllib3
from typing import Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_lcu_credentials() -> Optional[dict]:
    """
    Trouve le port et le token d'auth du client LoL via le lockfile.
    """
    lockfile_paths = [
        r"C:\Program Files (x86)\League of Legends\lockfile",
        r"C:\Program Files\League of Legends\lockfile",
        r"C:\Riot Games\League of Legends\lockfile",
        r"D:\Riot Games\League of Legends\lockfile",
        os.path.expanduser(r"~\AppData\Local\Riot Games\League of Legends\lockfile"),
    ]

    for path in lockfile_paths:
        try:
            with open(path, "r") as f:
                content = f.read().strip()
            # Format: LeagueClient:PID:PORT:TOKEN:PROTOCOL
            parts = content.split(":")
            if len(parts) >= 4:
                port  = int(parts[2])
                token = parts[3]
                auth  = base64.b64encode(f"riot:{token}".encode()).decode()
                return {
                    "port":        port,
                    "token":       token,
                    "auth_header": f"Basic {auth}",
                    "base_url":    f"https://127.0.0.1:{port}",
                }
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"[LCU] Erreur lockfile {path}: {e}")
            continue

    # Fallback : PowerShell (LeagueClient.exe, pas Ux)
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject Win32_Process -Filter \"name='LeagueClient.exe'\").CommandLine"],
            capture_output=True, text=True, timeout=8
        )
        cmd = result.stdout
        port_match  = re.search(r"--app-port=(\d+)", cmd)
        token_match = re.search(r"--remoting-auth-token=([a-zA-Z0-9_-]+)", cmd)
        if port_match and token_match:
            port  = int(port_match.group(1))
            token = token_match.group(1)
            auth  = base64.b64encode(f"riot:{token}".encode()).decode()
            return {
                "port":        port,
                "token":       token,
                "auth_header": f"Basic {auth}",
                "base_url":    f"https://127.0.0.1:{port}",
            }
    except Exception:
        pass

    return None


class LCUClient:
    """Client pour l'API locale du client LoL."""

    def __init__(self):
        self._creds: Optional[dict] = None

    def _ensure_creds(self) -> bool:
        if not self._creds:
            self._creds = get_lcu_credentials()
        return self._creds is not None

    def _get(self, endpoint: str) -> Optional[dict | list | str]:
        if not self._ensure_creds():
            return None
        try:
            r = requests.get(
                f"{self._creds['base_url']}{endpoint}",
                headers={"Authorization": self._creds["auth_header"]},
                verify=False,
                timeout=3,
            )
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            self._creds = None  # Reset si connexion perdue
            return None

    # -------------------------------------------------------------------------
    # Endpoints utiles
    # -------------------------------------------------------------------------

    def get_phase(self) -> str:
        """
        Retourne la phase actuelle du client.
        Valeurs: None, Lobby, Matchmaking, ChampSelect, InProgress,
                 WaitingForStats, PreEndOfGame, EndOfGame, Reconnect
        """
        data = self._get("/lol-gameflow/v1/gameflow-phase")
        if isinstance(data, str):
            return data
        return "None"

    def get_current_summoner(self) -> Optional[dict]:
        """Retourne les infos de l'invocateur connecté."""
        return self._get("/lol-summoner/v1/current-summoner")

    def get_champ_select_session(self) -> Optional[dict]:
        """
        Retourne la session de champion select complète.
        Contient: myTeam, theirTeam, actions (picks/bans), timer, localPlayerCellId
        """
        return self._get("/lol-champ-select/v1/session")

    def get_champion_name_by_id(self, champion_id: int) -> str:
        """Retourne le nom d'un champion via le LCU."""
        data = self._get(f"/lol-champions/v1/champions/{champion_id}")
        if data:
            return data.get("name", f"Champion#{champion_id}")
        return f"Champion#{champion_id}"

    def is_connected(self) -> bool:
        return self._ensure_creds() and self.get_phase() != "None"


# -------------------------------------------------------------------------
# Parser champion select
# -------------------------------------------------------------------------

def parse_champ_select(session: dict, my_cell_id: int = None) -> dict:
    """
    Extrait les infos utiles d'une session de champion select.

    Retourne:
    {
      my_cell_id, my_champion_id, my_team, their_team,
      bans, assigned_position, timer_left, phase
    }
    """
    if not session:
        return {}

    my_cell_id = my_cell_id or session.get("localPlayerCellId", -1)
    my_team    = session.get("myTeam", [])
    their_team = session.get("theirTeam", [])
    timer      = session.get("timer", {})

    # Mon champion
    me = next((p for p in my_team if p.get("cellId") == my_cell_id), {})
    my_champion_id = me.get("championId", 0)
    assigned_pos   = me.get("assignedPosition", "").upper()

    # Bans des deux équipes
    bans = []
    for action_group in session.get("actions", []):
        for action in action_group:
            if action.get("type") == "ban" and action.get("completed"):
                bans.append(action.get("championId", 0))

    def fmt_player(p: dict) -> dict:
        confirmed_id = p.get("championId", 0)
        intent_id    = p.get("championPickIntent", 0)
        return {
            "cell_id":      p.get("cellId"),
            "champion_id":  confirmed_id,
            "intent_id":    intent_id if intent_id != confirmed_id else 0,  # hover ≠ pick confirmé
            "is_confirmed": confirmed_id != 0,
            "position":     p.get("assignedPosition", "").upper(),
            "spell1":       p.get("spell1Id"),
            "spell2":       p.get("spell2Id"),
        }

    return {
        "my_cell_id":      my_cell_id,
        "my_champion_id":  my_champion_id,
        "my_position":     assigned_pos,
        "my_team":         [fmt_player(p) for p in my_team],
        "their_team":      [fmt_player(p) for p in their_team],
        "bans":            bans,
        "timer_left":      timer.get("adjustedTimeLeftInPhase", 0) / 1000,
        "phase":           timer.get("phase", ""),
    }
