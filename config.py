"""Configuration centralisée, lue depuis les variables d'environnement (.env supporté)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Variable d'environnement manquante : {name} (voir .env.example)"
        )
    return value


# Obligatoires (lues à la demande pour que db.py / seed fonctionnent sans elles)
def get_telegram_token() -> str:
    return _require("TELEGRAM_BOT_TOKEN")


def get_rpc_url() -> str:
    return _require("RPC_URL")


# Optionnelles
DB_PATH = os.getenv("DB_PATH", "whale_alert.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # secondes entre deux scans
# Préfixe du lien de transaction sur l'explorateur du réseau cible
EXPLORER_TX_URL = os.getenv("EXPLORER_TX_URL", "https://explorer.testnet.arc.io/tx/")

# Paramètres du watcher
MAX_BLOCKS_PER_SCAN = int(os.getenv("MAX_BLOCKS_PER_SCAN", "500"))
ALERTS_PER_MINUTE_LIMIT = 5
