# 🐋 whale-alert-bot

Bot Telegram qui surveille les transferts de tokens ERC-20 et envoie une alerte en temps réel quand un transfert dépasse un seuil que tu as configuré.

Tu crées des règles depuis Telegram (`/watch`), un worker lit la blockchain en continu, et dès qu'un transfert correspond à une règle, tu reçois un message avec le montant, les adresses (avec le nom de l'exchange ou du DEX s'il est connu) et le lien vers l'explorateur.

Démo sur **Arc Testnet**, code pensé pour fonctionner sur n'importe quel réseau EVM (mainnet compris) en changeant `RPC_URL`.

## Schéma du flux

```
 Telegram                    SQLite (whale_alert.db)                 Blockchain
 ────────                    ───────────────────────                 ──────────
 /watch token seuil ──bot.py──▶ watch_rules
                                     │
                                     ▼
                             watcher.py (boucle, toutes les 10 s)
                               1. lit les règles actives
                               2. get_logs(Transfer) depuis scan_state ◀── RPC_URL
                               3. filtre adresse + seuil (selon decimals)
                               4. déduplique (sent_alerts) + limite 5/min/chat
                               5. ajoute les labels (known_addresses)
                                     │
                                     ▼
 🐋 Alerte  ◀────────────── Telegram Bot API
```

`bot.py` et `watcher.py` sont **deux processus séparés** qui partagent le même fichier SQLite (mode WAL).

## Structure

```
whale-alert-bot/
├── bot.py                     # commandes Telegram (/start /watch /list /stop /help)
├── watcher.py                 # worker de surveillance + envoi des alertes
├── db.py                      # accès SQLite (règles, adresses connues, alertes, état du scan)
├── seed_known_addresses.py    # remplit le carnet d'adresses connues
├── config.py                  # variables d'environnement
├── requirements.txt
├── .env.example
└── README.md
```

## 1. Créer le bot Telegram (BotFather)

1. Dans Telegram, ouvre [@BotFather](https://t.me/BotFather) et clique sur **Start**.
2. Envoie `/newbot`.
3. Choisis un nom affiché (ex : `Whale Alert Bot`), puis un username unique finissant par `bot` (ex : `donkene_whale_alert_bot`).
4. BotFather te donne un **token** du type `123456789:AA...`. C'est la valeur de `TELEGRAM_BOT_TOKEN`.

⚠️ Le token donne le contrôle total du bot : ne le mets jamais dans le code ni sur GitHub. S'il fuite, envoie `/revoke` à BotFather pour en générer un nouveau.

## 2. Installation

Prérequis : Python 3.11+.

```bash
git clone <ton-repo>
cd whale-alert-bot

python -m venv .venv
# Windows : .venv\Scripts\activate
# Linux/macOS : source .venv/bin/activate

pip install -r requirements.txt
```

## 3. Configuration

Copie `.env.example` en `.env` et remplis-le :

| Variable | Obligatoire | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | oui | Token donné par BotFather |
| `RPC_URL` | oui | Endpoint RPC HTTP du réseau. Arc Testnet : `https://rpc.testnet.arc.io` |
| `EXPLORER_TX_URL` | non | Préfixe du lien de transaction. Défaut : `https://explorer.testnet.arc.io/tx/` |
| `DB_PATH` | non | Fichier SQLite. Défaut : `whale_alert.db` |
| `POLL_INTERVAL` | non | Secondes entre deux scans. Défaut : `10` |
| `MAX_BLOCKS_PER_SCAN` | non | Taille max d'une plage de blocks par requête `get_logs`. Défaut : `500` (divisée par deux automatiquement si le RPC refuse) |

Pour le mainnet Ethereum, mets par exemple `EXPLORER_TX_URL=https://etherscan.io/tx/` et ton RPC.

## 4. Lancer

Dans **deux terminaux** (avec le venv activé) :

```bash
# Terminal 1 : le bot (commandes Telegram)
python bot.py

# Terminal 2 : le worker (surveillance + alertes)
python watcher.py
```

Optionnel, pour remplir le carnet d'adresses connues (peut être relancé à tout moment, même worker allumé) :

```bash
python seed_known_addresses.py
```

## 5. Utilisation

| Commande | Effet |
|---|---|
| `/start` | Message de bienvenue |
| `/watch <token> <seuil> [adresse]` | Crée une règle pour ce chat |
| `/list` | Liste tes règles actives |
| `/stop <rule_id>` | Désactive une règle |
| `/help` | Rappel des commandes |

Le seuil est en **unités du token** (pas en wei). Si tu donnes une adresse, seuls les transferts où elle est émettrice ou destinataire sont surveillés.

Exemples (Arc Testnet) :

```
/watch 0x3600000000000000000000000000000000000000 100000
/watch 0x3600000000000000000000000000000000000000 500 0xTonAdresse...
/list
/stop 1
```

`/watch` vérifie que l'adresse est bien un contrat ERC-20 (code présent, `symbol()` et `decimals()` répondent) avant d'accepter la règle.

Exemple d'alerte :

```
🐋 Transfert important détecté
Token: USDC
Montant: 250,000 USDC
De: Binance 14
Vers: 0x3333…3333
Voir sur l'explorateur
```

## Comportements à connaître

- **Premier scan** : pour un token nouvellement surveillé, le worker part du block actuel et ne rejoue pas l'historique.
- **Redémarrage** : le dernier block scanné est sauvegardé par token (`scan_state`). Après une panne, le worker rattrape les blocks manqués.
- **Anti-doublon** : une alerte (`tx_hash`, `log_index`, `chat_id`) n'est jamais envoyée deux fois, même après un redémarrage.
- **Anti-spam** : si plus de 5 alertes seraient envoyées au même chat dans la même minute, elles sont regroupées en un seul message résumé (nombre, total par token, les 3 plus gros transferts).
- **Erreurs RPC** : le worker réessaie avec un délai croissant (1 s, 2 s, 4 s… jusqu'à 60 s) sans planter.

## Carnet d'adresses connues

`seed_known_addresses.py` charge une petite liste manuelle d'adresses publiques (quelques exchanges, routers Uniswap, bridges) avec leur source documentée dans le fichier (tags Etherscan, docs Uniswap, docs Arc).

**Cette liste est un point de départ, elle n'est pas exhaustive** : les exchanges changent régulièrement d'adresses et les labels viennent de sources tierces. Ces adresses sont sur Ethereum mainnet, donc elles n'apparaissent pas dans les alertes sur Arc Testnet. Pour l'enrichir, ajoute des lignes dans `KNOWN_ADDRESSES` puis relance le script.

## Limites connues

- Seuls les transferts ERC-20 standards (event `Transfer` à 3 topics) sont détectés. Les tokens ERC-721 sont ignorés.
- Une alerte dont l'envoi Telegram échoue n'est pas rejouée (le scan avance quand même).
- Le polling `get_logs` est simple et marche avec n'importe quel RPC HTTP, mais n'est pas instantané (délai ≈ `POLL_INTERVAL`).
- Pas de gestion des réorganisations de chaîne (sans importance sur Arc, à prévoir sur un réseau sans finalité rapide).

## Sécurité

- `.env` et `*.db` sont dans `.gitignore` : ne les commit pas.
- Les règles sont isolées par chat : un chat ne peut lister ou désactiver que ses propres règles.
