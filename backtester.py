"""
backtester.py — análise sistemática de gates e combos
Uso: python backtester.py [arquivo_csv]
"""
import csv, sys, itertools
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CSV = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/abib1/signal_outcomes_fresh.csv'

# ── Carregar dados ────────────────────────────────────────────────────────────
rows = []
with open(CSV) as f:
    for r in csv.DictReader(f):
        try:
            def fv(k): return float(r.get(k, 0) or 0)
            def iv(k): return int(r.get(k, 0) or 0)
            ev = fv('ev_strat')
            rows.append({
                'ca':           r.get('ca', ''),
                'ev':           ev,
                'tp1':          iv('tp1_hit'),
                'rug':          1 if ev < 0.70 else 0,   # consistente com scars
                'win':          1 if ev >= 1.50 else 0,  # TP1 hit
                'be':           fv('be_max_x'),
                'p1m':          fv('p1m'),
                # market context
                'mc':           fv('mc_entry'),
                'liq':          fv('liquidity'),
                'volume':       fv('volume'),
                'holders':      iv('holder_count'),
                'age_min':      fv('token_age_min'),
                # aibot scoring
                'pscore':       iv('priority_score'),
                'dev_score':    fv('dev_score'),
                # market trending fields
                'smart_degen':  iv('smart_degen_count'),
                'renowned':     iv('renowned_count'),
                'rug_r':        fv('rug_ratio'),
                'bundler':      fv('bundler_rate'),
                'top10':        fv('top_10_holder_rate'),
                'dev_hold':     fv('dev_team_hold_rate'),
                'entrapment':   fv('entrapment_ratio'),
                'chg5m':        fv('chg_5m'),
                'chg1h':        fv('chg_1h'),
                'buy_ratio':    fv('buy_ratio'),
                'swaps':        iv('swaps'),
            })
        except:
            pass

# Dedup por CA — manter primeira ocorrência (restarts geraram duplicatas)
_seen_ca, _deduped = set(), []
for _r in rows:
    _ca = _r.get('ca', '')
    if not _ca or _ca not in _seen_ca:
        if _ca: _seen_ca.add(_ca)
        _deduped.append(_r)
_removed = len(rows) - len(_deduped)
rows = _deduped

N = len(rows)
print(f'n={N}' + (f'  (dedup: -{_removed} duplicatas)' if _removed else '') + '\n')

# ── Gate definitions ──────────────────────────────────────────────────────────
# Cada gate é (nome, função_filtro)
GATES = [
    # priority score — componente central do aibot
    ('pscore>=62',      lambda r: r['pscore'] >= 62),
    ('pscore>=65',      lambda r: r['pscore'] >= 65),
    ('pscore>=68',      lambda r: r['pscore'] >= 68),
    ('pscore>=72',      lambda r: r['pscore'] >= 72),
    # dev score — qualidade do criador
    ('dev>=0.50',       lambda r: r['dev_score'] >= 0.50),
    ('dev>=0.70',       lambda r: r['dev_score'] >= 0.70),
    ('dev>=0.85',       lambda r: r['dev_score'] >= 0.85),
    # momentum entrada
    ('chg5m>0.05',      lambda r: r['chg5m'] > 0.05),
    ('chg5m>0.10',      lambda r: r['chg5m'] > 0.10),
    ('chg5m>0.20',      lambda r: r['chg5m'] > 0.20),
    ('chg1h>0.10',      lambda r: r['chg1h'] > 0.10),
    ('chg1h>0.30',      lambda r: r['chg1h'] > 0.30),
    # buy pressure
    ('buy_ratio>=0.55', lambda r: r['buy_ratio'] >= 0.55),
    ('buy_ratio>=0.60', lambda r: r['buy_ratio'] >= 0.60),
    ('buy_ratio>=0.65', lambda r: r['buy_ratio'] >= 0.65),
    # smart money
    ('smart>=5',        lambda r: r['smart_degen'] >= 5),
    ('smart>=10',       lambda r: r['smart_degen'] >= 10),
    ('smart>=20',       lambda r: r['smart_degen'] >= 20),
    ('smart>=50',       lambda r: r['smart_degen'] >= 50),
    # token age (aibot agnostic, mas achamos ser importante)
    ('age>=60min',      lambda r: r['age_min'] >= 60),
    ('age>=120min',     lambda r: r['age_min'] >= 120),
    # entrapment (holding presos — campo forte no contract-dd)
    ('entr<0.20',       lambda r: r['entrapment'] < 0.20),
    ('entr<0.10',       lambda r: r['entrapment'] < 0.10),
    # top10
    ('top10<=0.20',     lambda r: r['top10'] <= 0.20),
    ('top10<=0.30',     lambda r: r['top10'] <= 0.30),
    # rug ratio mais permissivo (aibot usa 0.60 como hard gate)
    ('rug_r<0.30',      lambda r: r['rug_r'] < 0.30),
    ('rug_r<0.15',      lambda r: r['rug_r'] < 0.15),
    # volume
    ('vol>50k',         lambda r: r['volume'] >= 50_000),
    ('vol>100k',        lambda r: r['volume'] >= 100_000),
    # price at 1min (early exit signal)
    ('p1m>=0.9',        lambda r: r['p1m'] >= 0.90),
    ('p1m>=1.0',        lambda r: r['p1m'] >= 1.00),
    ('p1m<0.8',         lambda r: 0 < r['p1m'] < 0.80),
]

def stats(g):
    if not g:
        return None
    tn = len(g)
    ev  = sum(r['ev']  for r in g) / tn
    tp  = sum(r['tp1'] for r in g) / tn
    rg  = sum(r['rug'] for r in g) / tn
    wi  = sum(r['win'] for r in g) / tn
    be  = sum(r['be']  for r in g) / tn
    return {'n': tn, 'pct': 100*tn/N, 'ev': ev, 'tp1': 100*tp, 'win': 100*wi, 'rug': 100*rg, 'be': be}

def fmt(s):
    if s is None: return 'n=0'
    return (f'n={s["n"]:>3} ({s["pct"]:>3.0f}%)'
            f'  EV={s["ev"]:>6.3f}x'
            f'  BE={s["be"]:>6.3f}x'
            f'  TP1={s["tp1"]:>4.0f}%'
            f'  WIN={s["win"]:>4.0f}%'
            f'  RUG={s["rug"]:>4.0f}%')

base = stats(rows)
print(f'{"BASELINE":<35} {fmt(base)}')
print()

# ── Seção 0: distribuição aibot (pscore + dev_score) ─────────────────────────
print('=' * 100)
print('SECAO 0 — DISTRIBUICAO AIBOT (priority_score e dev_score)')
print('=' * 100)

print('\n  priority_score buckets:')
for lo, hi in [(60,64),(65,69),(70,74),(75,79),(80,100)]:
    g = [r for r in rows if lo <= r['pscore'] <= hi]
    s = stats(g)
    if s:
        print(f'    pscore {lo}-{hi}  {fmt(s)}')

print('\n  dev_score buckets:')
for lo, hi in [(0.15,0.40),(0.40,0.60),(0.60,0.75),(0.75,0.90),(0.90,1.01)]:
    g = [r for r in rows if lo <= r['dev_score'] < hi]
    s = stats(g)
    if s:
        print(f'    dev {lo:.2f}-{hi:.2f}  {fmt(s)}')

print('\n  chg_5m buckets:')
for lo, hi, label in [(-1,-0.05,'<-5%'),(-0.05,0.05,'-5%~+5%'),(0.05,0.15,'+5%~+15%'),(0.15,0.30,'+15%~+30%'),(0.30,10,'>+30%')]:
    g = [r for r in rows if lo <= r['chg5m'] < hi]
    s = stats(g)
    if s:
        print(f'    chg5m {label:<12}  {fmt(s)}')

print()

# ── Seção 1: gates individuais ────────────────────────────────────────────────
print('=' * 90)
print('SEÇÃO 1 — GATES INDIVIDUAIS')
print('=' * 90)
single_results = []
for name, fn in GATES:
    g = [r for r in rows if fn(r)]
    s = stats(g)
    if s and s['n'] >= 10:
        single_results.append((name, fn, s, g))
        marker = ' <<' if s['rug'] <= 10 and s['ev'] >= 0.95 else ''
        print(f'  {name:<20} {fmt(s)}{marker}')

# ── Seção 2: combos 2-way (só gates com n>=20 individualmente) ────────────────
print()
print('=' * 90)
print('SEÇÃO 2 — COMBOS 2 GATES (mínimo n=15)')
print('=' * 90)
strong = [(name, fn, s, g) for name, fn, s, g in single_results if s['n'] >= 20]
combo2_results = []
for (n1, f1, s1, _), (n2, f2, s2, _) in itertools.combinations(strong, 2):
    g = [r for r in rows if f1(r) and f2(r)]
    s = stats(g)
    if s and s['n'] >= 15:
        label = f'{n1} + {n2}'
        combo2_results.append((label, s))
        marker = ' <<<' if s['rug'] <= 5 and s['ev'] >= 1.0 else (' <<' if s['rug'] <= 8 else '')
        print(f'  {label:<40} {fmt(s)}{marker}')

# ── Seção 3: top combos por EV positivo e baixo rug ──────────────────────────
print()
print('=' * 90)
print('SEÇÃO 3 — TOP COMBOS (EV>=1.0 e RUG<=10%)')
print('=' * 90)
top = [(l, s) for l, s in combo2_results if s['ev'] >= 1.0 and s['rug'] <= 10 and s['n'] >= 15]
top.sort(key=lambda x: (-x[1]['ev'], x[1]['rug']))
if top:
    for label, s in top:
        print(f'  {label:<40} {fmt(s)}')
else:
    print('  Nenhum combo atingiu EV>=1.0 e RUG<=10% com n>=15')

# ── Seção 4: melhor combo por rug mínimo ─────────────────────────────────────
print()
print('=' * 90)
print('SEÇÃO 4 — RANKING POR RUG MÍNIMO (n>=15)')
print('=' * 90)
all_combos = [(l, s) for l, s in combo2_results]
all_combos.sort(key=lambda x: (x[1]['rug'], -x[1]['n']))
for label, s in all_combos[:15]:
    print(f'  {label:<40} {fmt(s)}')

# ── Seção 5: dentro do melhor gate individual, testar todos os outros ─────────
best_single = min(single_results, key=lambda x: x[2]['rug'])
print()
print('=' * 90)
print(f'SEÇÃO 5 — DENTRO DO MELHOR GATE ({best_single[0]}, RUG={best_single[2]["rug"]:.0f}%)')
print('=' * 90)
base_g = [r for r in rows if best_single[1](r)]
print(f'  base: {fmt(stats(base_g))}')
sub_results = []
for name, fn, _, _ in single_results:
    if name == best_single[0]:
        continue
    g = [r for r in base_g if fn(r)]
    s = stats(g)
    if s and s['n'] >= 10:
        sub_results.append((name, s))
sub_results.sort(key=lambda x: (x[1]['rug'], -x[1]['ev']))
for name, s in sub_results:
    marker = ' <<<' if s['rug'] == 0 else (' <<' if s['rug'] <= 5 else '')
    print(f'  + {name:<20} {fmt(s)}{marker}')
