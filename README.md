# hounds-bot

Solana memecoin signal bot — buys on-chain signals filtered from GMGN market data, executes swaps via GmgnTerminal, manages exits with partial TP + cutoff logic.

## Architecture

```
Telegram (signal source)
    └── cooking_paper.py  (main loop)
            ├── GMGN API  (token metadata + smart money)
            ├── GmgnTerminal  (swap execution)
            └── hounds_outcomes.csv  (outcome tracking)

analyze_hounds.py  (local analysis — EV = mean(min(be_max_x, 2.0)))
refresh.ps1        (pull CSV from VM + run analysis in one command)
deploy.ps1         (syntax check → upload → verify → restart)
```

## Stack

- Python 3.11
- Telethon (Telegram client)
- GMGN API (market trending, token security, smart money)
- GCP VM (always-on, systemd service)
- PowerShell scripts for local dev workflow

## Exit logic

Each position has a strategy tag. Exits:
- **TP1** — partial sell (configurable bps) when target multiplier hit
- **TP2** — remainder at second target or cutoff (whichever comes first)
- **Cutoff** — full exit if TP1 never triggered within time window

## Analysis

```
EV = mean(min(be_max_x, 2.0))   # total return clipped at 2x, break-even = 1.0
```

`be_max_x` = peak MC during hold / entry MC — tracked per trade in real time.

## Deploy workflow

```powershell
.\deploy.ps1   # syntax check → scp upload → byte verify → systemctl restart
.\refresh.ps1  # pull CSV from VM → run analyze_hounds.py
```

---

## Support / Tips

If this project helped you, tips are appreciated:

**Solana:** `Fm4LJQ4cbi4nFzwEN7S793Zsv9jBv289eUt7ERPaUFpu`
