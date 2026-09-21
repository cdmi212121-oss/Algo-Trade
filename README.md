# GTT Scaping — Nifty/Bank Nifty/Sensex Scalping Algo (Paper Trading)

A **paper trading platform** that automates an options-scalping strategy
(support/resistance swings + dual underlying/premium confirmation) against
live Angel One market data. **No real orders are ever placed** — there is no
order-execution code path anywhere in this codebase; Angel One is used for
market data only.

## 1. Project overview

One Python/Flask application that:
- polls live NIFTY/BANKNIFTY/SENSEX spot and option-chain data (Angel One SmartAPI),
- runs the scalping strategy against it and simulates paper trades (entries, SL/target, momentum scale-out),
- serves a dashboard (Dashboard, Trade Console, Positions, History, P&L, Violation Log, Profile) for watching and manually placing paper trades (Market or GTT).

Strategy rules are documented with source citations in `strategy/strategy_rules.md`.

## 2. Architecture

**This is a single service, not a frontend/backend/database split.** One
Flask process (`simulator/app.py`) does three things at once:

1. **Web/API layer** — serves the dashboard pages and `/api/*` JSON endpoints.
2. **Trading engine** — runs as a background thread inside that same process (`simulator/engine.py`), polling data and driving the strategy.
3. **Dashboard** — server-rendered HTML (Jinja2, `simulator/templates/`) + vanilla JS/CSS (`simulator/static/`), no separate frontend build or framework.

The dashboard reads live state (positions, prices, P&L) directly out of that
same process's memory, not from a database — there's no inter-process or
network hop between "frontend" and "backend" today. **This matters for
deployment**: if you ever split the engine and the web layer into two
separate processes/services, they'd stop sharing that in-memory state and
the dashboard would go blank. That's a real architecture change (needs a
shared database), not a file-reorganization — see Troubleshooting/Voroa
sections below if this becomes necessary later.

There is **no database**. State lives in memory for the running process,
plus two CSV logs:
- `logs/trades_<date>.csv` — closed paper trades
- `price_history/<SYMBOL>_<date>.csv` — polled tick history (also used to rebuild candles)

## 3. Folder structure

```
GTT Scaping/
├── .env                    # your real secrets — NEVER commit (git-ignored)
├── .env.example             # placeholder names only — safe to commit
├── .gitignore
├── README.md
├── requirements.txt          # install this at repo root
├── algo.py                    # standalone, dependency-free export of just the strategy logic (see its own docstring) — not used by the running app, kept for portability to other systems
├── strategy/
│   └── strategy_rules.md        # the extracted strategy rules this engine implements, with source citations
└── simulator/                   # the application (despite the name, this is the real app — data feed, strategy, risk, broker simulation, web/API, dashboard)
    ├── app.py                     # entry point: Flask app + starts the engine thread
    ├── engine.py                  # orchestrates the background trading loop + manual-trade API
    ├── config.py                  # capital, risk %, symbols, trading hours
    ├── strategy_engine.py          # entry-signal logic (the actual algo)
    ├── swing.py                    # candle-based swing detector
    ├── strike_selection.py         # which strike to watch/trade
    ├── paper_broker.py              # simulated positions, fills, SL/target/scale-out, P&L
    ├── risk_manager.py              # position sizing, daily loss cap, violation log
    ├── angel_data.py                 # live market data via Angel One SmartAPI
    ├── market_data.py                # shared data shapes
    ├── candles.py                     # OHLC candle building from tick series
    ├── templates/                      # dashboard pages (Jinja2)
    └── static/                         # dashboard JS/CSS (vanilla, no build step)
```

Runtime-only, git-ignored, created automatically: `logs/`, `price_history/`,
`tools/cache/` (Angel instrument-master cache).

Not part of the app, git-ignored — your own course material and design
assets, not code: the `Topic *.txt` transcripts, `Algo Strategy/` (Figma
screenshots), `tools/models/` and `tools/fonts/` (local model/font files
from earlier experiments).

## 4. Requirements

- Python 3.11+
- See `requirements.txt` (Flask, requests, smartapi-python, pyotp, logzero, websocket-client, python-dotenv)
- No Node.js/npm — there is no separate frontend build.

## 5. Local installation

```powershell
pip install -r requirements.txt
copy .env.example .env
# edit .env with your real Angel One credentials (see below)
python simulator/app.py
```

Open `http://127.0.0.1:5000`.

## 6. Environment variables

All required variables (see `.env.example`):

| Variable | What it is | Where to get it |
|---|---|---|
| `ANGEL_API_KEY` | Your Angel One SmartAPI app key | Register an app at https://smartapi.angelone.in |
| `ANGEL_CLIENT_CODE` | Your Angel One client/login ID | Your own account |
| `ANGEL_PIN` | Your 4-digit MPIN | Your own account (used for API login, not your full password) |
| `ANGEL_TOTP_SECRET` | Base32 TOTP secret | Shown once when you enable TOTP under Angel One profile settings — the underlying secret, not a live 6-digit code |
| `PORT` | *(optional, production only)* | Injected automatically by most hosts (Voroa included) — don't set it yourself unless running locally on a non-default port |

Never paste real values into chat, commit them, or put them in
`.env.example` — only variable *names* go there.

## 7-9. How to run each part

There's only one thing to run — the web layer, API, and trading engine are
one process:

```powershell
python simulator/app.py
```

This starts the Flask server *and* the background trading engine thread
together. There's no separate frontend/backend/worker command today (see
Architecture above for why, and what changes if that's ever split).

## 10. Database setup

Not applicable — no database is used.

## 11. Testing

No automated test suite exists yet. To verify changes manually without any
risk (this app never places real orders regardless):
```powershell
python -c "import py_compile,glob; [py_compile.compile(f, doraise=True) for f in glob.glob('simulator/*.py')]; print('OK')"
```
then run `python simulator/app.py` and exercise the dashboard. The Trade
Console's Market/GTT orders are always paper trades against `PaperBroker` —
there is no live/sandbox toggle needed because there is no live path.

## 12. Git / GitHub setup

Safe to push: everything under `simulator/`, `strategy/`, `algo.py`,
`requirements.txt`, `.gitignore`, `.env.example`, `README.md`.

**Never push:** `.env` (your real secrets), `logs/`, `price_history/`,
`tools/cache/`, `tools/models/`, `tools/fonts/`, the `Topic *.txt` course
transcripts, or `Algo Strategy/` (Figma screenshots) — all already covered
by `.gitignore`.

```powershell
cd "C:\Users\LENOVO\Downloads\GTT Scaping"
git init
git add .
git status                          # check what's staged before committing - confirm no .env, no Topic *.txt
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

Note: Git is installed on this machine (`C:\Program Files\Git\bin\git.exe`)
but wasn't on this terminal session's PATH — if `git` isn't recognized,
either restart your terminal or use the full path above.

## 13. Voroa deployment

**One service needed**, since the web layer and trading engine are one
process (see Architecture):

- **Type**: Web service (needs a public HTTP port — the dashboard is how you interact with it)
- **Build command**: `pip install -r requirements.txt`
- **Start command**: `python simulator/app.py`
- **Environment variables to add in Voroa**: `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET` (Voroa should inject `PORT` automatically — don't set it yourself unless Voroa requires it explicitly)
- **Health check path**: `/health` → `{"status": "ok", "engine_alive": true/false}`
- **No database service needed.**

I don't have specific built-in knowledge of Voroa's exact deployment
conventions (whether it wants a `Procfile`, a UI-configured start command,
autodetection, etc.) — check Voroa's own docs for how they want the start
command specified, and let me know if it needs a particular file format.

**Important caveat carried over from local testing**: this app polls Angel
One continuously and holds all state in memory. If Voroa's web services
sleep/restart on inactivity (common on some PaaS free tiers), the trading
engine's in-memory swing history resets every time that happens, and
auto-entries may never get a fair, uninterrupted shot at firing — this
exact problem caused a full trading day of zero trades during local testing
due to repeated process restarts. Confirm Voroa's service stays continuously
running (not just responds to requests) before trusting live results.

## 14. Security notes

- No hardcoded credentials anywhere in the codebase — verified by inspection; the only place real secrets exist is your own `.env` file, which is git-ignored.
- `ANGEL_API_KEY`/`ANGEL_CLIENT_CODE`/`ANGEL_PIN`/`ANGEL_TOTP_SECRET` are read via `python-dotenv` in `angel_data.py` and used only to authenticate the read-only market-data connection.
- There is no order-placement code path — this isn't a flag that could be flipped, it's simply not implemented, so "accidentally going live" isn't possible from this codebase as it stands.
- `/health` deliberately returns no position/P&L/account data, just a liveness boolean.
- The masked client code shown in the Profile page (`AAA***10` style) is the only credential-derived value ever sent to the browser.

## 15. Troubleshooting

- **Dashboard shows no data after deploying**: check `/health` — if `engine_alive: false`, the background thread hasn't completed a poll yet (normal for the first ~10s) or crashed (check server logs).
- **No live option-chain data**: the engine only fetches option chains during NSE market hours (9:15–15:30 IST); outside that window it holds the last-fetched snapshot instead of fetching fresh data (see `engine.py`'s market-hours gating).
- **Zero auto-trades despite the market being open**: check `/api/topbar`'s `safe_to_trade` and `trades_used`/`trades_max` — could be a risk-limit block (see the Violation Log page), or simply that the dual-confirmation entry condition hasn't fired yet (this is by design, not a bug — it's intentionally strict).
- **Settings (auto-trade symbols, capital, etc.) reset after a restart**: expected — config lives in memory only, not persisted to disk or a database. Re-apply via the Profile page (or `POST /api/profile`) after every restart.

## Data source notes

NSE India's free public JSON API (and the `nsepython` library, which uses
the same technique) is blocked by NSE's bot protection as of this writing —
confirmed with TLS-fingerprint spoofing (`curl_cffi`) too, not fixable
without a full headless browser. Angel One SmartAPI is used instead:
official, authenticated, reliable, free with an account.
