#!/usr/bin/env python3
"""
kotte_backtester.py — análise de edge do kotte_monitor
Uso: python kotte_backtester.py kotte_outcomes_fresh.csv
"""

import csv, sys
from collections import defaultdict

# ── I/O ───────────────────────────────────────────────────────────────────────
def load(path):
    with open(path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    # só outcomes completos (x_1h preenchido)
    return [r for r in rows if r.get('x_1h') not in ('', None, '0', '0.0')]

def _f(r, k, default=0.0):
    try: return float(r.get(k) or default)
    except: return default

def _i(r, k, default=0):
    try: return int(float(r.get(k) or default))
    except: return default

# ── Stats ─────────────────────────────────────────────────────────────────────
def stats(rows, label=''):
    if not rows:
        return f'  {label:40s}  n=0'
    n    = len(rows)
    x1h  = [_f(r, 'x_1h') for r in rows]
    x5m  = [_f(r, 'x_5m')  for r in rows]
    x15m = [_f(r, 'x_15m') for r in rows]
    x30m = [_f(r, 'x_30m') for r in rows]
    avg  = lambda lst: sum(lst) / len(lst) if lst else 0
    wr   = lambda lst, t: 100 * sum(1 for x in lst if x >= t) / len(lst) if lst else 0
    return (
        f'  {label:40s}  n={n:3d}  '
        f'5m={avg(x5m):.2f}x  15m={avg(x15m):.2f}x  '
        f'30m={avg(x30m):.2f}x  1h={avg(x1h):.2f}x  '
        f'WR1.5={wr(x1h,1.5):.0f}%  WR2={wr(x1h,2.0):.0f}%'
    )

def section(title):
    print(f'\n{"─"*80}')
    print(f'  {title}')
    print(f'{"─"*80}')

# ── Análise ───────────────────────────────────────────────────────────────────
def run(rows):
    n = len(rows)
    print(f'\n{"═"*80}')
    print(f'  KOTTE BACKTESTER  —  {n} outcomes completos')
    print(f'{"═"*80}')

    # ── Seção 0: Baseline ────────────────────────────────────────────────────
    section('0. BASELINE')
    print(stats(rows, 'TOTAL'))

    # ── Seção 1: Por alert_type + tier ───────────────────────────────────────
    section('1. POR TIPO DE SINAL')
    groups = defaultdict(list)
    for r in rows:
        t    = r.get('alert_type', '')
        tier = r.get('tier', '0')
        key  = f'{t} T{tier}' if t == 'SMART_WALLET' else t
        groups[key].append(r)
    for key in sorted(groups):
        print(stats(groups[key], key))

    # ── Seção 2: Por age_min (idade do token no momento do alerta) ───────────
    section('2. POR IDADE DO TOKEN (age_min)')
    buckets = {'<5min': [], '5-30min': [], '30min-2h': [], '>2h': []}
    for r in rows:
        a = _f(r, 'age_min')
        if   a < 5:   buckets['<5min'].append(r)
        elif a < 30:  buckets['5-30min'].append(r)
        elif a < 120: buckets['30min-2h'].append(r)
        else:         buckets['>2h'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 3: Por track record da wallet (w7d_x2plus) ────────────────────
    section('3. POR TRACK RECORD DA WALLET (w7d_x2plus)')
    buckets = {'0-2': [], '3-7': [], '8-15': [], '>15': []}
    for r in rows:
        w = _i(r, 'w7d_x2plus')
        if   w <= 2:  buckets['0-2'].append(r)
        elif w <= 7:  buckets['3-7'].append(r)
        elif w <= 15: buckets['8-15'].append(r)
        else:         buckets['>15'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 4: Por MC de entrada ───────────────────────────────────────────
    section('4. POR MC DE ENTRADA')
    buckets = {'<10k': [], '10k-50k': [], '50k-200k': [], '>200k': []}
    for r in rows:
        mc = _f(r, 'mc_entry')
        if   mc < 10_000:  buckets['<10k'].append(r)
        elif mc < 50_000:  buckets['10k-50k'].append(r)
        elif mc < 200_000: buckets['50k-200k'].append(r)
        else:              buckets['>200k'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 5: GMGN — rug_ratio ────────────────────────────────────────────
    section('5. RUG RATIO (GMGN)')
    buckets = {'<0.20': [], '0.20-0.40': [], '>0.40': []}
    for r in rows:
        rug = _f(r, 'rug_ratio')
        if   rug < 0.20: buckets['<0.20'].append(r)
        elif rug < 0.40: buckets['0.20-0.40'].append(r)
        else:            buckets['>0.40'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 6: Smart wallets GMGN ─────────────────────────────────────────
    section('6. SMART WALLETS (GMGN) NA ENTRADA')
    buckets = {'0': [], '1-2': [], '3-5': [], '>5': []}
    for r in rows:
        sw = _i(r, 'smart_wallets')
        if   sw == 0: buckets['0'].append(r)
        elif sw <= 2: buckets['1-2'].append(r)
        elif sw <= 5: buckets['3-5'].append(r)
        else:         buckets['>5'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 7: Bundler rate ────────────────────────────────────────────────
    section('7. BUNDLER RATE')
    buckets = {'<5%': [], '5-15%': [], '>15%': []}
    for r in rows:
        b = _f(r, 'bundler_rate')
        if   b < 0.05: buckets['<5%'].append(r)
        elif b < 0.15: buckets['5-15%'].append(r)
        else:          buckets['>15%'].append(r)
    for k, v in buckets.items():
        print(stats(v, k))

    # ── Seção 8: Combos promissores ──────────────────────────────────────────
    section('8. COMBOS (requer n>=5 por grupo)')
    combos = {
        'T1+T2 (top tiers)':       [r for r in rows if r.get('alert_type')=='SMART_WALLET' and _i(r,'tier') <= 2],
        'age<30min + rug<0.20':    [r for r in rows if _f(r,'age_min')<30 and _f(r,'rug_ratio')<0.20],
        'w7d_x2plus>=5':           [r for r in rows if _i(r,'w7d_x2plus')>=5],
        'w7d_x2plus>=5 + age<30':  [r for r in rows if _i(r,'w7d_x2plus')>=5 and _f(r,'age_min')<30],
        'smart_w>=2 + rug<0.20':   [r for r in rows if _i(r,'smart_wallets')>=2 and _f(r,'rug_ratio')<0.20],
        'mc<50k + age<30min':      [r for r in rows if _f(r,'mc_entry')<50_000 and _f(r,'age_min')<30],
        'CLUSTER':                 [r for r in rows if r.get('alert_type')=='CLUSTER'],
    }
    for label, grp in combos.items():
        if len(grp) >= 3:
            print(stats(grp, label))
        else:
            print(f'  {label:40s}  n={len(grp)} (insuficiente)')

    print()


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'kotte_outcomes_fresh.csv'
    try:
        rows = load(path)
    except FileNotFoundError:
        print(f'[erro] arquivo não encontrado: {path}')
        sys.exit(1)
    if not rows:
        print(f'[aviso] nenhum outcome completo em {path}')
        sys.exit(0)
    run(rows)
