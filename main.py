"""
Point d'entrée principal de l'application.
"""

import sys
import os

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from core.riot_api import RiotAPIClient
from data.database import init_db, upsert_champions


def setup():
    """Initialisation au premier lancement."""
    print("=== LoL Tool — Initialisation ===\n")

    # Vérifie la clé API
    if not config.RIOT_API_KEY or "xxxx" in config.RIOT_API_KEY:
        print("[!] Clé API Riot non configurée !")
        print("    → Modifie le fichier .env et ajoute ta clé RIOT_API_KEY")
        return False

    # Initialise la BDD
    init_db()

    # Charge les données statiques (champions, items)
    client = RiotAPIClient()
    print("\n[+] Chargement des champions depuis Data Dragon...")
    champions = client.get_champion_data()
    if champions:
        upsert_champions(champions, config.CURRENT_PATCH)
    else:
        print("[!] Impossible de charger les champions")

    print("\n[+] Setup terminé !")
    return True


def main():
    if not setup():
        sys.exit(1)

    print("\n[+] Lancement de l'application...")
    # TODO: lancer l'UI PyQt6
    print("    (UI à venir — module core/riot_api.py prêt)")


if __name__ == "__main__":
    main()
