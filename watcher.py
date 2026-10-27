"""Worker de surveillance : scanne les events Transfer et envoie les alertes Telegram.

Se lance dans un processus séparé du bot : `python watcher.py`
"""
import asyncio
import html
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from web3 import Web3

import config
import db

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
# httpx logge l'URL complète des requêtes Telegram, qui contient le token du bot
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("watcher")

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DECIMALS_ABI = [{"name": "decimals", "type": "function", "stateMutability": "view",
                 "inputs": [], "outputs": [{"name": "", "type": "uint8"}]}]
MAX_BACKOFF = 60


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


def describe(address: str) -> str:
    """Label du carnet d'adresses connues si l'adresse y figure, sinon adresse tronquée."""
    return db.get_label(address) or short(address)


def fmt_amount(amount: Decimal) -> str:
    text = f"{amount:,.6f}".rstrip("0").rstrip(".")
    return text or "0"


def decode_transfer(log_entry) -> Optional[dict]:
    """Décode un log Transfer ERC-20. Renvoie None pour tout autre format (ex : ERC-721)."""
    topics = log_entry["topics"]
    if len(topics) != 3:  # un Transfer ERC-721 a 4 topics
        return None
    data = Web3.to_hex(log_entry["data"])
    if len(data) <= 2:
        return None
    return {
        "from": "0x" + Web3.to_hex(topics[1])[-40:].lower(),
        "to": "0x" + Web3.to_hex(topics[2])[-40:].lower(),
        "raw": int(data, 16),
        "tx_hash": Web3.to_hex(log_entry["transactionHash"]),
        "log_index": log_entry["logIndex"],
    }


def format_alert(symbol: str, amount: Decimal, frm: str, to: str, tx_hash: str) -> str:
    sym = html.escape(symbol)
    link = config.EXPLORER_TX_URL + tx_hash
    return (
        "🐋 <b>Transfert important détecté</b>\n"
        f"Token: {sym}\n"
        f"Montant: {fmt_amount(amount)} {sym}\n"
        f"De: <code>{html.escape(describe(frm))}</code>\n"
        f"Vers: <code>{html.escape(describe(to))}</code>\n"
        f'<a href="{html.escape(link)}">Voir sur l\'explorateur</a>'
    )


def format_summary(alerts: list[dict]) -> str:
    """Message résumé quand trop d'alertes arrivent d'un coup pour un même chat."""
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        by_symbol[a["symbol"]].append(a)
    lines = [f"🐋 <b>{len(alerts)} transferts importants détectés</b> (résumé anti-spam)"]
    for symbol, items in by_symbol.items():
        total = sum((i["amount"] for i in items), Decimal(0))
        sym = html.escape(symbol)
        lines.append(f"• {sym} : {len(items)} transferts, total {fmt_amount(total)} {sym}")
    lines.append("\nLes plus gros :")
    for a in sorted(alerts, key=lambda x: x["amount"], reverse=True)[:3]:
        sym = html.escape(a["symbol"])
        link = html.escape(config.EXPLORER_TX_URL + a["tx_hash"])
        lines.append(f'• {fmt_amount(a["amount"])} {sym} : <a href="{link}">voir</a>')
    return "\n".join(lines)


class Watcher:
    def __init__(self) -> None:
        self.w3 = Web3(Web3.HTTPProvider(config.get_rpc_url(), request_kwargs={"timeout": 20}))
        self.bot = Bot(config.get_telegram_token())
        self.decimals_cache: dict[str, int] = {}
        self.chunk = config.MAX_BLOCKS_PER_SCAN

    # ---- accès RPC (synchrones, appelés via asyncio.to_thread) ----
    def get_head(self) -> int:
        return self.w3.eth.block_number

    def get_decimals(self, token: str) -> int:
        if token not in self.decimals_cache:
            contract = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=DECIMALS_ABI)
            self.decimals_cache[token] = int(contract.functions.decimals().call())
        return self.decimals_cache[token]

    def fetch_logs(self, token: str, start: int, end: int) -> list:
        return self.w3.eth.get_logs({
            "fromBlock": start,
            "toBlock": end,
            "address": Web3.to_checksum_address(token),
            "topics": [TRANSFER_TOPIC],
        })

    # ---- envoi ----
    async def send(self, chat_id: int, text: str) -> bool:
        try:
            await self.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return True
        except Exception:
            # chat inaccessible (bot bloqué…) : on log sans faire planter le worker
            log.exception("Envoi Telegram impossible (chat %s)", chat_id)
            return False

    # ---- logique de scan ----
    async def handle_logs(self, token: str, rules: list[dict], logs: list) -> int:
        decimals = await asyncio.to_thread(self.get_decimals, token)
        divisor = Decimal(10) ** decimals
        # 1) collecte : une alerte par (chat, tx, log), même si plusieurs règles du chat matchent
        pending: dict[int, dict[tuple, dict]] = defaultdict(dict)
        for entry in logs:
            transfer = decode_transfer(entry)
            if transfer is None:
                continue
            amount = Decimal(transfer["raw"]) / divisor
            for rule in rules:
                watched = rule["watched_address"]
                if watched and watched not in (transfer["from"], transfer["to"]):
                    continue
                if amount < rule["threshold"]:
                    continue
                key = (transfer["tx_hash"], transfer["log_index"])
                pending[rule["chat_id"]].setdefault(key, {
                    "symbol": rule["token_symbol"] or "?", "amount": amount,
                    "from": transfer["from"], "to": transfer["to"],
                    "tx_hash": transfer["tx_hash"], "log_index": transfer["log_index"],
                })

        # 2) envoi : anti-doublon (sent_alerts) puis limite de débit par chat
        sent = 0
        for chat_id, alerts_by_key in pending.items():
            alerts = [a for a in alerts_by_key.values()
                      if not db.is_alert_sent(a["tx_hash"], a["log_index"], chat_id)]
            if not alerts:
                continue
            recent = db.count_recent_alerts(chat_id)
            if recent + len(alerts) <= config.ALERTS_PER_MINUTE_LIMIT:
                for a in alerts:
                    text = format_alert(a["symbol"], a["amount"], a["from"], a["to"], a["tx_hash"])
                    if await self.send(chat_id, text):
                        db.record_alerts([(a["tx_hash"], a["log_index"], chat_id)])
                        sent += 1
            else:
                log.info("Chat %s : %d alertes (+%d déjà envoyées < 1 min) → résumé groupé",
                         chat_id, len(alerts), recent)
                if await self.send(chat_id, format_summary(alerts)):
                    db.record_alerts([(a["tx_hash"], a["log_index"], chat_id) for a in alerts])
                    sent += len(alerts)
        return sent

    async def scan_token(self, token: str, rules: list[dict], head: int) -> None:
        last = db.get_last_block(token)
        if last is None:
            # première fois : on part du block actuel, sans rejouer l'historique
            db.set_last_block(token, head)
            log.info("Token %s : scan initialisé au block %d", short(token), head)
            return
        start = last + 1
        while start <= head:
            end = min(start + self.chunk - 1, head)
            try:
                logs = await asyncio.to_thread(self.fetch_logs, token, start, end)
            except Exception:
                self.chunk = max(1, self.chunk // 2)  # plage trop grande pour le RPC ?
                raise
            self.chunk = min(config.MAX_BLOCKS_PER_SCAN, self.chunk * 2)
            sent = await self.handle_logs(token, rules, logs)
            db.set_last_block(token, end)  # avancé seulement une fois la plage traitée
            if logs:
                log.info("Token %s blocks %d-%d : %d logs, %d alertes", short(token), start, end, len(logs), sent)
            start = end + 1

    async def scan_once(self) -> bool:
        """Un passage complet. Renvoie True si tout s'est bien passé."""
        rules = db.get_active_rules()
        if not rules:
            return True
        by_token: dict[str, list[dict]] = defaultdict(list)
        for rule in rules:
            by_token[rule["token_address"]].append(rule)
        head = await asyncio.to_thread(self.get_head)
        ok = True
        for token, token_rules in by_token.items():
            try:
                await self.scan_token(token, token_rules, head)
            except Exception:
                ok = False
                log.exception("Erreur de scan pour le token %s", short(token))
        return ok

    async def run(self) -> None:
        backoff = 1
        log.info("Worker démarré (poll %ss, DB : %s)", config.POLL_INTERVAL, config.DB_PATH)
        while True:
            try:
                ok = await self.scan_once()
            except Exception:
                ok = False
                log.exception("Erreur RPC pendant le scan")
            if ok:
                backoff = 1
                await asyncio.sleep(config.POLL_INTERVAL)
            else:
                log.warning("Nouvel essai dans %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)


def main() -> None:
    db.init_db()
    try:
        asyncio.run(Watcher().run())
    except KeyboardInterrupt:
        log.info("Arrêt du worker")


if __name__ == "__main__":
    main()
