"""
analyze_hounds.py — single source of truth for hounds_outcomes.csv analysis
EV = mean(min(be_max_x, 2.0)) — total return clipped at 2x, break-even = 1.0

be_max_x = peak_mc / mc_entry — tracked in real time per trade
"""
import pandas as pd
import numpy as np
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')

CSV = os.path.join(os.environ['USERPROFILE'], 'hounds_outcomes_fresh.csv')

# ── Load and clean ─────────────────────────────────────────────────────────────
df_raw = pd.read_csv(CSV)
skip_str = {'symbol', 'ca', 'vex_score_label', 'vex_lore_label'}
for col in df_raw.select_dtypes(include='object').columns:
    if col in skip_str:
        continue
    converted = pd.to_numeric(df_raw[col], errors='coerce')
    if converted.notna().sum() > 0:
        df_raw[col] = converted
df_raw = df_raw.replace(-1, np.nan).replace(-1.0, np.nan)

# non-migrated tokens with valid MC entry
df = df_raw[(df_raw['mc'] > 0) & (df_raw['migrated'] == 0)].copy().reset_index(drop=True)

# ── Core stats function — DO NOT CHANGE FORMULA ────────────────────────────────
def stats(g, label=''):
    if len(g) == 0:
        return None
    bmax = pd.to_numeric(g['be_max_x'], errors='coerce').dropna()
    return {
        'gate':  label,
        'n':     len(bmax),
        'WR150': f"{(bmax >= 1.5).mean()*100:.0f}%",
        'WR200': f"{(bmax >= 2.0).mean()*100:.0f}%",
        'avg':   round(bmax.mean(), 2),
        'EV@2x': round(bmax.clip(upper=2.0).mean(), 3),  # ← LOCKED FORMULA
    }

def table(rows):
    df_t = pd.DataFrame([r for r in rows if r])
    print(df_t.to_string(index=False))
    print()

# ── Reusable masks (thresholds are examples — tune for your data) ──────────────
dead     = df['progress'].between(50, 60) | df['progress'].between(70, 80)
nodead   = ~dead
inflow40 = df['inflow_1m'] >= 40   # adjust threshold to your signal source

# ── 1. BASELINE ────────────────────────────────────────────────────────────────
print("=" * 60)
print("1. BASELINE")
print("=" * 60)
table([stats(df, 'all valid')])

# ── 2. PRIMARY GATE ────────────────────────────────────────────────────────────
print("=" * 60)
print("2. PRIMARY GATE")
print("=" * 60)
table([
    stats(df[nodead],           'nodead'),
    stats(df[nodead & inflow40], 'nodead + inflow>=40'),
])

# ── 3. SNAPSHOT ANALYSIS ───────────────────────────────────────────────────────
snap_cols = [c for c in df.columns if c.startswith('p') and c.endswith('m') and c[1:-1].isdigit()]
print("=" * 60)
print(f"3. SNAPSHOTS  (columns: {snap_cols})")
print("=" * 60)
has_snap = df[snap_cols].notna().any(axis=1)
df_snap  = df[has_snap].copy()
print(f"Trades with snapshot: {len(df_snap)} / {len(df)}")
if len(df_snap) > 0:
    rows_snap = []
    for col in snap_cols:
        minute = int(col[1:-1])
        vals = pd.to_numeric(df_snap[col], errors='coerce').clip(upper=2.0)
        if vals.notna().sum() >= 3:
            rows_snap.append({
                'minute': minute,
                'n':      vals.notna().sum(),
                'EV@2x':  round(vals.mean(), 3),
                'WR150':  f"{(pd.to_numeric(df_snap[col], errors='coerce') >= 1.5).mean()*100:.0f}%",
            })
    print(pd.DataFrame(rows_snap).to_string(index=False))
print()
