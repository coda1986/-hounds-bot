#!/usr/bin/env python3
"""
signal_scanner.py — Aibot-style scanner (GMGN skillmarket-demos/aitrader)

Pipeline:
  1. Poll market_trending (30s) — tokens em momentum de medio prazo
  2. Hard gates aibot CFG (rug<=0.60, bundler<=0.30, top10<=0.40, smart>=1)
  3. Dev score via portfolio_created_tokens (cache 1h por creator)
  4. Priority score 0-100 (7 componentes: mom5m/buy_pressure/mom1h/turnover/consensus/dev/safety)
  5. Entra se priority_score >= PRIORITY_MIN
  6. Exit: trailing stop 20% desde o high OU timeout 60min
"""

import os, sys, time, csv, json, logging, math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import gmgn_client
import scars as _scars

try:
    import gmgn_terminal as _terminal
    _TRADING_OK = True
except ImportError:
    _TRADING_OK = False

# ── Config ───────────────────────────────────────────────────────────────────
POLL_TRENDING_S  = 30       # poll market_trending a cada 30s
POLL_PENDING_S   = 10       # check pending (price) a cada 10s
LIQ_MIN          = 15_000
MAX_HOLD_MIN     = 60       # hold maximo 60min (timeout)
PRIORITY_MIN     = 60       # score >= 60 = "buyable" (aibot band)
LIVE             = False

OUT_CSV      = os.path.join(os.path.dirname(__file__), 'signal_outcomes.csv')
PENDING_JSON = os.path.join(os.path.dirname(__file__), 'signal_pending.json')
SEEN_JSON    = os.path.join(os.path.dirname(__file__), 'signal_seen.json')
_SEEN_TTL_H  = 12
_DEV_CACHE_TTL = 3600   # 1h — reusa dev_score para mesmo creator

# Hard gates exatos do aibot CFG
MAX_RUG_RATIO    = 0.60
MAX_BUNDLER_RATE = 0.30
MAX_DEV_HOLD_PCT = 0.10
MAX_TOP10_RATE   = 0.40
MIN_SMART_DEGEN  = 1

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Estado ───────────────────────────────────────────────────────────────────
seen        = {}   # ca -> ts_first_seen
seen_names  = {}   # symbol_lower -> ts (anti-copycat 2h)
pending     = {}   # ca -> record dict
_dev_cache  = {}   # creator -> (ts, dev_score_float)
_written_cas = set()
_scars_list  = []   # winners carregados do disco

def _load_franchise_kw():
    _path = os.path.join(os.path.dirname(__file__), 'franchise_names.txt')
    try:
        with open(_path) as f:
            return frozenset(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return frozenset({'trump','elon','musk','fomo','disney','nvidia','apple','google'})

_FRANCHISE_KW = _load_franchise_kw()

# ── CSV ──────────────────────────────────────────────────────────────────────
COLS = [
    'ts', 'ca', 'symbol', 'mc_entry',
    # campos do market_trending
    'smart_degen_count', 'renowned_count',
    'rug_ratio', 'bundler_rate', 'top_10_holder_rate', 'dev_team_hold_rate',
    'is_wash_trading', 'entrapment_ratio',
    'chg_5m', 'chg_1h', 'chg_24h',
    'buy_ratio', 'swaps', 'buys', 'sells',
    'volume', 'holder_count', 'liquidity',
    'launchpad_platform', 'token_age_min',
    # scoring aibot
    'dev_score', 'priority_score',
    # outcomes — trailing stop 20% / timeout 60min
    'p5m', 'p10m', 'p15m', 'p30m', 'p45m', 'p60m',
    'be_max_x', 'hold_min', 'exit_reason', 'ev_strat',
]

def _init_csv():
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, 'r', newline='') as f:
            existing = next(csv.reader(f), [])
        if existing != COLS:
            os.rename(OUT_CSV, OUT_CSV + '.bak')
            log.warning('[init] header desatualizado — backup em %s.bak', OUT_CSV)
    if not os.path.exists(OUT_CSV):
        with open(OUT_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=COLS).writeheader()
        log.info('[init] criado %s', OUT_CSV)

def _dump_pending():
    try:
        export = {ca: {k: v for k, v in rec.items() if k != 'snaps'}
                  for ca, rec in pending.items()}
        with open(PENDING_JSON, 'w') as f:
            json.dump(export, f)
    except Exception:
        pass

def _load_seen() -> dict:
    if not os.path.exists(SEEN_JSON):
        return {}
    try:
        with open(SEEN_JSON) as f:
            data = json.load(f)
        cutoff = time.time() - _SEEN_TTL_H * 3600
        return {ca: ts for ca, ts in data.items() if ts > cutoff}
    except Exception:
        return {}

def _dump_seen():
    try:
        with open(SEEN_JSON, 'w') as f:
            json.dump(seen, f)
    except Exception:
        pass

def _append_outcome(row: dict):
    with open(OUT_CSV, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=COLS, extrasaction='ignore').writerow(row)

# ── Dev Score (fórmula exata aibot skillmarket) ───────────────────────────────
def _dev_score(creator: str) -> float:
    """Dev score 0.15-1.0 baseado no historico do criador.
    Cache 1h por creator address. Retorna 0.5 (neutro) se sem dados.
    """
    if not creator:
        return 0.5
    cached = _dev_cache.get(creator)
    if cached and (time.time() - cached[0]) < _DEV_CACHE_TTL:
        return cached[1]
    try:
        data   = gmgn_client.portfolio_created_tokens(creator)
        tokens = data.get('tokens') or []
        if not tokens:
            score = 0.5
            _dev_cache[creator] = (time.time(), score)
            return score

        total = len(tokens)

        # survival rate — base da formula
        rug_count = sum(1 for t in tokens if float(t.get('rug_ratio', 0) or 0) > 0.3)
        surv = 1.0 - (rug_count / total)

        s = 0.25 + 0.55 * surv

        # inner_count: tokens ainda na bonding curve (nunca graduaram)
        inner = int(data.get('inner_count', 0) or 0)
        s -= 0.30 * max(0.0, min(1.0, (inner - 50) / 950.0))

        # track record: log10(ath_mc) normalizado
        ath_mc = float((data.get('creator_ath_info') or {}).get('ath_mc') or 0)
        if ath_mc > 1e5:
            track = min(1.0, (math.log10(ath_mc) - 5) / 4)
            s += 0.15 * track * surv

        # sec_risk: % tokens com rug_ratio alto
        sec_risk = sum(1 for t in tokens if float(t.get('rug_ratio', 0) or 0) > 0.5)
        s -= 0.35 * (sec_risk / total)

        # dev exited recentemente
        if any(t.get('creator_token_status') == 'creator_sell' for t in tokens[:5]):
            s -= 0.10

        # CTO bonus
        if any(t.get('cto_flag') for t in tokens):
            s += 0.05

        score = max(0.15, min(1.0, s))
        _dev_cache[creator] = (time.time(), score)
        return score
    except Exception as e:
        log.debug('dev_score %s: %s', creator[:8] if creator else '?', e)
        return 0.5


# ── Priority Score (7 componentes, pesos exatos aibot) ───────────────────────
def _priority_score(tok: dict, dev_s: float) -> int:
    """Score 0-100.
    Pesos: mom5m=30, buy_pressure=18, mom1h=12, turnover=12, consensus=12, dev=12, safety=10
    Penalidade: chg_1h <= -12% -> score x 0.4 (descarte direto -> 0)
    buy_ratio < 0.42 -> hard reject (retorna 0)
    """
    TOTAL_W = 106  # soma dos pesos

    chg5  = float(tok.get('price_change_percent5m', 0) or 0) / 100
    chg1h = float(tok.get('price_change_percent1h', 0) or 0) / 100

    # penalidade severa 1h
    if chg1h <= -0.12:
        return 0

    buys  = int(tok.get('buys',  0) or 0)
    sells = int(tok.get('sells', 0) or 0)
    tot   = buys + sells
    buy_ratio = buys / tot if tot > 0 else 0.5

    # buy_pressure hard gate
    if buy_ratio < 0.42:
        return 0

    mc    = float(tok.get('market_cap', 0) or 0)
    vol   = float(tok.get('volume',     0) or 0)
    smart = int(tok.get('smart_degen_count', 0) or 0)
    top10 = float(tok.get('top_10_holder_rate', 1.0) or 1.0)
    rug   = float(tok.get('rug_ratio',  0) or 0)

    raw = 0.0

    # mom5m (30): chg_5m em [-25%, +25%]
    raw += max(0.0, min(1.0, (chg5 + 0.25) / 0.50)) * 30

    # buy_pressure (18): buy_ratio em [0.42, 0.80]
    raw += max(0.0, min(1.0, (buy_ratio - 0.42) / 0.38)) * 18

    # mom1h (12): chg_1h em [-15%, +15%]
    raw += max(0.0, min(1.0, (chg1h + 0.15) / 0.30)) * 12

    # turnover (12): vol/mc — 50% = maximo
    turn = (vol / mc) if mc > 0 else 0
    raw += max(0.0, min(1.0, turn / 0.50)) * 12

    # consensus (12): log10(1 + smart_degen), saturado em 10
    raw += min(1.0, math.log10(1 + smart) / math.log10(11)) * 12

    # dev (12): dev_score ja em [0.15, 1.0]
    raw += dev_s * 12

    # safety (10): rug + top10
    safety = 0.0
    if rug < 0.20:
        safety += 0.5
    if top10 < 0.30:
        safety += 0.5
    elif top10 < 0.40:
        safety += 0.3
    raw += safety * 10

    return int(round(raw / TOTAL_W * 100))


# ── Processamento do trending ─────────────────────────────────────────────────
def process_trending(tokens: list) -> int:
    now   = time.time()
    added = 0

    for tok in tokens:
        ca = tok.get('address', '')
        if not ca or ca in seen:
            continue
        seen[ca] = now

        mc = float(tok.get('market_cap', 0) or 0)

        symbol  = tok.get('symbol', ca[:8])
        creator = tok.get('creator', '')

        # gate franquia
        name_key = symbol.strip().lower()
        if any(kw in name_key for kw in _FRANCHISE_KW):
            log.info('  SKIP %-12s franquia', symbol)
            continue

        # gate nome cooldown 2h
        if now - seen_names.get(name_key, 0) < 7200:
            log.info('  SKIP %-12s nome cooldown', symbol)
            continue

        rug      = float(tok.get('rug_ratio',             0) or 0)
        bundler  = float(tok.get('bundler_rate',           0) or 0)
        top10    = float(tok.get('top_10_holder_rate',     1) or 1)
        dev_hold = float(tok.get('dev_team_hold_rate',     0) or 0)
        smart    = int(tok.get('smart_degen_count',        0) or 0)
        wash     = bool(tok.get('is_wash_trading',         False))
        liq      = float(tok.get('liquidity',              0) or 0)

        # ── Hard gates aibot ─────────────────────────────────────────────
        if rug > MAX_RUG_RATIO:
            log.info('  SKIP %-12s rug=%.2f > %.2f', symbol, rug, MAX_RUG_RATIO)
            continue
        if bundler > MAX_BUNDLER_RATE:
            log.info('  SKIP %-12s bundler=%.2f > %.2f', symbol, bundler, MAX_BUNDLER_RATE)
            continue
        if dev_hold > MAX_DEV_HOLD_PCT:
            log.info('  SKIP %-12s dev_hold=%.2f > %.2f', symbol, dev_hold, MAX_DEV_HOLD_PCT)
            continue
        if top10 > MAX_TOP10_RATE:
            log.info('  SKIP %-12s top10=%.2f > %.2f', symbol, top10, MAX_TOP10_RATE)
            continue
        if smart < MIN_SMART_DEGEN:
            log.info('  SKIP %-12s smart_degen=%d < %d', symbol, smart, MIN_SMART_DEGEN)
            continue
        if wash:
            log.info('  SKIP %-12s wash_trading', symbol)
            continue
        if liq < LIQ_MIN:
            log.info('  SKIP %-12s liq=$%.0f < $%d', symbol, liq, LIQ_MIN)
            continue

        # ── Dev score ────────────────────────────────────────────────────
        dev_s = _dev_score(creator)
        if dev_s < 0.15:
            log.info('  SKIP %-12s dev_score=%.2f < 0.15', symbol, dev_s)
            continue

        # ── Priority score ───────────────────────────────────────────────
        pscore = _priority_score(tok, dev_s)
        if pscore < PRIORITY_MIN:
            chg5 = float(tok.get('price_change_percent5m', 0) or 0)
            log.info('  SKIP %-12s priority=%d < %d  (chg5m=%.1f%%  buy_ratio=%.2f  dev=%.2f)',
                     symbol, pscore, PRIORITY_MIN,
                     chg5,
                     (int(tok.get('buys',0) or 0)) / max(1, int(tok.get('buys',0) or 0) + int(tok.get('sells',0) or 0)),
                     dev_s)
            continue

        # ── Passou todos os gates ────────────────────────────────────────
        chg5  = float(tok.get('price_change_percent5m', 0) or 0) / 100
        chg1h = float(tok.get('price_change_percent1h', 0) or 0) / 100
        chg24 = float(tok.get('price_change_percent',   0) or 0) / 100
        buys  = int(tok.get('buys',  0) or 0)
        sells = int(tok.get('sells', 0) or 0)
        tot   = buys + sells
        buy_ratio = round(buys / tot, 4) if tot > 0 else 0.5
        creation_ts = float(tok.get('creation_timestamp', 0) or tok.get('open_timestamp', 0) or 0)
        age_min = round((now - creation_ts) / 60, 1) if creation_ts > 0 else 0.0

        # Scars check — fingerprint vs winners conhecidos
        _fp = _scars.fingerprint({
            'priority_score':    pscore,
            'dev_score':         dev_s,
            'chg_5m':            chg5,
            'chg_1h':            chg1h,
            'buy_ratio':         buy_ratio,
            'smart_degen_count': smart,
            'token_age_min':     age_min,
            'rug_ratio':         rug,
            'bundler_rate':      bundler,
            'entrapment_ratio':  float(tok.get('entrapment_ratio', 0) or 0),
        })
        ov, match = _scars.check(_fp, _scars_list)
        scar_tag = (f'  [WINNER_PATTERN ov={ov}/{_scars.N_FIELDS} ~ {match["symbol"]} {match["be_max_x"]:.1f}x]'
                    if match else '')

        log.info('  SIGNAL %-12s mc=$%.0f  score=%d  dev=%.2f  chg5m=%.1f%%  chg1h=%.1f%%  buy_ratio=%.2f  smart=%d  rug=%.2f  age=%.0fmin%s',
                 symbol, mc, pscore, dev_s, chg5*100, chg1h*100, buy_ratio, smart, rug, age_min, scar_tag)

        seen_names[name_key] = now
        pending[ca] = {
            'ts':               datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'ca':               ca,
            'symbol':           symbol,
            'mc_entry':         round(mc, 2),
            'smart_degen_count': smart,
            'renowned_count':   int(tok.get('renowned_count', 0) or 0),
            'rug_ratio':        round(rug, 4),
            'bundler_rate':     round(bundler, 4),
            'top_10_holder_rate': round(top10, 4),
            'dev_team_hold_rate': round(dev_hold, 4),
            'is_wash_trading':  int(wash),
            'entrapment_ratio': round(float(tok.get('entrapment_ratio', 0) or 0), 4),
            'chg_5m':           round(chg5, 4),
            'chg_1h':           round(chg1h, 4),
            'chg_24h':          round(chg24, 4),
            'buy_ratio':        buy_ratio,
            'swaps':            int(tok.get('swaps', 0) or 0),
            'buys':             buys,
            'sells':            sells,
            'volume':           round(float(tok.get('volume', 0) or 0), 2),
            'holder_count':     int(tok.get('holder_count', 0) or 0),
            'liquidity':        round(liq, 2),
            'launchpad_platform': tok.get('launchpad_platform', tok.get('exchange', '')),
            'token_age_min':    age_min,
            'dev_score':        round(dev_s, 4),
            'priority_score':   pscore,
            'entry_ts':         now,
            'mc_high':          mc,     # pico de MC (para log de OUTCOME)
            'snaps':            [],
            'live_bought':      False,
        }

        if LIVE and _TRADING_OK:
            try:
                buy_data = _terminal._signed_post("/v1/trade/swap", {}, {
                    "chain":        "sol",
                    "from_address": _terminal.WALLET,
                    "input_token":  _terminal.SOL_MINT,
                    "output_token": ca,
                    "input_amount": str(int(_terminal.BUY_SOL * _terminal._LAMPORTS_PER_SOL)),
                    "slippage":     _terminal.SLIPPAGE,
                    **_terminal._FEE_FIELDS,
                    "is_anti_mev":  _terminal.ANTI_MEV,
                })
                order_id = buy_data.get("order_id") or buy_data.get("hash") or ""
                log.info('  BUY  %-12s order=%s  hold=%dmin', symbol, order_id[:20], MAX_HOLD_MIN)
                pending[ca]['live_bought']   = True
                pending[ca]['live_order_id'] = order_id
            except Exception as e:
                log.error('  BUY FAILED %-12s: %s', symbol, e)
        elif LIVE and not _TRADING_OK:
            log.error('LIVE=True mas gmgn_terminal indisponivel')

        added += 1

    return added


# ── Price tracking e outcomes (igual ao scanner anterior) ────────────────────
def _real_mc(ca: str) -> float:
    try:
        info   = gmgn_client.token_info(ca)
        p      = info.get('price') or {}
        price  = float(p.get('price') or 0)
        supply = float(info.get('circulating_supply') or 0)
        return price * supply
    except Exception as e:
        log.warning('  real_mc %s: %s', ca[:8], e)
        return 0.0


def update_pending():
    now      = time.time()
    to_close = []

    for ca, rec in list(pending.items()):
        elapsed = (now - rec['entry_ts']) / 60.0
        mc_now  = _real_mc(ca)

        if mc_now > 0:
            mc_x = mc_now / rec['mc_entry']
            rec['snaps'].append((elapsed, mc_x))

            if mc_now > rec['mc_high']:
                rec['mc_high'] = mc_now

        # Timeout maximo
        if elapsed >= MAX_HOLD_MIN:
            to_close.append(ca)

    for ca in to_close:
        _close(ca)

    return len(to_close)


def _close(ca: str):
    if ca in _written_cas:
        log.error('DUPLICATE_CLOSE blocked %s', ca[:12])
        pending.pop(ca, None)
        return
    rec   = pending.pop(ca)
    _written_cas.add(ca)
    snaps = rec['snaps']

    def snap_at(minute: float) -> float:
        if not snaps:
            return 1.0
        return min(snaps, key=lambda s: abs(s[0] - minute))[1]

    hold_min    = round((time.time() - rec['entry_ts']) / 60.0, 1)
    exit_reason = 'timeout'
    prices      = {f'p{m}m': round(snap_at(m), 4) for m in [5, 10, 15, 30, 45, 60]}
    be_max_x    = max((s[1] for s in snaps), default=1.0)

    real_mc  = _real_mc(ca)
    ev_strat = round(real_mc / rec['mc_entry'], 4) if real_mc > 0 and rec['mc_entry'] > 0 else 0.0

    row = {**rec, **prices,
           'be_max_x':    round(be_max_x, 4),
           'hold_min':    hold_min,
           'exit_reason': exit_reason,
           'ev_strat':    round(ev_strat, 4)}
    _append_outcome(row)

    if _scars.is_winner(row):
        _scars.append(row)
        _scars_list[:] = _scars.load()
        log.info('  WINNER_SAVED %-12s be_max=%.2fx  scars_n=%d', rec['symbol'], be_max_x, len(_scars_list))

    mc_high_x = round(rec['mc_high'] / rec['mc_entry'], 4) if rec['mc_entry'] > 0 else 0.0
    log.info('  OUTCOME %-12s be_max=%.2fx  high=%.2fx  ev=%.2fx  exit=%s  %.1fmin  score=%d  dev=%.2f',
             rec['symbol'], be_max_x, mc_high_x, ev_strat, exit_reason, hold_min,
             rec.get('priority_score', 0), rec.get('dev_score', 0))

    if LIVE and _TRADING_OK and rec.get('live_bought'):
        try:
            sell_data = _terminal._swap_sell(ca)
            sell_id = sell_data.get("order_id") or sell_data.get("hash") or ""
            log.info('  SELL %-12s order=%s  (%s)', rec['symbol'], sell_id[:20], exit_reason)
        except Exception as e:
            log.error('  SELL FAILED %-12s: %s  *** FECHAR MANUAL ***', rec['symbol'], e)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    _init_csv()
    seen.update(_load_seen())
    log.info('[seen] %d CAs carregados', len(seen))
    global _scars_list
    _scars_list = _scars.load()
    log.info('[%s]', _scars.summary(_scars_list))

    live_status = f'LIVE={LIVE}'
    if LIVE and _TRADING_OK:
        live_status = f'LIVE=True  wallet={_terminal.WALLET[:8]}..  buy={_terminal.BUY_SOL}SOL'
    elif LIVE:
        live_status = 'LIVE=True  *** gmgn_terminal AUSENTE ***'

    log.info('aibot_scanner start — liq>=$%d  gates=[rug<=%.1f bund<=%.1f top10<=%.1f smart>=%d]  priority>=%d  max_hold=%dmin  %s',
             LIQ_MIN,
             MAX_RUG_RATIO, MAX_BUNDLER_RATE, MAX_TOP10_RATE, MIN_SMART_DEGEN,
             PRIORITY_MIN, MAX_HOLD_MIN, live_status)

    last_trending = 0.0
    poll_n        = 0
    consec_err    = 0

    while True:
        try:
            now = time.time()

            # Poll trending a cada 30s
            if now - last_trending >= POLL_TRENDING_S:
                poll_n += 1
                tokens = gmgn_client.market_trending(
                    chain='sol',
                    interval='1h',
                    order_by='smart_degen_count',
                    limit=100,
                )
                added = process_trending(tokens)
                last_trending = now
                log.info('poll#%d  tokens=%d  +new=%d  pending=%d',
                         poll_n, len(tokens), added, len(pending))

            # Check pending a cada 10s (price snapshots)
            closed = update_pending()
            if closed:
                log.info('closed=%d  pending=%d', closed, len(pending))

            _dump_pending()
            _dump_seen()
            consec_err = 0

        except gmgn_client.RateLimitBanned as e:
            sleep_s = max(60, e.reset_at - time.time())
            log.warning('rate ban — dormindo %.0fs', sleep_s)
            time.sleep(sleep_s)
            continue

        except KeyboardInterrupt:
            log.info('interrompido')
            break

        except Exception as e:
            consec_err += 1
            backoff = min(60 * consec_err, 300)
            log.error('erro #%d: %s — retry em %ds', consec_err, e, backoff)
            time.sleep(backoff)
            continue

        time.sleep(POLL_PENDING_S)


if __name__ == '__main__':
    main()
