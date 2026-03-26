"""
Pick Advisor — recommande le champion à jouer et le build optimal.

Fonctions:
  1. Counter-pick : meilleur champion à jouer contre l'ennemi d'en face
  2. Build recommandé : runes + items basés sur les données mondiales
  3. Build adaptatif : ajuste les items en fonction de ce qu'achètent les ennemis
"""

import json
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from data.database import get_conn, get_champion_name, get_all_champions


# -------------------------------------------------------------------------
# Noms d'items (cache local depuis Data Dragon)
# -------------------------------------------------------------------------

_item_cache: dict[int, str] = {}
_rune_cache: dict[int, str] = {}
_rune_tree_cache: dict[int, str] = {}  # keystone_id -> tree_name

def get_item_name(item_id: int) -> str:
    if not _item_cache:
        _load_item_cache()
    return _item_cache.get(item_id, f"Item#{item_id}")

def get_rune_name(rune_id: int) -> str:
    if not _rune_cache:
        _load_rune_cache()
    return _rune_cache.get(rune_id, f"Rune#{rune_id}")

def get_rune_tree_name(rune_id: int) -> str:
    """Retourne le nom du arbre primaire pour un keystone donné."""
    if not _rune_tree_cache:
        _load_rune_cache()
    return _rune_tree_cache.get(rune_id, "")

def _load_item_cache():
    try:
        import requests
        r = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{config.CURRENT_PATCH}.1/data/fr_FR/item.json",
            timeout=10
        )
        if r.status_code == 200:
            for iid, item in r.json().get("data", {}).items():
                _item_cache[int(iid)] = item.get("name", f"Item#{iid}")
    except Exception:
        pass

def _load_rune_cache():
    try:
        import requests
        r = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{config.CURRENT_PATCH}.1/data/fr_FR/runesReforged.json",
            timeout=10
        )
        if r.status_code == 200:
            for tree in r.json():
                tree_name = tree.get("name", "")
                tree_id   = tree.get("id")
                _rune_cache[tree_id] = tree_name
                for slot in tree.get("slots", []):
                    for rune in slot.get("runes", []):
                        rid = rune.get("id")
                        _rune_cache[rid] = rune.get("name", f"Rune#{rid}")
                        # Toutes les runes d'un arbre → arbre (pour trouver l'arbre secondaire)
                        _rune_tree_cache[rid] = tree_name
    except Exception:
        pass


# -------------------------------------------------------------------------
# 1. Counter-pick
# -------------------------------------------------------------------------

def get_counters(enemy_champion_id: int, role: str,
                 patch: str = None, top_n: int = 5) -> list[dict]:
    """
    Retourne les meilleurs champions à jouer contre un ennemi dans un rôle.

    enemy_champion_id : ID du champion adverse
    role              : TOP, JUNGLE, MID, ADC, SUPPORT
    top_n             : nombre de suggestions

    Retourne: [{"champion_id", "champion_name", "win_rate", "games_played", "advantage"}]
    """
    patch = patch or config.CURRENT_PATCH

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.champion_id,
                   c.name AS champion_name,
                   m.win_rate,
                   m.games_played,
                   (m.win_rate - 0.5) * 100 AS advantage
            FROM matchups m
            JOIN champions c ON c.id = m.champion_id
            WHERE m.enemy_id   = ?
              AND m.role        = ?
              AND m.patch      LIKE ?
              AND m.games_played >= 10
            ORDER BY m.win_rate DESC
            LIMIT ?
        """, (enemy_champion_id, role.upper(), patch.split(".")[0] + "%", top_n)).fetchall()

    return [dict(r) for r in rows]


def get_best_pick_vs_team(enemy_champion_ids: list[int], role: str,
                           patch: str = None, top_n: int = 3) -> list[dict]:
    """
    Trouve le meilleur champion à jouer contre TOUTE l'équipe adverse.
    Calcule un score global (somme des win rates contre chaque ennemi).

    enemy_champion_ids : liste des IDs des champions ennemis
    role               : rôle du joueur
    """
    if not enemy_champion_ids:
        return []

    patch = patch or config.CURRENT_PATCH
    patch_filter = patch.split(".")[0] + "%"

    # Score = somme des win rates contre chaque ennemi présent
    # (On cherche le champion avec le meilleur win rate moyen contre l'équipe)
    placeholders = ",".join("?" * len(enemy_champion_ids))

    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT m.champion_id,
                   c.name AS champion_name,
                   AVG(m.win_rate) AS avg_win_rate,
                   COUNT(*)        AS matchups_found,
                   SUM(m.games_played) AS total_games
            FROM matchups m
            JOIN champions c ON c.id = m.champion_id
            WHERE m.enemy_id IN ({placeholders})
              AND m.role      = ?
              AND m.patch    LIKE ?
              AND m.games_played >= 10
            GROUP BY m.champion_id
            ORDER BY avg_win_rate DESC
            LIMIT ?
        """, (*enemy_champion_ids, role.upper(), patch_filter, top_n)).fetchall()

    return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# 1b. Bans recommandés
# -------------------------------------------------------------------------

def get_ban_suggestions(role: str, patch: str = None, top_n: int = 5) -> list[dict]:
    """
    Retourne les champions à bannir en priorité pour un rôle donné.
    Logique : champions avec le win rate le plus élevé quand ils sont dans ce rôle,
    c'est-à-dire les champions les plus forts contre lesquels jouer.
    """
    patch = patch or config.CURRENT_PATCH

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.enemy_id       AS champion_id,
                   c.name           AS champion_name,
                   AVG(m.win_rate)  AS avg_win_rate,
                   SUM(m.games_played) AS total_games
            FROM matchups m
            JOIN champions c ON c.id = m.enemy_id
            WHERE m.role       = ?
              AND m.patch      LIKE ?
              AND m.games_played >= 10
            GROUP BY m.enemy_id
            ORDER BY avg_win_rate DESC
            LIMIT ?
        """, (role.upper(), patch.split(".")[0] + "%", top_n)).fetchall()

    return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# 2. Build recommandé
# -------------------------------------------------------------------------

def get_recommended_build(champion_id: int, role: str,
                           patch: str = None) -> Optional[dict]:
    """
    Retourne le build optimal (runes + items) pour un champion/rôle.
    """
    patch = patch or config.CURRENT_PATCH

    with get_conn() as conn:
        # Priorité : win_rate DESC avec min 20 parties ; fallback sans seuil
        row = conn.execute("""
            SELECT * FROM builds
            WHERE champion_id = ?
              AND role        = ?
              AND patch      LIKE ?
              AND games_played >= 20
            ORDER BY win_rate DESC
            LIMIT 1
        """, (champion_id, role.upper(), patch.split(".")[0] + "%")).fetchone()

        if not row:
            row = conn.execute("""
                SELECT * FROM builds
                WHERE champion_id = ?
                  AND role        = ?
                  AND patch      LIKE ?
                ORDER BY games_played DESC
                LIMIT 1
            """, (champion_id, role.upper(), patch.split(".")[0] + "%")).fetchone()

    if not row:
        return None

    build = dict(row)
    for field in ["perk_ids", "starter_items", "core_items", "situational"]:
        if build.get(field):
            try:
                build[field] = json.loads(build[field])
            except Exception:
                build[field] = []

    # Ajoute les noms des items
    build["core_items_named"] = [
        {"id": iid, "name": get_item_name(iid)}
        for iid in build.get("core_items", [])
        if iid
    ]

    # Ajoute les noms des runes
    perk_ids = build.get("perk_ids", [])
    if perk_ids:
        keystone_id = perk_ids[0] if perk_ids else None
        build["keystone_name"]    = get_rune_name(keystone_id) if keystone_id else ""
        build["primary_tree"]     = get_rune_tree_name(keystone_id) if keystone_id else ""
        build["rune_names"]       = [get_rune_name(rid) for rid in perk_ids]
        # Sépare primaires (0-3) et secondaires (4-5)
        build["primary_runes"]    = build["rune_names"][:4]
        build["secondary_runes"]  = build["rune_names"][4:6]

    return build


# -------------------------------------------------------------------------
# 3. Build adaptatif (selon les items ennemis)
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# E. Power spike items — alertes quand un ennemi achète un item dangereux
# -------------------------------------------------------------------------

POWER_SPIKE_ITEMS: dict[int, dict] = {
    # Létalité / AD burst
    3142: {"name": "Yomumu",          "type": "lethality", "alert": "!! Spike létalité — attention aux assassins"},
    3147: {"name": "Edge of Night",   "type": "lethality", "alert": "!! Spike létalité + spell shield"},
    6692: {"name": "Eclipse",         "type": "lethality", "alert": "!! Spike létalité bruiser"},
    6693: {"name": "Prowler's Claw",  "type": "lethality", "alert": "!! Spike létalité assassin"},
    3814: {"name": "Edge of Night",   "type": "lethality", "alert": "!! Spike létalité + shield"},
    # AP burst
    3089: {"name": "Rabadon",         "type": "ap_burst",  "alert": "!! Spike AP majeur — dégâts × 1.35"},
    4645: {"name": "Shadowflame",     "type": "ap_burst",  "alert": "!! Spike AP burst"},
    3135: {"name": "Void Staff",      "type": "ap_pen",    "alert": "!! Pénétration magique — soigne moins"},
    # Sustain / tank spike
    3078: {"name": "Trinity Force",   "type": "bruiser",   "alert": "!! Spike bruiser — Spellblade actif"},
    6632: {"name": "Divine Sunderer", "type": "bruiser",   "alert": "!! Spike bruiser — % santé max"},
    3071: {"name": "Black Cleaver",   "type": "bruiser",   "alert": "!! Black Cleaver — réduction armure stack"},
    # Crit spike
    3031: {"name": "Infinity Edge",   "type": "crit",      "alert": "!! Spike crit — dégâts critiques +35%"},
    3046: {"name": "Phantom Dancer",  "type": "crit",      "alert": "!! Spike crit + shield vie basse"},
    # Support power spike
    3190: {"name": "Locket",          "type": "support",   "alert": "!! Bouclier AOE ennemi actif"},
    3107: {"name": "Redemption",      "type": "support",   "alert": "!! Heal AOE ennemi — anti-heal recommandé"},
}

# Items déjà achetés vus (pour ne pas répéter les alertes)
_seen_spike_items: set[int] = set()


def get_power_spike_alerts(enemy_item_ids: list[int], reset: bool = False) -> list[dict]:
    """
    Retourne les nouvelles alertes de power spike pour les items ennemis.
    Ne retourne chaque alerte qu'une seule fois par partie (reset=True en début de partie).
    """
    global _seen_spike_items
    if reset:
        _seen_spike_items = set()

    alerts = []
    for iid in enemy_item_ids:
        if iid in POWER_SPIKE_ITEMS and iid not in _seen_spike_items:
            _seen_spike_items.add(iid)
            alerts.append(POWER_SPIKE_ITEMS[iid])
    return alerts


# -------------------------------------------------------------------------
# Règles d'adaptation : {item_ennemi_tag → items conseillés}
# Basé sur les stats de LoL : si l'ennemi achète X, tu devrais acheter Y
ADAPTIVE_RULES = {
    # Ennemis full AD → armure
    "heavy_ad": {
        "trigger_items": [
            3031,  # Infinity Edge
            3036,  # Lord Dominik's Regards
            3033,  # Mortal Reminder
            3035,  # Last Whisper
            3071,  # Black Cleaver
            6692,  # Eclipse
            6693,  # Prowler's Claw
        ],
        "counter_items": [
            3143,  # Randuin's Omen
            3110,  # Frozen Heart
            3082,  # Warden's Mail
            3075,  # Thornmail
            3742,  # Dead Man's Plate
        ],
        "tip": "L'ennemi est full AD → achète de l'armure"
    },
    # Ennemis full AP → résistance magique
    "heavy_ap": {
        "trigger_items": [
            3089,  # Rabadon's Deathcap
            3135,  # Void Staff
            4645,  # Shadowflame
            3165,  # Morellonomicon
            3116,  # Rylai's Crystal Scepter
            6653,  # Liandry's Torment
        ],
        "counter_items": [
            3156,  # Maw of Malmortius
            3139,  # Mercurial Scimitar
            3091,  # Wit's End
            3111,  # Mercury's Treads
            3194,  # Adaptive Helm
            4401,  # Force of Nature
        ],
        "tip": "L'ennemi est full AP → achète de la résistance magique"
    },
    # Ennemis avec beaucoup de soins → anti-heal
    "heavy_heal": {
        "trigger_items": [
            3174,  # Athene's Unholy Grail
            3107,  # Redemption
            3504,  # Ardent Censer
            2065,  # Shurelya's Battlesong
        ],
        "counter_items": [
            3075,  # Thornmail
            3033,  # Mortal Reminder
            3123,  # Executioner's Calling
            3165,  # Morellonomicon
        ],
        "tip": "L'ennemi soigne beaucoup → achète de l'anti-heal"
    },
    # Ennemis avec boucliers → brise-bouclier
    "heavy_shields": {
        "trigger_items": [
            3190,  # Locket of the Iron Solari
            8001,  # Anathema's Chains
            3109,  # Knight's Vow
        ],
        "counter_items": [
            6035,  # Serpent's Fang
            3155,  # Hexdrinker
        ],
        "tip": "L'ennemi a des boucliers → achète Serpent's Fang"
    },
}


def get_adaptive_recommendations(champion_id: int, role: str,
                                  enemy_item_ids: list[int],
                                  patch: str = None) -> list[dict]:
    """
    Analyse les items achetés par les ennemis et recommande des adaptations.

    Retourne une liste de recommandations triées par priorité.
    """
    if not enemy_item_ids:
        return []

    patch = patch or config.CURRENT_PATCH
    recommendations = []
    enemy_set = set(enemy_item_ids)

    for rule_name, rule in ADAPTIVE_RULES.items():
        # Compte combien de trigger items l'ennemi a
        triggers_found = [iid for iid in rule["trigger_items"] if iid in enemy_set]

        if len(triggers_found) >= 2:
            priority = len(triggers_found)
            recommendations.append({
                "rule":          rule_name,
                "priority":      priority,
                "tip":           rule["tip"],
                "trigger_items": [{"id": iid, "name": get_item_name(iid)} for iid in triggers_found],
                "counter_items": [{"id": iid, "name": get_item_name(iid)} for iid in rule["counter_items"]],
            })

    # Trie par priorité (plus de triggers = plus urgent)
    recommendations.sort(key=lambda x: x["priority"], reverse=True)

    # Complète avec les données de la BDD si disponibles
    with get_conn() as conn:
        if enemy_item_ids:
            placeholders = ",".join("?" * len(enemy_item_ids))
            db_recs = conn.execute(f"""
                SELECT ab.counter_item_id, i.name, ab.win_rate_delta, ab.priority
                FROM adaptive_builds ab
                LEFT JOIN items i ON i.id = ab.counter_item_id
                WHERE ab.champion_id = ?
                  AND ab.role        = ?
                  AND ab.patch      LIKE ?
                  AND ab.enemy_item_id IN ({placeholders})
                ORDER BY ab.priority DESC, ab.win_rate_delta DESC
                LIMIT 3
            """, (champion_id, role.upper(), patch.split(".")[0] + "%", *enemy_item_ids)).fetchall()

            for row in db_recs:
                recommendations.insert(0, {
                    "rule":          "db_adaptive",
                    "priority":      row["priority"] + 10,
                    "tip":           f"Recommandation BDD: achète {row['name']}",
                    "trigger_items": [],
                    "counter_items": [{"id": row["counter_item_id"], "name": row["name"]}],
                    "win_rate_delta": row["win_rate_delta"],
                })

    return recommendations


# -------------------------------------------------------------------------
# Interface principale — tout en un
# -------------------------------------------------------------------------

def get_full_advice(my_champion_id: int, role: str,
                    enemy_champion_ids: list[int],
                    enemy_item_ids: list[int] = None,
                    patch: str = None) -> dict:
    """
    Point d'entrée principal du Pick Advisor.
    Retourne toutes les recommandations pour la partie en cours.
    """
    patch = patch or config.CURRENT_PATCH
    enemy_item_ids = enemy_item_ids or []

    # 1. Champion conseillé (si mon champion n'est pas encore fixé)
    counter_suggestions = []
    if enemy_champion_ids:
        primary_enemy = enemy_champion_ids[0]  # ennemi direct (même rôle)
        counter_suggestions = get_counters(primary_enemy, role, patch)

    # 2. Meilleur pick vs toute l'équipe
    best_vs_team = get_best_pick_vs_team(enemy_champion_ids, role, patch)

    # 3. Build pour mon champion actuel
    my_build = get_recommended_build(my_champion_id, role, patch)

    # 4. Adaptations selon items ennemis
    adaptations = get_adaptive_recommendations(
        my_champion_id, role, enemy_item_ids, patch
    )

    return {
        "my_champion_id":     my_champion_id,
        "my_champion_name":   get_champion_name(my_champion_id),
        "role":               role,
        "patch":              patch,
        "counters_vs_primary": counter_suggestions,
        "best_vs_team":       best_vs_team,
        "recommended_build":  my_build,
        "adaptive_items":     adaptations,
        "has_data":           my_build is not None,
    }


def format_advice(advice: dict) -> str:
    """Formate les recommandations en texte lisible."""
    lines = []
    lines.append(f"=== Conseils pour {advice['my_champion_name']} ({advice['role']}) ===")

    # Build
    build = advice.get("recommended_build")
    if build:
        wr = build.get("win_rate", 0) * 100
        games = build.get("games_played", 0)
        lines.append(f"\n[Build] Win rate: {wr:.1f}% sur {games} parties")
        core = [item["name"] for item in build.get("core_items_named", [])]
        if core:
            lines.append(f"  Items core : {' → '.join(core)}")
    else:
        lines.append("\n[Build] Pas encore assez de données (lance la collecte)")

    # Counters
    counters = advice.get("counters_vs_primary", [])
    if counters:
        lines.append("\n[Counters vs ennemi direct]")
        for c in counters:
            adv = c.get("advantage", 0)
            sign = "+" if adv >= 0 else ""
            lines.append(f"  {c['champion_name']:<15} {sign}{adv:.1f}% ({c['games_played']} parties)")

    # Best vs team
    best = advice.get("best_vs_team", [])
    if best:
        lines.append("\n[Meilleur pick vs leur équipe]")
        for b in best:
            wr = b.get("avg_win_rate", 0) * 100
            lines.append(f"  {b['champion_name']:<15} {wr:.1f}% win rate moyen")

    # Adaptations
    adaptations = advice.get("adaptive_items", [])
    if adaptations:
        lines.append("\n[Items adaptatifs (selon achats ennemis)]")
        for a in adaptations:
            lines.append(f"  ⚠ {a['tip']}")
            items = [i["name"] for i in a.get("counter_items", [])[:3]]
            if items:
                lines.append(f"    → {', '.join(items)}")

    return "\n".join(lines)


# -------------------------------------------------------------------------
# TEST
# -------------------------------------------------------------------------

if __name__ == "__main__":
    from data.database import init_db
    init_db()

    print("=== Test Pick Advisor ===\n")

    # Simule: je joue Ahri MID contre Zed
    all_champs = get_all_champions()
    ahri   = next((c for c in all_champs if c["name"] == "Ahri"), None)
    zed    = next((c for c in all_champs if c["name"] == "Zed"), None)
    darius = next((c for c in all_champs if c["name"] == "Darius"), None)

    if not ahri:
        print("[!] Champions pas en BDD. Lance d'abord: python main.py")
        exit()

    print(f"Scénario: Ahri MID vs Zed + Darius (items: Infinity Edge, Rabadon)")
    print()

    enemy_ids  = [zed["id"] if zed else 238, darius["id"] if darius else 122]
    enemy_items = [3031, 3089]  # Infinity Edge + Rabadon

    advice = get_full_advice(
        my_champion_id=ahri["id"],
        role="MID",
        enemy_champion_ids=enemy_ids,
        enemy_item_ids=enemy_items,
    )

    print(format_advice(advice))
