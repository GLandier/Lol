"""
Base de données locale SQLite.
Stocke : champions, items, matchups, builds recommandés.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), config.DB_PATH)


def init_db():
    """Crée les tables si elles n'existent pas."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            -- Champions
            CREATE TABLE IF NOT EXISTS champions (
                id          INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                key         TEXT NOT NULL,
                patch       TEXT
            );

            -- Items
            CREATE TABLE IF NOT EXISTS items (
                id          INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT,
                patch       TEXT
            );

            -- Stats de matchup (champion vs champion)
            -- Source: Lolalytics
            CREATE TABLE IF NOT EXISTS matchups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                champion_id     INTEGER NOT NULL,   -- le champion qu'on joue
                enemy_id        INTEGER NOT NULL,   -- l'ennemi d'en face
                role            TEXT NOT NULL,      -- TOP, JUNGLE, MID, ADC, SUPPORT
                patch           TEXT NOT NULL,
                games_played    INTEGER DEFAULT 0,
                win_rate        REAL DEFAULT 0.0,   -- 0.0 à 1.0
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(champion_id, enemy_id, role, patch)
            );

            -- Builds recommandés par champion/rôle/matchup
            CREATE TABLE IF NOT EXISTS builds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                champion_id     INTEGER NOT NULL,
                role            TEXT NOT NULL,
                patch           TEXT NOT NULL,
                -- Runes
                primary_style   INTEGER,
                sub_style       INTEGER,
                perk_ids        TEXT,               -- JSON list
                -- Items (ordre d'achat recommandé)
                starter_items   TEXT,               -- JSON list
                core_items      TEXT,               -- JSON list
                boots           INTEGER,
                situational     TEXT,               -- JSON list
                -- Stats
                games_played    INTEGER DEFAULT 0,
                win_rate        REAL DEFAULT 0.0,
                pick_rate       REAL DEFAULT 0.0,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(champion_id, role, patch)
            );

            -- Builds adaptatifs (en fonction des items ennemis)
            CREATE TABLE IF NOT EXISTS adaptive_builds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                champion_id     INTEGER NOT NULL,
                role            TEXT NOT NULL,
                patch           TEXT NOT NULL,
                enemy_item_id   INTEGER NOT NULL,   -- item acheté par l'ennemi
                counter_item_id INTEGER NOT NULL,   -- item conseillé en réponse
                priority        INTEGER DEFAULT 0,  -- plus haut = plus important
                win_rate_delta  REAL DEFAULT 0.0,   -- gain de win rate avec cet item
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            -- Historique des parties analysées (pour éviter les doublons)
            CREATE TABLE IF NOT EXISTS analyzed_matches (
                match_id    TEXT PRIMARY KEY,
                patch       TEXT,
                analyzed_at TEXT DEFAULT (datetime('now'))
            );

            -- Données brutes par participant (permet de ré-agréger sans re-télécharger)
            CREATE TABLE IF NOT EXISTS raw_participants (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id        TEXT NOT NULL,
                champion_id     INTEGER NOT NULL,
                role            TEXT NOT NULL,
                win             INTEGER NOT NULL,  -- 0 ou 1
                items           TEXT NOT NULL,     -- JSON list
                primary_style   INTEGER,
                sub_style       INTEGER,
                perk_ids        TEXT,              -- JSON list
                opponent_id     INTEGER,           -- champion adverse même rôle
                patch           TEXT NOT NULL,
                FOREIGN KEY (match_id) REFERENCES analyzed_matches(match_id)
            );
        """)
    print(f"[DB] Base de données initialisée: {DB_PATH}")


@contextmanager
def get_conn():
    """Context manager pour les connexions SQLite."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # lectures concurrentes pendant écriture
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -------------------------------------------------------------------------
# CHAMPIONS
# -------------------------------------------------------------------------

def upsert_champions(champions_data: dict, patch: str):
    """Insère ou met à jour les champions depuis Data Dragon."""
    with get_conn() as conn:
        for champ in champions_data.values():
            conn.execute("""
                INSERT INTO champions (id, name, key, patch)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, patch=excluded.patch
            """, (int(champ["key"]), champ["name"], champ["id"], patch))
    print(f"[DB] {len(champions_data)} champions mis à jour (patch {patch})")


def get_champion_name(champion_id: int) -> str:
    """Retourne le nom d'un champion par son ID."""
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM champions WHERE id = ?", (champion_id,)).fetchone()
        return row["name"] if row else f"Champion#{champion_id}"


def get_all_champions() -> list[dict]:
    """Retourne tous les champions."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM champions ORDER BY name").fetchall()
        return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# MATCHUPS
# -------------------------------------------------------------------------

def upsert_matchup(champion_id: int, enemy_id: int, role: str,
                   patch: str, games: int, win_rate: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO matchups (champion_id, enemy_id, role, patch, games_played, win_rate)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(champion_id, enemy_id, role, patch)
            DO UPDATE SET games_played=excluded.games_played,
                          win_rate=excluded.win_rate,
                          updated_at=datetime('now')
        """, (champion_id, enemy_id, role, patch, games, win_rate))


def get_best_counters(enemy_id: int, role: str, patch: str, limit: int = 5) -> list[dict]:
    """Retourne les meilleurs counters contre un champion."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.champion_id, c.name, m.win_rate, m.games_played
            FROM matchups m
            JOIN champions c ON c.id = m.champion_id
            WHERE m.enemy_id = ? AND m.role = ? AND m.patch = ?
              AND m.games_played >= 100
            ORDER BY m.win_rate DESC
            LIMIT ?
        """, (enemy_id, role, patch, limit)).fetchall()
        return [dict(r) for r in rows]


def get_worst_matchups(champion_id: int, role: str, patch: str, limit: int = 5) -> list[dict]:
    """Retourne les pires matchups pour un champion (counters contre lui)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.enemy_id, c.name, m.win_rate, m.games_played
            FROM matchups m
            JOIN champions c ON c.id = m.enemy_id
            WHERE m.champion_id = ? AND m.role = ? AND m.patch = ?
              AND m.games_played >= 100
            ORDER BY m.win_rate ASC
            LIMIT ?
        """, (champion_id, role, patch, limit)).fetchall()
        return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# BUILDS
# -------------------------------------------------------------------------

def upsert_build(champion_id: int, role: str, patch: str, build_data: dict):
    """Insère ou met à jour un build."""
    import json
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO builds (
                champion_id, role, patch,
                primary_style, sub_style, perk_ids,
                starter_items, core_items, boots, situational,
                games_played, win_rate, pick_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(champion_id, role, patch)
            DO UPDATE SET
                primary_style=excluded.primary_style,
                sub_style=excluded.sub_style,
                perk_ids=excluded.perk_ids,
                starter_items=excluded.starter_items,
                core_items=excluded.core_items,
                boots=excluded.boots,
                situational=excluded.situational,
                games_played=excluded.games_played,
                win_rate=excluded.win_rate,
                pick_rate=excluded.pick_rate,
                updated_at=datetime('now')
        """, (
            champion_id, role, patch,
            build_data.get("primary_style"),
            build_data.get("sub_style"),
            json.dumps(build_data.get("perk_ids", [])),
            json.dumps(build_data.get("starter_items", [])),
            json.dumps(build_data.get("core_items", [])),
            build_data.get("boots"),
            json.dumps(build_data.get("situational", [])),
            build_data.get("games_played", 0),
            build_data.get("win_rate", 0.0),
            build_data.get("pick_rate", 0.0),
        ))


def get_build(champion_id: int, role: str, patch: str) -> Optional[dict]:
    """Récupère le build recommandé pour un champion/rôle."""
    import json
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM builds
            WHERE champion_id = ? AND role = ? AND patch = ?
        """, (champion_id, role, patch)).fetchone()

        if not row:
            return None

        build = dict(row)
        # Désérialise les listes JSON
        for field in ["perk_ids", "starter_items", "core_items", "situational"]:
            if build.get(field):
                build[field] = json.loads(build[field])
        return build


def get_adaptive_items(champion_id: int, role: str, patch: str,
                       enemy_item_ids: list[int]) -> list[dict]:
    """
    Retourne les items conseillés en réponse aux items achetés par les ennemis.
    """
    if not enemy_item_ids:
        return []

    placeholders = ",".join("?" * len(enemy_item_ids))
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT ab.counter_item_id, i.name, ab.win_rate_delta,
                   ab.priority, ab.enemy_item_id
            FROM adaptive_builds ab
            JOIN items i ON i.id = ab.counter_item_id
            WHERE ab.champion_id = ? AND ab.role = ? AND ab.patch = ?
              AND ab.enemy_item_id IN ({placeholders})
            ORDER BY ab.priority DESC, ab.win_rate_delta DESC
            LIMIT 5
        """, (champion_id, role, patch, *enemy_item_ids)).fetchall()
        return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# TEST RAPIDE
# -------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    print("[DB] Tables créées avec succès.")
    print(f"[DB] Fichier: {DB_PATH}")
