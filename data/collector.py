"""
Collecteur de données — analyse les parties haut-elo via l'API Riot.
Pipeline: Challenger/GM/Master → match IDs → match details → stats agrégées.

Stratégie:
  1. Récupère les joueurs Challenger/GM/Master
  2. Récupère leurs dernières parties SoloQ
  3. Analyse chaque partie (champion, rôle, items, runes, résultat)
  4. Calcule win rates, builds optimaux, matchups
  5. Stocke en BDD locale
"""

import time
import json
from collections import defaultdict
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.riot_api import RiotAPIClient
from data.database import (
    init_db, upsert_build, upsert_matchup,
    get_conn, get_all_champions
)


# -------------------------------------------------------------------------
# Détection du rôle joué
# -------------------------------------------------------------------------

POSITION_MAP = {
    "TOP":     "TOP",
    "JUNGLE":  "JUNGLE",
    "MIDDLE":  "MID",
    "BOTTOM":  "ADC",
    "UTILITY": "SUPPORT",
}


def detect_role(participant: dict) -> str:
    """Extrait le rôle d'un participant depuis les données de partie."""
    pos = participant.get("teamPosition", "") or participant.get("individualPosition", "")
    return POSITION_MAP.get(pos.upper(), "UNKNOWN")


# -------------------------------------------------------------------------
# Récupération des joueurs haut-elo
# -------------------------------------------------------------------------

def get_high_elo_puuids(client: RiotAPIClient, max_players: int = 200) -> list[str]:
    """
    Récupère les PUUIDs des joueurs de tous les tiers avec un cap par tier.
    Distribution : Challenger (tous) + GM (tous) + Master (cap) + Diamond/Emerald/Platinum (cap chacun)
    """
    puuids = []
    per_tier_cap = max_players // 6  # cap par tier pour avoir un mix équilibré

    def collect_puuids(entries, cap):
        collected = []
        for entry in entries:
            puuid = entry.get("puuid")
            if puuid:
                collected.append(puuid)
            else:
                summoner_id = entry.get("summonerId")
                if summoner_id:
                    url_s = f"{client.base_url}/lol/summoner/v4/summoners/{summoner_id}"
                    summoner = client._get(url_s)
                    if summoner and summoner.get("puuid"):
                        collected.append(summoner["puuid"])
            if len(collected) >= cap:
                break
        return collected

    # Challenger / Grandmaster : tous les joueurs (peu nombreux)
    for url, name in [
        (f"{client.base_url}/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5", "Challenger"),
        (f"{client.base_url}/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5", "GrandMaster"),
    ]:
        data = client._get(url)
        if not data:
            continue
        entries = data.get("entries", [])
        collected = collect_puuids(entries, len(entries))
        puuids.extend(collected)
        print(f"  {name}: {len(collected)} joueurs collectés")
        time.sleep(0.5)

    # Master : cap pour laisser de la place aux autres tiers
    data = client._get(f"{client.base_url}/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5")
    if data:
        entries = data.get("entries", [])
        collected = collect_puuids(entries, per_tier_cap)
        puuids.extend(collected)
        print(f"  Master: {len(collected)} joueurs collectés (/{len(entries)} dispo)")
    time.sleep(0.5)

    # Diamond / Emerald / Platinum : endpoint /entries/{TIER}/{DIVISION}
    entry_tiers = [
        ("DIAMOND", "Diamond"),
        ("EMERALD", "Emerald"),
        ("PLATINUM", "Platinum"),
    ]
    divisions = ["I", "II", "III", "IV"]

    for tier, name in entry_tiers:
        tier_collected = []
        for division in divisions:
            if len(tier_collected) >= per_tier_cap:
                break
            page = 1
            while len(tier_collected) < per_tier_cap:
                url = (f"{client.base_url}/lol/league/v4/entries/RANKED_SOLO_5x5"
                       f"/{tier}/{division}?page={page}")
                entries = client._get(url)
                if not entries:
                    break
                for entry in entries:
                    puuid = entry.get("puuid")
                    if puuid:
                        tier_collected.append(puuid)
                    else:
                        summoner_id = entry.get("summonerId")
                        if summoner_id:
                            url_s = f"{client.base_url}/lol/summoner/v4/summoners/{summoner_id}"
                            summoner = client._get(url_s)
                            if summoner and summoner.get("puuid"):
                                tier_collected.append(summoner["puuid"])
                    if len(tier_collected) >= per_tier_cap:
                        break
                if len(entries) < 205:
                    break
                page += 1
                time.sleep(0.5)
        puuids.extend(tier_collected[:per_tier_cap])
        print(f"  {name}: {len(tier_collected[:per_tier_cap])} joueurs collectés")
        time.sleep(0.5)

    print(f"  Total PUUIDs collectés: {len(puuids)}")
    return puuids[:max_players]


# -------------------------------------------------------------------------
# Analyse d'une partie
# -------------------------------------------------------------------------

def analyze_match(match_data: dict) -> list[dict]:
    """
    Extrait les statistiques utiles de chaque participant d'une partie.
    Retourne une liste de dicts, un par participant.
    """
    if not match_data:
        return []

    info = match_data.get("info", {})
    participants = info.get("participants", [])
    version = info.get("gameVersion", "")
    parts = version.split(".")
    patch = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else version  # ex: "16.6"

    results = []

    for p in participants:
        role = detect_role(p)
        if role == "UNKNOWN":
            continue

        # Items achetés (slots 0-5, slot 6 = trinket)
        items = [
            p.get(f"item{i}") for i in range(6)
            if p.get(f"item{i}", 0) != 0
        ]

        # Runes
        perks = p.get("perks", {})
        styles = perks.get("styles", [])
        primary_style = styles[0].get("style") if len(styles) > 0 else None
        sub_style = styles[1].get("style") if len(styles) > 1 else None
        perk_ids = []
        for style in styles:
            for sel in style.get("selections", []):
                perk_ids.append(sel.get("perk"))

        results.append({
            "champion_id":    p.get("championId"),
            "role":           role,
            "win":            p.get("win", False),
            "items":          items,
            "primary_style":  primary_style,
            "sub_style":      sub_style,
            "perk_ids":       perk_ids,
            "team_id":        p.get("teamId"),
            "opponent_champion_id": None,  # rempli après
            "patch":          patch,
            # Stats détaillées
            "kills":          p.get("kills", 0),
            "deaths":         p.get("deaths", 0),
            "assists":        p.get("assists", 0),
            "cs":             p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "vision_score":   p.get("visionScore", 0),
            "damage":         p.get("totalDamageDealtToChampions", 0),
            "gold":           p.get("goldEarned", 0),
        })

    # Associe chaque joueur à son adversaire direct (même rôle, équipe opposée)
    for p in results:
        opponent = next(
            (q for q in results
             if q["role"] == p["role"]
             and q["team_id"] != p["team_id"]),
            None
        )
        if opponent:
            p["opponent_champion_id"] = opponent["champion_id"]

    return results


# -------------------------------------------------------------------------
# Agrégateur — construit les stats depuis les parties collectées
# -------------------------------------------------------------------------

class StatsAggregator:
    """
    Accumule les données de parties et calcule les statistiques agrégées.
    """

    def __init__(self):
        # {(champion_id, role, patch): {"wins": 0, "games": 0, "items": Counter, "runes": Counter}}
        self.champion_stats = defaultdict(lambda: {
            "wins": 0, "games": 0,
            "item_combos": defaultdict(lambda: {"wins": 0, "games": 0}),
            "rune_combos": defaultdict(lambda: {"wins": 0, "games": 0}),
        })
        # {(champion_id, enemy_id, role, patch): {"wins": 0, "games": 0}}
        self.matchup_stats = defaultdict(lambda: {"wins": 0, "games": 0})

    def add_match_results(self, results: list[dict]):
        """Ajoute les résultats d'une partie à l'agrégateur."""
        for p in results:
            key = (p["champion_id"], p["role"], p["patch"])
            stats = self.champion_stats[key]
            stats["games"] += 1
            if p["win"]:
                stats["wins"] += 1

            # Items (top 3 core items triés)
            core = tuple(sorted(p["items"][:3]))
            stats["item_combos"][core]["games"] += 1
            if p["win"]:
                stats["item_combos"][core]["wins"] += 1

            # Runes
            rune_key = (p["primary_style"], p["sub_style"], tuple(p["perk_ids"][:4]))
            stats["rune_combos"][rune_key]["games"] += 1
            if p["win"]:
                stats["rune_combos"][rune_key]["wins"] += 1

            # Matchup
            if p["opponent_champion_id"]:
                mu_key = (p["champion_id"], p["opponent_champion_id"], p["role"], p["patch"])
                self.matchup_stats[mu_key]["games"] += 1
                if p["win"]:
                    self.matchup_stats[mu_key]["wins"] += 1

    def save_to_db(self, min_games: int = 50):
        """Sauvegarde les statistiques agrégées en BDD."""
        saved_builds = 0
        saved_matchups = 0

        # --- Builds ---
        for (champion_id, role, patch), stats in self.champion_stats.items():
            if stats["games"] < min_games:
                continue

            win_rate = stats["wins"] / stats["games"]

            # Meilleur combo d'items
            best_items = max(
                stats["item_combos"].items(),
                key=lambda x: (x[1]["wins"] / x[1]["games"]) if x[1]["games"] >= 20 else 0,
                default=([], {"wins": 0, "games": 0})
            )
            core_items = list(best_items[0]) if best_items[0] else []

            # Meilleures runes
            best_runes = max(
                stats["rune_combos"].items(),
                key=lambda x: (x[1]["wins"] / x[1]["games"]) if x[1]["games"] >= 20 else 0,
                default=((None, None, ()), {"wins": 0, "games": 0})
            )
            rune_key = best_runes[0]

            build_data = {
                "primary_style":  rune_key[0],
                "sub_style":      rune_key[1],
                "perk_ids":       list(rune_key[2]),
                "starter_items":  [],
                "core_items":     core_items,
                "boots":          None,
                "situational":    [],
                "games_played":   stats["games"],
                "win_rate":       win_rate,
                "pick_rate":      0.0,
            }

            upsert_build(champion_id, role, patch, build_data)
            saved_builds += 1

        # --- Matchups ---
        for (champion_id, enemy_id, role, patch), stats in self.matchup_stats.items():
            if stats["games"] < min_games:
                continue
            win_rate = stats["wins"] / stats["games"]
            upsert_matchup(champion_id, enemy_id, role, patch, stats["games"], win_rate)
            saved_matchups += 1

        print(f"[Aggregator] Builds sauvegardés: {saved_builds}")
        print(f"[Aggregator] Matchups sauvegardés: {saved_matchups}")


# -------------------------------------------------------------------------
# Agrégation depuis la BDD
# -------------------------------------------------------------------------

def aggregate_from_db(min_games: int = 50, patch: str = None):
    """
    Relit les raw_participants depuis la BDD et recalcule tous les builds/matchups.
    Peut être appelé indépendamment de la collecte.
    """
    patch = patch or config.CURRENT_PATCH

    with get_conn() as conn:
        # Debug : compte les lignes brutes disponibles
        total = conn.execute("SELECT COUNT(*) FROM raw_participants").fetchone()[0]
        sample = conn.execute("SELECT patch, COUNT(*) as n FROM raw_participants GROUP BY patch").fetchall()
        print(f"  raw_participants: {total} lignes")
        for row in sample:
            print(f"    patch={row[0]} → {row[1]} lignes")

        # --- Builds : meilleur win rate par champion/role ---
        rows = conn.execute("""
            SELECT champion_id, role, patch,
                   COUNT(*) as games,
                   SUM(win) as wins,
                   primary_style, sub_style, perk_ids, items
            FROM raw_participants
            WHERE patch LIKE ?
            GROUP BY champion_id, role, patch, primary_style, sub_style, perk_ids, items
        """, (patch.split(".")[0] + "%",)).fetchall()

        # Regroupe par (champion_id, role, patch)
        from collections import defaultdict
        champ_stats = defaultdict(lambda: {"games": 0, "wins": 0, "combos": []})

        for row in rows:
            key = (row["champion_id"], row["role"], row["patch"])
            champ_stats[key]["games"] += row["games"]
            champ_stats[key]["wins"] += row["wins"]
            champ_stats[key]["combos"].append(row)

        builds_saved = 0
        for (champion_id, role, patch_val), stats in champ_stats.items():
            if stats["games"] < min_games:
                continue

            # Meilleur combo runes+items par win rate
            best = max(
                stats["combos"],
                key=lambda r: (r["wins"] / r["games"]) if r["games"] >= max(1, min_games // 5) else 0
            )

            build_data = {
                "primary_style":  best["primary_style"],
                "sub_style":      best["sub_style"],
                "perk_ids":       json.loads(best["perk_ids"] or "[]"),
                "starter_items":  [],
                "core_items":     json.loads(best["items"] or "[]")[:3],
                "boots":          None,
                "situational":    [],
                "games_played":   stats["games"],
                "win_rate":       stats["wins"] / stats["games"],
                "pick_rate":      0.0,
            }
            upsert_build(champion_id, role, patch_val, build_data)
            builds_saved += 1

        # --- Matchups ---
        matchup_rows = conn.execute("""
            SELECT champion_id, opponent_id, role, patch,
                   COUNT(*) as games, SUM(win) as wins
            FROM raw_participants
            WHERE opponent_id IS NOT NULL AND patch LIKE ?
            GROUP BY champion_id, opponent_id, role, patch
            HAVING games >= ?
        """, (patch.split(".")[0] + "%", min_games)).fetchall()

        matchups_saved = 0
        for row in matchup_rows:
            wr = row["wins"] / row["games"]
            upsert_matchup(row["champion_id"], row["opponent_id"],
                           row["role"], row["patch"], row["games"], wr)
            matchups_saved += 1

    print(f"[Aggregator] Builds sauvegardés: {builds_saved}")
    print(f"[Aggregator] Matchups sauvegardés: {matchups_saved}")


# -------------------------------------------------------------------------
# Pipeline principal
# -------------------------------------------------------------------------

def run_collection(max_players: int = 50, matches_per_player: int = 20,
                   queue: int = 420, min_games_threshold: int = 50):
    """
    Pipeline complet de collecte de données.

    max_players         : nombre de joueurs haut-elo à analyser
    matches_per_player  : parties à analyser par joueur
    queue               : 420 = SoloQ, 440 = FlexQ
    """
    client = RiotAPIClient()
    aggregator = StatsAggregator()

    print("=== Collecte de données haut-elo ===")
    print(f"Joueurs: {max_players} | Parties/joueur: {matches_per_player} | Queue: {'SoloQ' if queue==420 else 'FlexQ'}\n")

    # 1. Récupère les joueurs haut-elo
    print("[1/3] Récupération des joueurs haut-elo...")
    puuids = get_high_elo_puuids(client, max_players)

    if not puuids:
        print("[!] Aucun joueur trouvé. Vérifie ta clé API et ta région.")
        return

    # 2. Collecte et analyse les parties
    print(f"\n[2/3] Analyse des parties ({len(puuids)} joueurs)...")
    analyzed = 0
    skipped = 0

    with get_conn() as conn:
        for i, puuid in enumerate(puuids, 1):
            print(f"  Joueur {i}/{len(puuids)}...", end=" ", flush=True)

            match_ids = client.get_match_ids(puuid, count=matches_per_player, queue=queue)

            player_analyzed = 0
            for match_id in match_ids:
                # Vérifie si déjà analysé
                existing = conn.execute(
                    "SELECT 1 FROM analyzed_matches WHERE match_id = ?", (match_id,)
                ).fetchone()

                if existing:
                    skipped += 1
                    continue

                match_data = client.get_match(match_id)
                if not match_data:
                    continue

                results = analyze_match(match_data)

                # Sauvegarde les données brutes en BDD
                for p in results:
                    conn.execute("""
                        INSERT INTO raw_participants
                            (match_id, champion_id, role, win, items,
                             primary_style, sub_style, perk_ids, opponent_id, patch)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        match_id,
                        p["champion_id"],
                        p["role"],
                        1 if p["win"] else 0,
                        json.dumps(p["items"]),
                        p["primary_style"],
                        p["sub_style"],
                        json.dumps(p["perk_ids"]),
                        p["opponent_champion_id"],
                        p["patch"],
                    ))

                conn.execute(
                    "INSERT OR IGNORE INTO analyzed_matches (match_id, patch) VALUES (?, ?)",
                    (match_id, config.CURRENT_PATCH)
                )
                analyzed += 1
                player_analyzed += 1

            print(f"{player_analyzed} parties analysées")
            time.sleep(0.1)

    print(f"\n  Total: {analyzed} nouvelles parties | {skipped} déjà analysées")

    # 3. Agrège et sauvegarde les statistiques depuis la BDD
    print("\n[3/3] Agrégation des statistiques...")
    aggregate_from_db(min_games=min_games_threshold)

    print("\n=== Collecte terminée ===")


# -------------------------------------------------------------------------
# TEST RAPIDE
# -------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()

    # Test léger : 5 joueurs, 5 parties chacun (min_games=1 pour voir les résultats)
    run_collection(max_players=5000, matches_per_player=100, min_games_threshold=50)
