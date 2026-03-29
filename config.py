import os
from dotenv import load_dotenv

load_dotenv()

# Riot API
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
REGION = os.getenv("REGION", "euw1")          # euw1, na1, kr, eun1...
PLATFORM = os.getenv("PLATFORM", "europe")    # europe, americas, asia

# Riot API base URLs
RIOT_BASE_URL = f"https://{REGION}.api.riotgames.com"
RIOT_PLATFORM_URL = f"https://{PLATFORM}.api.riotgames.com"

# Rate limits (clé dev : 20 req/s, 100 req/2min)
RATE_LIMIT_PER_SECOND = 20
RATE_LIMIT_PER_2MIN = 100

# Live game polling interval (secondes)
LIVE_POLL_INTERVAL = 3

# BDD locale
DB_PATH = "data/lol_tool.db"

# Lolalytics
LOLALYTICS_BASE_URL = "https://lolalytics.com"

# Patch actuel (à mettre à jour chaque patch)
CURRENT_PATCH = "16.6"
