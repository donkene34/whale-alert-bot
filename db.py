"""Accès SQLite (stdlib). Mode WAL pour que le bot et le worker partagent le fichier.

Convention : toutes les adresses sont stockées en minuscules.
"""
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_rules (
    id              INTEGER PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    token_address   VARCHAR(42) NOT NULL,
    token_symbol    VARCHAR(20),
    threshold       NUMERIC NOT NULL,
    watched_address VARCHAR(42),
    active          BOOLEAN DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS known_addresses (
    address  VARCHAR(42) PRIMARY KEY,
    label    VARCHAR(100),
    category VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS sent_alerts (
    tx_hash   VARCHAR(66),
    log_index INT,
    chat_id   INTEGER,
    sent_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tx_hash, log_index, chat_id)
);

-- Ajout au schéma initial : dernier block scanné par token (reprise après redémarrage)
CREATE TABLE IF NOT EXISTS scan_state (
    token_address VARCHAR(42) PRIMARY KEY,
    last_block    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rules_chat ON watch_rules(chat_id, active);
CREATE INDEX IF NOT EXISTS idx_alerts_chat_time ON sent_alerts(chat_id, sent_at);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _norm(address: Optional[str]) -> Optional[str]:
    return address.lower() if address else None


def _rule_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["threshold"] = Decimal(str(d["threshold"]))
    d["active"] = bool(d["active"])
    return d


# ---------- watch_rules ----------
def add_rule(chat_id: int, token_address: str, token_symbol: str,
             threshold: Decimal, watched_address: Optional[str] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO watch_rules (chat_id, token_address, token_symbol, threshold, watched_address) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, _norm(token_address), token_symbol, str(threshold), _norm(watched_address)),
        )
        return cur.lastrowid


def list_rules(chat_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watch_rules WHERE chat_id = ? AND active = 1 ORDER BY id", (chat_id,)
        ).fetchall()
    return [_rule_to_dict(r) for r in rows]


def get_active_rules() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watch_rules WHERE active = 1 ORDER BY id").fetchall()
    return [_rule_to_dict(r) for r in rows]


def deactivate_rule(rule_id: int, chat_id: int) -> bool:
    """Désactive une règle, uniquement si elle appartient à ce chat."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE watch_rules SET active = 0 WHERE id = ? AND chat_id = ? AND active = 1",
            (rule_id, chat_id),
        )
        return cur.rowcount > 0


# ---------- known_addresses ----------
def upsert_known_address(address: str, label: str, category: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO known_addresses (address, label, category) VALUES (?, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET label = excluded.label, category = excluded.category",
            (_norm(address), label, category),
        )


def get_label(address: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT label FROM known_addresses WHERE address = ?", (_norm(address),)
        ).fetchone()
    return row["label"] if row else None


# ---------- sent_alerts ----------
def is_alert_sent(tx_hash: str, log_index: int, chat_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_alerts WHERE tx_hash = ? AND log_index = ? AND chat_id = ?",
            (tx_hash, log_index, chat_id),
        ).fetchone()
    return row is not None


def record_alerts(rows: list[tuple[str, int, int]]) -> None:
    """rows = [(tx_hash, log_index, chat_id), ...]. Les doublons sont ignorés (UNIQUE)."""
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO sent_alerts (tx_hash, log_index, chat_id) VALUES (?, ?, ?)", rows
        )


def count_recent_alerts(chat_id: int, seconds: int = 60) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sent_alerts "
            "WHERE chat_id = ? AND sent_at >= datetime('now', ?)",
            (chat_id, f"-{int(seconds)} seconds"),
        ).fetchone()
    return row["n"]


# ---------- scan_state ----------
def get_last_block(token_address: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_block FROM scan_state WHERE token_address = ?", (_norm(token_address),)
        ).fetchone()
    return row["last_block"] if row else None


def set_last_block(token_address: str, block: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scan_state (token_address, last_block) VALUES (?, ?) "
            "ON CONFLICT(token_address) DO UPDATE SET last_block = excluded.last_block",
            (_norm(token_address), block),
        )


if __name__ == "__main__":
    init_db()
    print(f"DB initialisée : {config.DB_PATH}")
