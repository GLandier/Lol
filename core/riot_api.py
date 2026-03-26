"""
Riot API client — gère toutes les requêtes vers l'API Riot Games.
Inclut : rate limiting, gestion d'erreurs, retry automatique.
"""

import asyncio
import time
import aiohttp
import requests
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


class RateLimiter:
    """Limite les requêtes pour respecter les quotas Riot API."""

    def __init__(self, per_second: int, per_2min: int):
        self.per_second = per_second
        self.per_2min = per_2min
        self._second_calls: list[float] = []
        self._2min_calls: list[float] = []

    def wait_if_needed(self):
        now = time.time()

        # Nettoie les anciens timestamps
        self._second_calls = [t for t in self._second_calls if now - t < 1.0]
        self._2min_calls = [t for t in self._2min_calls if now - t < 120.0]

        # Attend si limite par seconde atteinte
        if len(self._second_calls) >= self.per_second:
            sleep_time = 1.0 - (now - self._second_calls[0])
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Attend si limite par 2min atteinte
        if len(self._2min_calls) >= self.per_2min:
            sleep_time = 120.0 - (now - self._2min_calls[0])
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._second_calls.append(time.time())
        self._2min_calls.append(time.time())


class RiotAPIClient:
    """
    Client synchrone pour l'API Riot Games.
    Utilise requests pour les appels simples.
    """

    def __init__(self, api_key: str = None, region: str = None, platform: str = None):
        self.api_key = api_key or config.RIOT_API_KEY
        self.region = region or config.REGION
        self.platform = platform or config.PLATFORM
        self.base_url = f"https://{self.region}.api.riotgames.com"
        self.platform_url = f"https://{self.platform}.api.riotgames.com"
        self.headers = {"X-Riot-Token": self.api_key}
        self.rate_limiter = RateLimiter(
            config.RATE_LIMIT_PER_SECOND,
            config.RATE_LIMIT_PER_2MIN
        )

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        """Effectue un GET avec gestion d'erreurs et retry."""
        self.rate_limiter.wait_if_needed()
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited par Riot — attend le retry-after
                retry_after = int(response.headers.get("Retry-After", 5))
                print(f"[RiotAPI] Rate limited. Attente {retry_after}s...")
                time.sleep(retry_after)
                return self._get(url, params)
            elif response.status_code == 404:
                return None
            else:
                print(f"[RiotAPI] Erreur {response.status_code}: {url}")
                return None

        except requests.exceptions.Timeout:
            print(f"[RiotAPI] Timeout sur {url}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[RiotAPI] Erreur requête: {e}")
            return None

    # -------------------------------------------------------------------------
    # COMPTE & INVOCATEUR
    # -------------------------------------------------------------------------

    def get_account_by_riot_id(self, game_name: str, tag_line: str) -> Optional[dict]:
        """
        Récupère le PUUID via le Riot ID (ex: Pseudo#EUW).
        Retourne: {puuid, gameName, tagLine}
        """
        url = f"{self.platform_url}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return self._get(url)

    def get_summoner_by_puuid(self, puuid: str) -> Optional[dict]:
        """
        Récupère les infos invocateur via PUUID.
        Retourne: {id, accountId, puuid, profileIconId, summonerLevel}
        """
        url = f"{self.base_url}/lol/summoner/v4/summoners/by-puuid/{puuid}"
        return self._get(url)

    def get_summoner_by_name(self, summoner_name: str) -> Optional[dict]:
        """Récupère les infos invocateur via nom (legacy)."""
        url = f"{self.base_url}/lol/summoner/v4/summoners/by-name/{summoner_name}"
        return self._get(url)

    # -------------------------------------------------------------------------
    # PARTIE EN COURS (LIVE GAME)
    # -------------------------------------------------------------------------

    def get_live_game(self, puuid: str) -> Optional[dict]:
        """
        Récupère la partie en cours du joueur.
        Retourne None si pas en partie.

        Structure retournée:
        {
          gameId, gameType, gameStartTime, mapId, gameLength,
          platformId, gameMode, gameQueueConfigId,
          participants: [{puuid, summonerName, championId, teamId,
                          perks: {perkIds, perkStyle, perkSubStyle},
                          ...}],
          bannedChampions: [{championId, teamId, pickTurn}]
        }
        """
        url = f"{self.base_url}/lol/spectator/v5/active-games/by-summoner/{puuid}"
        return self._get(url)

    # -------------------------------------------------------------------------
    # HISTORIQUE DE PARTIES
    # -------------------------------------------------------------------------

    def get_match_ids(self, puuid: str, count: int = 20,
                      queue: int = None, start: int = 0) -> list[str]:
        """
        Récupère les IDs des dernières parties.
        queue: 420 = SoloQ, 440 = FlexQ, None = tout
        """
        params = {"start": start, "count": min(count, 100)}
        if queue:
            params["queue"] = queue
        url = f"{self.platform_url}/lol/match/v5/matches/by-puuid/{puuid}/ids"
        result = self._get(url, params)
        return result or []

    def get_match(self, match_id: str) -> Optional[dict]:
        """
        Récupère les détails complets d'une partie.
        Contient: metadata, info (participants avec tous les stats)
        """
        url = f"{self.platform_url}/lol/match/v5/matches/{match_id}"
        return self._get(url)

    def get_match_timeline(self, match_id: str) -> Optional[dict]:
        """Récupère la timeline d'une partie (events par frame)."""
        url = f"{self.platform_url}/lol/match/v5/matches/{match_id}/timeline"
        return self._get(url)

    # -------------------------------------------------------------------------
    # CLASSEMENT
    # -------------------------------------------------------------------------

    def get_ranked_stats(self, summoner_id: str) -> Optional[list]:
        """
        Récupère les stats ranked (SoloQ, Flex).
        Retourne: [{queueType, tier, rank, leaguePoints, wins, losses}]
        """
        url = f"{self.base_url}/lol/league/v4/entries/by-summoner/{summoner_id}"
        return self._get(url)

    # -------------------------------------------------------------------------
    # DATA DRAGON (assets statiques)
    # -------------------------------------------------------------------------

    def get_champion_data(self) -> Optional[dict]:
        """Récupère les données de tous les champions via Data Dragon."""
        url = f"https://ddragon.leagueoflegends.com/cdn/{config.CURRENT_PATCH}.1/data/fr_FR/champion.json"
        self.rate_limiter.wait_if_needed()
        try:
            r = requests.get(url, timeout=10)
            return r.json().get("data") if r.status_code == 200 else None
        except Exception as e:
            print(f"[DataDragon] Erreur: {e}")
            return None

    def get_item_data(self) -> Optional[dict]:
        """Récupère les données de tous les items via Data Dragon."""
        url = f"https://ddragon.leagueoflegends.com/cdn/{config.CURRENT_PATCH}.1/data/fr_FR/item.json"
        self.rate_limiter.wait_if_needed()
        try:
            r = requests.get(url, timeout=10)
            return r.json().get("data") if r.status_code == 200 else None
        except Exception as e:
            print(f"[DataDragon] Erreur: {e}")
            return None


# -------------------------------------------------------------------------
# HELPER — Extraire les infos utiles d'une live game
# -------------------------------------------------------------------------

def parse_live_game(live_data: dict) -> dict:
    """
    Simplifie les données brutes d'une live game.
    Retourne un dict propre avec nos équipes séparées.
    """
    if not live_data:
        return {}

    participants = live_data.get("participants", [])
    team1 = [p for p in participants if p.get("teamId") == 100]
    team2 = [p for p in participants if p.get("teamId") == 200]

    def format_player(p: dict) -> dict:
        return {
            "puuid": p.get("puuid"),
            "summoner_name": p.get("riotId", p.get("summonerName", "?")),
            "champion_id": p.get("championId"),
            "team_id": p.get("teamId"),
            "spell1": p.get("spell1Id"),
            "spell2": p.get("spell2Id"),
            "runes": {
                "primary_style": p.get("perks", {}).get("perkStyle"),
                "sub_style": p.get("perks", {}).get("perkSubStyle"),
                "perk_ids": p.get("perks", {}).get("perkIds", []),
            }
        }

    return {
        "game_id": live_data.get("gameId"),
        "game_mode": live_data.get("gameMode"),
        "game_start": live_data.get("gameStartTime"),
        "game_length": live_data.get("gameLength"),
        "map_id": live_data.get("mapId"),
        "queue_id": live_data.get("gameQueueConfigId"),
        "team_blue": [format_player(p) for p in team1],
        "team_red": [format_player(p) for p in team2],
        "banned_champions": live_data.get("bannedChampions", []),
    }


# -------------------------------------------------------------------------
# TEST RAPIDE
# -------------------------------------------------------------------------

if __name__ == "__main__":
    client = RiotAPIClient()

    print("=== Test connexion Riot API ===")
    print(f"Région: {client.region} | Platform: {client.platform}")
    print(f"Clé API configurée: {'Oui' if client.api_key and 'xxxx' not in client.api_key else 'NON — modifie le .env'}")

    # Test Data Dragon
    print("\n--- Chargement Data Dragon ---")
    champions = client.get_champion_data()
    if champions:
        print(f"Champions chargés: {len(champions)}")
        # Affiche 5 champions en exemple
        for name in list(champions.keys())[:5]:
            print(f"  - {champions[name]['name']} (id: {champions[name]['key']})")
    else:
        print("Erreur Data Dragon")

    # Test compte (nécessite une vraie clé API)
    # account = client.get_account_by_riot_id("TonPseudo", "EUW")
    # print(f"\nCompte: {account}")
