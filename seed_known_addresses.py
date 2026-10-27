"""Peuple la table known_addresses avec une liste manuelle d'adresses publiques connues.

Usage : python seed_known_addresses.py

ATTENTION : cette liste est un POINT DE DÉPART, pas une liste exhaustive. Les exchanges
changent régulièrement leurs adresses, et les labels viennent de sources tierces.
Les adresses Ethereum mainnet n'existent pas sur Arc Testnet : elles servent quand le bot
est pointé vers le mainnet. Pour enrichir : ajoute des lignes dans KNOWN_ADDRESSES.

Chaque entrée : (adresse, label, catégorie, source).
Catégories : "exchange", "dex", "bridge", "other".
"""
from web3 import Web3

import db

ETHERSCAN = "Etherscan name tag : https://etherscan.io/address/{}"
UNISWAP_V3 = "Uniswap docs : https://developers.uniswap.org/docs/protocols/v3/deployments/v3-ethereum-deployments"
ARC_DOCS = "Arc docs : https://docs.arc.io/arc/references/contract-addresses"

KNOWN_ADDRESSES = [
    # ---- Exchanges (Ethereum mainnet) ----
    ("0x28C6c06298d514Db089934071355E5743bf21d60", "Binance 14", "exchange",
     ETHERSCAN.format("0x28C6c06298d514Db089934071355E5743bf21d60")),
    ("0xF977814e90dA44bFA03b6295A0616a897441aceC", "Binance Hot Wallet 20", "exchange",
     ETHERSCAN.format("0xF977814e90dA44bFA03b6295A0616a897441aceC")),
    ("0x71660c4005BA85c37ccec55d0C4493E66Fe775d3", "Coinbase 1", "exchange",
     ETHERSCAN.format("0x71660c4005BA85c37ccec55d0C4493E66Fe775d3")),
    ("0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2", "Kraken 1", "exchange",
     ETHERSCAN.format("0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2")),
    # ---- DEX (Ethereum mainnet) ----
    ("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45", "Uniswap V3 SwapRouter02", "dex", UNISWAP_V3),
    ("0x66a9893cc07d91d95644aedd05d03f95e1dba8af", "Uniswap Universal Router", "dex", UNISWAP_V3),
    ("0xE592427A0AEce92De3Edee1F18E0157C05861564", "Uniswap V3 SwapRouter", "dex", UNISWAP_V3),
    ("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D", "Uniswap V2 Router 2", "dex",
     ETHERSCAN.format("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")),
    # ---- Bridges (Ethereum mainnet) ----
    ("0x99C9fc46f92E8a1c0deC1b1747d010903E884bE1", "Optimism Gateway", "bridge",
     ETHERSCAN.format("0x99C9fc46f92E8a1c0deC1b1747d010903E884bE1")),
    ("0x72Ce9c846789fdB6fC1f34aC4AD25Dd9ef7031ef", "Arbitrum L1 Gateway Router", "bridge",
     ETHERSCAN.format("0x72Ce9c846789fdB6fC1f34aC4AD25Dd9ef7031ef")),
    # ---- Arc Testnet ----
    ("0x9fdF14c5B14173D74C08Af27AebFf39240dC105A", "USYC Teller (Arc Testnet)", "other", ARC_DOCS),
]


def main() -> None:
    db.init_db()
    for address, label, category, _source in KNOWN_ADDRESSES:
        if not Web3.is_address(address):  # détecte aussi un checksum erroné (faute de frappe)
            raise SystemExit(f"Adresse invalide dans la liste : {address} ({label})")
        db.upsert_known_address(address, label, category)
    print(f"{len(KNOWN_ADDRESSES)} adresses insérées/mises à jour dans {db.config.DB_PATH}")


if __name__ == "__main__":
    main()
