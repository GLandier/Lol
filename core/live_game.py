"""
Live Game Monitor — surveille la partie via l'API locale LoL (127.0.0.1:2999).

L'API Live Client Data de Riot tourne localement pendant une partie et donne :
  - Les 10 joueurs avec leurs items, scores, rôles
  - Le vrai temps de jeu
  - Les événements (dragon, baron, inhibiteur...)

Endpoints utilisés:
  GET https://127.0.0.1:2999/liveclientdata/allgamedata
  GET https://127.0.0.1:2999/liveclientdata/eventdata
  GET https://127.0.0.1:2999/liveclientdata/gamestats
"""

import time
import threading
import requests
import urllib3
from enum import Enum
from typing import Callable, Optional

# Désactive le warning SSL (l'API locale utilise un certificat auto-signé)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from data.database import get_champion_name, get_conn


LOCAL_API = "https://127.0.0.1:2999/liveclientdata"


class GameState(Enum):
    IDLE       = "idle"
    IN_GAME    = "in_game"
    GAME_ENDED = "game_ended"


# -------------------------------------------------------------------------
# Client API locale
# -------------------------------------------------------------------------

def _local_get(endpoint: str) -> Optional[dict]:
    """GET sur l'API locale LoL. Retourne None si LoL n'est pas en partie."""
    try:
        r = requests.get(
            f"{LOCAL_API}/{endpoint}",
            verify=False,   # certificat auto-signé
            timeout=2,
        )
        if r.status_code == 200:
            return r.json()
        return None
    except requests.exceptions.ConnectionError:
        return None  # LoL pas lancé ou pas en partie
    except Exception:
        return None


def get_all_game_data() -> Optional[dict]:
    return _local_get("allgamedata")

def get_game_stats() -> Optional[dict]:
    return _local_get("gamestats")

def get_event_data() -> Optional[dict]:
    return _local_get("eventdata")

def get_player_list() -> Optional[list]:
    return _local_get("playerlist")


# -------------------------------------------------------------------------
# Parsing des données
# -------------------------------------------------------------------------

TEAM_MAP = {"ORDER": "blue", "CHAOS": "red"}

def parse_game_data(data: dict, my_summoner_name: str = "") -> dict:
    """
    Transforme les données brutes de l'API locale en dict propre.
    """
    if not data:
        return {}

    game_data  = data.get("gameData", {})
    active_player = data.get("activePlayer", {})
    all_players   = data.get("allPlayers", [])

    game_time = game_data.get("gameTime", 0)

    # Identifie le joueur local
    my_name = (active_player.get("riotIdGameName") or
               active_player.get("summonerName") or
               my_summoner_name or "")

    me = next(
        (p for p in all_players
         if p.get("riotIdGameName", "") == my_name
         or p.get("summonerName", "") == my_name),
        all_players[0] if all_players else {}
    )

    my_team_id = me.get("team", "ORDER")

    my_team    = [p for p in all_players if p.get("team") == my_team_id]
    enemy_team = [p for p in all_players if p.get("team") != my_team_id]

    def format_player(p: dict) -> dict:
        items = [
            {"id": item.get("itemID"), "name": item.get("displayName", "")}
            for item in p.get("items", [])
        ]
        scores = p.get("scores", {})
        return {
            "summoner_name": p.get("riotIdGameName") or p.get("summonerName", "?"),
            "champion_name": p.get("championName", ""),
            "team":          p.get("team", ""),
            "position":      p.get("position", ""),
            "is_dead":       p.get("isDead", False),
            "respawn_timer": p.get("respawnTimer", 0),
            "level":         p.get("level", 1),
            "items":         items,
            "item_ids":      [item.get("itemID") for item in p.get("items", [])],
            "kills":         scores.get("kills", 0),
            "deaths":        scores.get("deaths", 0),
            "assists":       scores.get("assists", 0),
            "cs":            scores.get("creepScore", 0),
            "ward_score":    scores.get("wardScore", 0.0),
        }

    minutes = int(game_time) // 60
    seconds = int(game_time) % 60

    return {
        "game_time":      game_time,
        "game_time_str":  f"{minutes:02d}:{seconds:02d}",
        "game_mode":      game_data.get("gameMode", ""),
        "map_number":     game_data.get("mapNumber", 0),
        "my_summoner":    my_name,
        "my_champion":    me.get("championName", ""),
        "my_team":        [format_player(p) for p in my_team],
        "enemy_team":     [format_player(p) for p in enemy_team],
        "enemy_champions": [p.get("championName", "") for p in enemy_team],
        "enemy_items":    {
            p.get("riotIdGameName") or p.get("summonerName", "?"): [
                item.get("itemID") for item in p.get("items", [])
            ]
            for p in enemy_team
        },
        # Stats du joueur local (plus détaillées)
        "my_stats": {
            "level":        active_player.get("level", 1),
            "current_gold": active_player.get("currentGold", 0),
            "abilities":    active_player.get("abilities", {}),
        },
    }


# -------------------------------------------------------------------------
# Tracker d'événements (dragon, baron, inhibiteur...)
# -------------------------------------------------------------------------

class EventTracker:
    """Analyse les événements de jeu pour les timers d'objectifs."""

    RESPAWN_TIMERS = {
        "DragonKill":       300,
        "BaronKill":        360,
        "HeraldKill":       360,
        "InhibitorKilled":  300,
        "ElderDragonKill":  360,
    }

    def __init__(self):
        self._seen_event_ids: set[int] = set()
        self.objective_kills: list[dict] = []
        self.ward_timers: list[dict] = []

    def process_events(self, event_data: Optional[dict]) -> list[dict]:
        """
        Traite les nouveaux événements.
        Retourne la liste des nouveaux événements d'objectifs.
        """
        if not event_data:
            return []

        events = event_data.get("Events", [])
        new_events = []

        for event in events:
            eid = event.get("EventID", -1)
            if eid in self._seen_event_ids:
                continue
            self._seen_event_ids.add(eid)

            etype = event.get("EventName", "")

            # Objectifs
            if etype in self.RESPAWN_TIMERS:
                entry = {
                    "type":        etype,
                    "time":        event.get("EventTime", 0),
                    "respawn_in":  self.RESPAWN_TIMERS[etype],
                    "spawns_at":   event.get("EventTime", 0) + self.RESPAWN_TIMERS[etype],
                    "killer":      event.get("KillerName", ""),
                    "stolen":      event.get("Stolen", False),
                }
                self.objective_kills.append(entry)
                new_events.append(entry)

                etype_clean = etype.replace("Kill", "").replace("Killed", "")
                t = event.get("EventTime", 0)
                respawn = entry["spawns_at"]
                print(f"[Event] {etype_clean} tué à {int(t)//60:02d}:{int(t)%60:02d} "
                      f"→ respawn à {int(respawn)//60:02d}:{int(respawn)%60:02d}")

            # Wards ennemies placées
            elif etype == "WardPlaced":
                placer = event.get("WardPlacedBy", "")
                ward_type = event.get("WardType", "")
                t = event.get("EventTime", 0)
                # Durée selon type : SightWard=90s, JammerDevice=permanent (on ignore)
                if ward_type in ("SightWard", "YellowTrinket"):
                    duration = 90
                    self.ward_timers.append({
                        "id":       eid,
                        "placer":   placer,
                        "type":     ward_type,
                        "placed_at": t,
                        "expires_at": t + duration,
                    })

            # Ward tuée → on la retire
            elif etype == "WardKill":
                killed_by = event.get("KillerName", "")
                # Marque la ward la plus ancienne comme tuée
                if self.ward_timers:
                    self.ward_timers.pop(0)

        return new_events

    def get_active_wards(self, current_time: float) -> list[dict]:
        """Retourne les wards ennemies encore actives avec leur temps restant."""
        active = []
        for w in self.ward_timers:
            time_left = w["expires_at"] - current_time
            if time_left > 0:
                active.append({
                    "placer":         w["placer"],
                    "type":           w["type"],
                    "time_left":      time_left,
                    "time_left_str":  f"{int(time_left)//60:02d}:{int(time_left)%60:02d}",
                    "placed_at_str":  f"{int(w['placed_at'])//60:02d}:{int(w['placed_at'])%60:02d}",
                })
        # Nettoie les wards expirées
        self.ward_timers = [w for w in self.ward_timers if w["expires_at"] > current_time]
        return active

    def get_active_timers(self, current_time: float) -> list[dict]:
        """Retourne les objectifs avec leur timer restant."""
        result = []
        # Garde seulement le dernier kill de chaque type
        seen_types: dict[str, dict] = {}
        for kill in self.objective_kills:
            seen_types[kill["type"]] = kill

        for etype, kill in seen_types.items():
            time_left = kill["spawns_at"] - current_time
            result.append({
                "type":          etype,
                "is_up":         time_left <= 0,
                "time_left":     max(0, time_left),
                "time_left_str": f"{max(0,int(time_left))//60:02d}:{max(0,int(time_left))%60:02d}",
                "spawns_at":     kill["spawns_at"],
                "killer":        kill["killer"],
            })
        return result


# -------------------------------------------------------------------------
# Monitor principal
# -------------------------------------------------------------------------

class LiveGameMonitor:
    """
    Surveille la partie via l'API locale LoL (127.0.0.1:2999).
    Poll toutes les LIVE_POLL_INTERVAL secondes.
    """

    def __init__(self, summoner_name: str = ""):
        self.summoner_name = summoner_name
        self.state = GameState.IDLE
        self.current_data: Optional[dict] = None
        self.event_tracker = EventTracker()

        self._on_game_start:  list[Callable] = []
        self._on_game_update: list[Callable] = []
        self._on_game_end:    list[Callable] = []

        self._running = False
        self._thread: Optional[threading.Thread] = None

    # Callbacks
    def on_game_start(self, fn: Callable):   self._on_game_start.append(fn)
    def on_game_update(self, fn: Callable):  self._on_game_update.append(fn)
    def on_game_end(self, fn: Callable):     self._on_game_end.append(fn)

    def _emit(self, callbacks: list[Callable], data: dict):
        for fn in callbacks:
            try:
                fn(data)
            except Exception as e:
                print(f"[LiveGame] Erreur callback: {e}")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[LiveGame] Monitoring démarré (API locale 127.0.0.1:2999)")
        print("[LiveGame] Lance LoL et entre en partie pour commencer.\n")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[LiveGame] Erreur: {e}")
            time.sleep(config.LIVE_POLL_INTERVAL)

    def _tick(self):
        raw = get_all_game_data()

        if raw is None:
            if self.state == GameState.IN_GAME:
                self.state = GameState.GAME_ENDED
                print("[LiveGame] Partie terminée.")
                self._emit(self._on_game_end, self.current_data or {})
                self.current_data = None
                self.event_tracker = EventTracker()
            self.state = GameState.IDLE
            return

        # Traite les événements
        events = self.event_tracker.process_events(get_event_data())

        # Parse les données
        parsed = parse_game_data(raw, self.summoner_name)
        parsed["objective_timers"] = self.event_tracker.get_active_timers(parsed["game_time"])
        parsed["new_events"] = events

        if self.state == GameState.IDLE:
            self.state = GameState.IN_GAME
            self.current_data = parsed
            print(f"[LiveGame] Partie détectée !")
            print(f"  Champion : {parsed['my_champion']}")
            print(f"  Alliés   : {[p['champion_name'] for p in parsed['my_team']]}")
            print(f"  Ennemis  : {parsed['enemy_champions']}")
            self._emit(self._on_game_start, parsed)
        else:
            self.current_data = parsed
            self._emit(self._on_game_update, parsed)

    def get_enemy_item_ids(self) -> list[int]:
        """Retourne tous les IDs d'items achetés par les ennemis."""
        if not self.current_data:
            return []
        items = []
        for item_list in self.current_data.get("enemy_items", {}).values():
            items.extend(item_list)
        return list(set(items))

    def get_game_time(self) -> float:
        if not self.current_data:
            return 0.0
        return self.current_data.get("game_time", 0.0)

    def is_in_game(self) -> bool:
        return self.state == GameState.IN_GAME


# -------------------------------------------------------------------------
# TEST
# -------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Test Live Game Monitor (API locale) ===")
    print("Lance LoL et entre en partie, puis relance ce script.\n")

    monitor = LiveGameMonitor()

    def on_start(data):
        print(f"\n=== PARTIE DÉMARRÉE ===")
        print(f"Champion  : {data['my_champion']}")
        print(f"Ennemis   : {data['enemy_champions']}")
        print(f"Mode      : {data['game_mode']}")

    def on_update(data):
        t = data['game_time_str']
        ennemis = ", ".join(
            f"{p['champion_name']}({p['kills']}/{p['deaths']}/{p['assists']})"
            for p in data['enemy_team']
        )
        timers = data.get('objective_timers', [])
        timer_str = " | ".join(
            f"{obj['type'].replace('Kill','')}: {'UP' if obj['is_up'] else obj['time_left_str']}"
            for obj in timers
        ) if timers else "aucun objectif tué"
        print(f"[{t}] {ennemis}")
        if timers:
            print(f"  Timers: {timer_str}")

    def on_end(data):
        print("\n=== PARTIE TERMINÉE ===")

    monitor.on_game_start(on_start)
    monitor.on_game_update(on_update)
    monitor.on_game_end(on_end)
    monitor.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()
        print("Arrêté.")
