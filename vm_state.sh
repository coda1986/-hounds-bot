#!/bin/bash
# vm_state.sh — snapshot do estado critico do VM
BOT_DIR=/home/abib1/signal_bot
OUT=$BOT_DIR/vm_state.json

live=$(grep '^HOUNDS_LIVE' $BOT_DIR/cooking_paper.py 2>/dev/null | head -1 | tr -d ' ')
buy=$(grep '^HOUNDS_BUY_SOL' $BOT_DIR/cooking_paper.py 2>/dev/null | head -1 | tr -d ' ')
svc=$(systemctl is-active cooking-paper 2>/dev/null)

outcomes_lines=$(wc -l < $BOT_DIR/hounds_outcomes.csv 2>/dev/null || echo 1)
launchpad_lines=$(wc -l < $BOT_DIR/hounds_launchpad_log.csv 2>/dev/null || echo 1)
outcomes_n=$((outcomes_lines - 1))
launchpad_n=$((launchpad_lines - 1))

outcomes_mtime=$(stat -c '%Y' $BOT_DIR/hounds_outcomes.csv 2>/dev/null || echo 0)
launchpad_mtime=$(stat -c '%Y' $BOT_DIR/hounds_launchpad_log.csv 2>/dev/null || echo 0)
code_mtime=$(stat -c '%Y' $BOT_DIR/cooking_paper.py 2>/dev/null || echo 0)

pending_count=$(python3 -c "import json; d=json.load(open('$BOT_DIR/hounds_pending.json')); print(len(d) if isinstance(d, (list,dict)) else 0)" 2>/dev/null || echo "ERR")

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

python3 - <<PYEOF
import json
data = {
    "snapshot_utc": "$ts",
    "service": "$svc",
    "HOUNDS_LIVE": "$live",
    "HOUNDS_BUY_SOL": "$buy",
    "outcomes_n": $outcomes_n,
    "outcomes_mtime": $outcomes_mtime,
    "launchpad_n": $launchpad_n,
    "launchpad_mtime": $launchpad_mtime,
    "code_mtime": $code_mtime,
    "pending_count": "$pending_count"
}
with open("$OUT", "w") as f:
    json.dump(data, f, indent=2)
print(json.dumps(data, indent=2))
PYEOF
