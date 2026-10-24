"""
scars.py — Fingerprint de winners para matching preditivo.

Dois modos:
  - aibot (signal_scanner): be_max_x >= WIN_THRESHOLD -> scars.jsonl
  - kotte (kotte_monitor):  x_1h    >= WIN_THRESHOLD -> kotte_scars.jsonl
"""

import json, os, threading

SCARS_FILE       = os.path.join(os.path.dirname(__file__), 'scars.jsonl')
KOTTE_SCARS_FILE = os.path.join(os.path.dirname(__file__), 'kotte_scars.jsonl')
WIN_THRESHOLD    = 1.45
MIN_OVERLAP      = 6
KOTTE_MIN_OVERLAP = 5
_lock            = threading.Lock()

# Campos -> thresholds de bucket (aibot)
_BUCKETS = {
    'priority_score':    [62, 70],       # 0:<62  1:62-69  2:>=70
    'dev_score':         [0.50, 0.75],   # 0:<0.50  1:0.50-0.75  2:>=0.75
    'chg_5m':            [-0.05, 0.10],  # 0:<-5%  1:-5~+10%  2:>+10%
    'chg_1h':            [0.0,  0.20],   # 0:<0%  1:0-20%  2:>20%
    'buy_ratio':         [0.55, 0.70],   # 0:<0.55  1:0.55-0.70  2:>=0.70
    'smart_degen_count': [10,   50],     # 0:<10  1:10-49  2:>=50
    'token_age_min':     [1440, 10080],  # 0:<1dia  1:1-7dias  2:>=7dias
    'rug_ratio':         [0.20, 0.40],   # 0:<0.20  1:0.20-0.40  2:>=0.40
    'bundler_rate':      [0.05, 0.15],   # 0:<5%  1:5-15%  2:>=15%
    'entrapment_ratio':  [0.05, 0.20],   # 0:<5%  1:5-20%  2:>=20%
}

N_FIELDS = len(_BUCKETS)


def _bucket(field: str, value) -> int:
    thresholds = _BUCKETS[field]
    v = float(value or 0)
    for i, t in enumerate(thresholds):
        if v < t:
            return i
    return len(thresholds)


def fingerprint(row: dict) -> dict:
    return {f: _bucket(f, row.get(f, 0)) for f in _BUCKETS}


def is_winner(row: dict) -> bool:
    return float(row.get('be_max_x', 0) or 0) >= WIN_THRESHOLD


def append(row: dict) -> dict:
    scar = {
        'ts':       row.get('ts', ''),
        'symbol':   row.get('symbol', ''),
        'ca':       row.get('ca', ''),
        'be_max_x': float(row.get('be_max_x', 0) or 0),
        'ev':       float(row.get('ev_strat',  0) or 0),
        'fp':       fingerprint(row),
    }
    with _lock:
        with open(SCARS_FILE, 'a') as f:
            f.write(json.dumps(scar) + '\n')
    return scar


def load() -> list:
    if not os.path.exists(SCARS_FILE):
        return []
    with _lock:
        with open(SCARS_FILE) as f:
            return [json.loads(ln) for ln in f if ln.strip()]


def check(fp: dict, winners: list) -> tuple:
    """Retorna (overlap, winner) do melhor match se overlap >= MIN_OVERLAP."""
    best_ov, best_sc = 0, None
    for s in winners:
        ov = sum(1 for k in fp if fp.get(k) == s['fp'].get(k))
        if ov > best_ov:
            best_ov, best_sc = ov, s
    return (best_ov, best_sc) if best_ov >= MIN_OVERLAP else (0, None)


def summary(winners: list) -> str:
    if not winners:
        return 'scars: 0 winners'
    evs = [s['ev'] for s in winners]
    bes = [s['be_max_x'] for s in winners]
    return (f'scars: n={len(winners)}  ev_avg={sum(evs)/len(evs):.3f}  '
            f'be_max_avg={sum(bes)/len(bes):.3f}')


# ── Kotte scars ───────────────────────────────────────────────────────────────
_KOTTE_BUCKETS = {
    'mc_entry':           [10_000, 50_000],  # 0:<10k  1:10k-50k  2:>=50k
    'age_min':            [5,      30],      # 0:<5min  1:5-30min  2:>=30min
    'w7d_x2plus':         [3,       8],      # 0:<3     1:3-7      2:>=8
    'smart_wallets':      [2,       5],      # 0:<2     1:2-4      2:>=5
    'rug_ratio':          [0.20,  0.40],     # 0:<20%   1:20-40%   2:>=40%
    'bundler_rate':       [0.05,  0.15],     # 0:<5%    1:5-15%    2:>=15%
    'fresh_wallet_rate':  [0.30,  0.60],     # 0:<30%   1:30-60%   2:>=60%
    'top_10_holder_rate': [0.20,  0.40],     # 0:<20%   1:20-40%   2:>=40%
}


def kotte_fingerprint(row: dict) -> dict:
    out = {}
    for f, thresholds in _KOTTE_BUCKETS.items():
        v = float(row.get(f, 0) or 0)
        bucket = len(thresholds)
        for i, t in enumerate(thresholds):
            if v < t:
                bucket = i
                break
        out[f] = bucket
    return out


def kotte_is_winner(row: dict) -> bool:
    return float(row.get('x_1h', 0) or 0) >= WIN_THRESHOLD


def kotte_append(row: dict) -> dict:
    scar = {
        'call_ts':    row.get('call_ts', ''),
        'symbol':     row.get('symbol', ''),
        'ca':         row.get('ca', ''),
        'alert_type': row.get('alert_type', ''),
        'tier':       row.get('tier', 0),
        'x_1h':       float(row.get('x_1h', 0) or 0),
        'fp':         kotte_fingerprint(row),
    }
    with _lock:
        with open(KOTTE_SCARS_FILE, 'a') as f:
            f.write(json.dumps(scar) + '\n')
    return scar


def kotte_load() -> list:
    if not os.path.exists(KOTTE_SCARS_FILE):
        return []
    with _lock:
        with open(KOTTE_SCARS_FILE) as f:
            return [json.loads(ln) for ln in f if ln.strip()]


def kotte_check(fp: dict, winners: list) -> tuple:
    """Retorna (overlap, winner) do melhor match se overlap >= KOTTE_MIN_OVERLAP."""
    best_ov, best_sc = 0, None
    for s in winners:
        ov = sum(1 for k in fp if fp.get(k) == s['fp'].get(k))
        if ov > best_ov:
            best_ov, best_sc = ov, s
    return (best_ov, best_sc) if best_ov >= KOTTE_MIN_OVERLAP else (0, None)


def kotte_summary(winners: list) -> str:
    if not winners:
        return 'kotte_scars: 0 winners'
    xs = [s['x_1h'] for s in winners]
    return f'kotte_scars: n={len(winners)}  x1h_avg={sum(xs)/len(xs):.3f}'
