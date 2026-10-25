# POLY5 — Polymarket BTC 5-minute research & trading station

A Windows desktop system for the Polymarket **"BTC Up or Down – 5 minutes"** markets, built iteratively with AI coding agents (Codex and Claude). It has two independently built applications:

| App | Version | What it does |
|---|---|---|
| **Trading Station** (`poly5/`, `main.py`) | 3.3.0-dev.14 | Live order book & Chainlink reference feed, append-only fact ledger, paper trading, a web-style **quick-trade page** (1-Tap buy / Cash out), and a gated live-execution path |
| **Model Trainer** (`trainstation/`, `trainer_main.py`) | 1.9.3 | Continuous training of probability models (direction, last-minute, market-aware, dynamic take-profit), historical fast-forward, frozen forward validation, optional AI supervision with a control group |

> **This is a research project, not financial advice and not a money-making product.** No strategy in this repository has been statistically proven profitable. The documented live test lost money. Read `DEVELOPMENT_REPORT_3.3.md` before using any live feature. Use only where you are legally allowed to, and in line with the platform's terms.

## Repository layout

```
poly5/                 Trading station (PySide6 desktop + asyncio worker)
  quick_trade.py       Web-style quick-trade page (default UI mode)
  live_service.py      Gated live execution, reconciliation from confirmed fills, settlement
  live_exchange.py     Official polymarket-client SDK adapter (sign / post once / lookup / cancel)
  storage.py           Append-only JSONL fact ledger + rebuildable SQLite index
  auto_paper.py        Paper strategies (incl. last-minute model vote)
trainstation/          Model trainer (evolution, judge, replay, AI supervisor, order-book recorder)
tests/                 Offline unit tests for the station (no network, no orders)
research/              Research notes, public market snapshots
  experiments/         exp1–exp21 scripts and outputs (back-tests, rule grids, gate audits)
  station-diagnostics/ Order-book replay diagnostics
scripts/               Build (PyInstaller + NSIS), release staging, diagnostics
collaboration/         Shared change log and known-issues list between agents
docs/HANDOFF_README.md Original agent hand-off brief (historical)
```

Key documents: `AGENTS.md` (hard rules), `DEVELOPMENT_REPORT_3.3.md` (station history dev.1–dev.14), `TRAINER_CHANGES_1.0.1.md` (trainer history 1.0.1–1.9.3), `COLLABORATION.md`.

## Running the tests

Python ≥ 3.11 on Windows, dependencies in `requirements.txt` (official `polymarket-client` SDK, PySide6, scikit-learn, polars, …).

```powershell
$env:PYTHONPATH="$PWD;$PWD\tests"
python -m unittest discover -s tests -p "test_*.py"
python -m unittest trainstation.test_trainer trainstation.test_dynamic trainstation.test_revision
```

Tests use injected clients and fixtures only; none of them can reach a real order endpoint.

## Building

`scripts/build.ps1` (station) and `scripts/build_trainer.ps1` (trainer) produce PyInstaller one-folder builds; `scripts/build_installer.py` and `scripts/trainer_installer.nsi` wrap them with NSIS. Pre-built installers are attached to the GitHub Release.

The default data locations are `D:\POLYMARKET\data` (station) and `D:\POLYMARKET\trainer` (trainer); adjust in the code/config if needed.

## Safety model (summary)

- Live trading is **off by default**. The first enable requires an exact cash preview, a self-attestation of location/age eligibility and a typed confirmation phrase. The quick-trade mode can re-enable on launch afterwards, but it only ever sends orders the user clicks.
- The official geoblock must be fresh and match the region in the local setting `allowed_country`. There is no default in code. No VPN or proxy circumvention.
- Keys are entered locally and sealed with Windows DPAPI. They never go to logs, chat or IPC.
- An order whose result is unknown is locked and reconciled, never resent. Positions are booked only from on-chain-confirmed fills. A 50% drawdown stop latches.
- Automatic redemption is not implemented. Winning positions must be redeemed in the official account.

## Privacy

This public copy was redacted: no wallet addresses, account names, balances, order or transaction identifiers, location statements or local user paths. Fixture values in tests are synthetic.
