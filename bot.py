"""Bot Telegram : gestion des règles de surveillance (/watch, /list, /stop)."""
import asyncio
import html
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from web3 import Web3

import config
import db

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
# httpx logge l'URL complète des requêtes Telegram, qui contient le token du bot
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ERC20_ABI = [
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
]

HELP_TEXT = (
    "Commandes disponibles :\n\n"
    "/watch <code>&lt;token_address&gt; &lt;seuil&gt; [adresse_surveillée]</code>\n"
    "  Crée une règle. Le seuil est en unités du token (ex : 100000).\n"
    "  Sans adresse, tous les transferts du token sont surveillés.\n"
    "/list : tes règles actives\n"
    "/stop <code>&lt;rule_id&gt;</code> : désactive une règle\n"
    "/help : ce message\n\n"
    "Exemple : <code>/watch 0xA0b8...eB48 100000</code>"
)


def _w3() -> Web3:
    return Web3(Web3.HTTPProvider(config.get_rpc_url(), request_kwargs={"timeout": 15}))


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


def fetch_token_info(token_address: str) -> tuple[str, int]:
    """Vérifie que l'adresse est un contrat ERC-20 et renvoie (symbole, decimals).

    Fonction synchrone (web3 HTTP) : à appeler via asyncio.to_thread.
    Lève ValueError avec un message lisible si ce n'est pas un ERC-20 valide.
    """
    w3 = _w3()
    checksum = Web3.to_checksum_address(token_address)
    if len(w3.eth.get_code(checksum)) == 0:
        raise ValueError("Cette adresse n'est pas un contrat (aucun code on-chain).")
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    try:
        symbol = contract.functions.symbol().call()
        decimals = contract.functions.decimals().call()
    except Exception as exc:  # ABI decode error, revert, etc.
        raise ValueError("Ce contrat ne répond pas comme un ERC-20 (symbol()/decimals()).") from exc
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbole du token invalide.")
    return symbol.strip()[:20], int(decimals)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🐋 <b>Whale Alert Bot</b>\n\n"
        "Je surveille les transferts de tokens ERC-20 et je t'alerte quand un montant "
        "dépasse ton seuil.\n\n" + HELP_TEXT,
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) not in (2, 3):
        await update.message.reply_text(
            "Usage : /watch <token_address> <seuil> [adresse_surveillée]\n"
            "Exemple : /watch 0x... 100000"
        )
        return

    token_address, threshold_raw = args[0], args[1]
    watched: Optional[str] = args[2] if len(args) == 3 else None

    if not Web3.is_address(token_address):
        await update.message.reply_text("❌ Adresse du token invalide (format 0x + 40 caractères hex).")
        return
    if watched is not None and not Web3.is_address(watched):
        await update.message.reply_text("❌ Adresse surveillée invalide.")
        return
    try:
        threshold = Decimal(threshold_raw.replace(",", "."))
        if not threshold.is_finite() or threshold <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await update.message.reply_text("❌ Seuil invalide : donne un nombre strictement positif.")
        return

    try:
        symbol, _decimals = await asyncio.to_thread(fetch_token_info, token_address)
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    except Exception:
        log.exception("Erreur RPC pendant la vérification du token")
        await update.message.reply_text("⚠️ Erreur réseau/RPC pendant la vérification. Réessaie dans un instant.")
        return

    rule_id = db.add_rule(update.effective_chat.id, token_address, symbol, threshold, watched)
    scope = f"les transferts impliquant <code>{short(watched.lower())}</code>" if watched else "tous les transferts"
    await update.message.reply_text(
        f"✅ Règle <b>#{rule_id}</b> créée : {scope} de <b>{html.escape(symbol)}</b> "
        f"≥ {threshold:f} {html.escape(symbol)}.",
        parse_mode=ParseMode.HTML,
    )


async def list_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rules = db.list_rules(update.effective_chat.id)
    if not rules:
        await update.message.reply_text("Aucune règle active. Crée-en une avec /watch.")
        return
    lines = ["<b>Tes règles actives :</b>"]
    for r in rules:
        sym = html.escape(r["token_symbol"] or "?")
        target = f"adresse <code>{short(r['watched_address'])}</code>" if r["watched_address"] else "tous les transferts"
        lines.append(
            f"#{r['id']} · {sym} (<code>{short(r['token_address'])}</code>) · "
            f"seuil {r['threshold']:f} · {target}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage : /stop <rule_id>  (voir /list)")
        return
    rule_id = int(context.args[0])
    if db.deactivate_rule(rule_id, update.effective_chat.id):
        await update.message.reply_text(f"🛑 Règle #{rule_id} désactivée.")
    else:
        await update.message.reply_text(f"❌ Aucune règle active #{rule_id} dans ce chat.")


def build_app() -> Application:
    app = Application.builder().token(config.get_telegram_token()).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("list", list_rules))
    app.add_handler(CommandHandler("stop", stop))
    return app


def main() -> None:
    config.get_rpc_url()  # échoue tôt si RPC_URL manque
    db.init_db()
    log.info("Bot démarré (DB : %s)", config.DB_PATH)
    build_app().run_polling()


if __name__ == "__main__":
    main()
