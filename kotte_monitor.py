#!/usr/bin/env python3
"""
kotte_monitor.py — escuta @kotte_memescan_bot, enriquece com GMGN e coleta outcomes

Captura: Smart wallet buy / Cluster / Dev launch / KOL buy
Na entrada: token_info + token_security (fingerprint completo)
Snaps de preço: 5m, 15m, 30m, 60m
"""

import asyncio, csv, json, logging, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import gmgn_client
import scars

from telethon import TelegramClient, events

# ── Config ────────────────────────────────────────────────────────────────────
BOT_USERNAME = 'kotte_memescan_bot'
SNAP_MINS    = [5, 15, 30, 60]
OUT_CSV      = os.path.join(os.path.dirname(__file__), 'kotte_outcomes.csv')
PENDING_JSON = os.path.join(os.path.dirname(__file__), 'kotte_pending.json')
CHECK_S      = 30   # outcome snaps
POLL_S       = 5    # kotte message poll

COLS = [
    # identidade do sinal
    'call_ts', 'ca', 'symbol', 'alert_type', 'tier',
    # do alert
    'mc_entry', 'age_min', 'wallet_id', 'sol_spent',
    'w7d_alerts', 'w7d_x2plus', 'w7d_x5plus', 'w7d_best_x',
    # token_info — snapshot na entrada
    'holder_count', 'smart_wallets', 'renowned_wallets',
    'bundler_wallets', 'fresh_wallets', 'sniper_wallets',
    'top70_sniper_rate', 'bot_degen_rate',
    'creator_created_count', 'fresh_wallet_rate',
    'top_10_holder_rate', 'creator_hold_rate',
    'rug_ratio', 'bundler_rate',
    'buys_1m', 'sells_1m', 'volume_5m',
    'buy_vol_1m', 'sell_vol_1m', 'net_buy_1m',
    'launchpad_platform',
    # token_security
    'burn_ratio', 'is_renounced',
    # outcomes
    'x_5m', 'x_15m', 'x_30m', 'x_1h',
]

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Parser ────────────────────────────────────────────────────────────────────
_CA      = re.compile(r'`([1-9A-HJ-NP-Za-km-z]{32,44})`')
_SYMBOL  = re.compile(r'🪙\s+\*?\*?([^\n*]+)\*?\*?')
_MC      = re.compile(r'Market cap[:\s]+\$([0-9,]+)')
_AGE_MIN = re.compile(r'(\d+)\s+min after')
_AGE_H   = re.compile(r'(\d+\.?\d*)\s+h after')
_AGE_SEC = re.compile(r'(\d+)\s+sec after')
_WALLET  = re.compile(r'\[([^\]]+)\]')
_SOL     = re.compile(r'Spent[:\s]+\*?\*?([\d.]+)\s+SOL')
_ALERTS  = re.compile(r'(\d+)\s+buys alerted')
_X2PLUS  = re.compile(r'(\d+)\s+went x2\+')
_X5PLUS  = re.compile(r'(\d+)\s+went x5\+')
_BEST    = re.compile(r'Best[:\s]+.+?([\d.]+)x')


def _parse_alert_type(text):
    if 'TIER 1' in text:
        return 'SMART_WALLET', 1
    if 'TIER 2' in text:
        return 'SMART_WALLET', 2
    if 'TIER 3' in text:
        return 'SMART_WALLET', 3
    if 'CLUSTER' in text.upper():
        return 'CLUSTER', 0
    if 'QLO LAUNCH' in text or 'DEV LAUNCH' in text or 'LAUNCHES' in text:
        return 'DEV_LAUNCH', 0
    if 'KOL' in text.upper():
        return 'KOL', 0
    return None, None


def _mc_val(s):
    try:
        return float(s.replace(',', ''))
    except Exception:
        return 0.0


def _age_to_min(text):
    m = _AGE_SEC.search(text)
    if m:
        return round(int(m.group(1)) / 60, 2)
    m = _AGE_MIN.search(text)
    if m:
        return int(m.group(1))
    m = _AGE_H.search(text)
    if m:
        return round(float(m.group(1)) * 60, 1)
    return None


def parse_alert(text):
    alert_type, tier = _parse_alert_type(text)
    if not alert_type:
        return None

    ca_m = _CA.search(text)
    if not ca_m:
        return None

    sym_m  = _SYMBOL.search(text)
    mc_m   = _MC.search(text)
    wal_m  = _WALLET.search(text)
    sol_m  = _SOL.search(text)
    al_m   = _ALERTS.search(text)
    x2_m   = _X2PLUS.search(text)
    x5_m   = _X5PLUS.search(text)
    best_m = _BEST.search(text)

    return {
        'ca':          ca_m.group(1),
        'symbol':      sym_m.group(1).strip() if sym_m else '',
        'alert_type':  alert_type,
        'tier':        tier,
        'mc_entry':    _mc_val(mc_m.group(1)) if mc_m else 0.0,
        'age_min':     _age_to_min(text) or 0,
        'wallet_id':   wal_m.group(1) if wal_m else '',
        'sol_spent':   float(sol_m.group(1)) if sol_m else 0.0,
        'w7d_alerts':  int(al_m.group(1))   if al_m  else 0,
        'w7d_x2plus':  int(x2_m.group(1))   if x2_m  else 0,
        'w7d_x5plus':  int(x5_m.group(1))   if x5_m  else 0,
        'w7d_best_x':  float(best_m.group(1)) if best_m else 0.0,
    }


# ── GMGN enrich ───────────────────────────────────────────────────────────────
def enrich_entry(ca):
    out = {}
    try:
        info  = gmgn_client.token_info(ca)
        p     = info.get('price', {}) or {}
        stat  = info.get('stat', {}) or {}
        wtags = info.get('wallet_tags_stat', {}) or {}
        sec   = info.get('security', {}) or {}
        bv    = float(p.get('buy_volume_1m', 0) or 0)
        sv    = float(p.get('sell_volume_1m', 0) or 0)
        out.update({
            'holder_count':          int(info.get('holder_count', 0) or 0),
            'smart_wallets':         int(wtags.get('smart_wallets', 0) or 0),
            'renowned_wallets':      int(wtags.get('renowned_wallets', 0) or 0),
            'bundler_wallets':       int(wtags.get('bundler_wallets', 0) or 0),
            'fresh_wallets':         int(wtags.get('fresh_wallets', 0) or 0),
            'sniper_wallets':        int(wtags.get('sniper_wallets', 0) or 0),
            'top70_sniper_rate':     round(float(stat.get('top70_sniper_hold_rate', 0) or 0), 4),
            'bot_degen_rate':        round(float(stat.get('bot_degen_rate', 0) or 0), 4),
            'creator_created_count': int(stat.get('creator_created_count', 0) or 0),
            'fresh_wallet_rate':     round(float(stat.get('fresh_wallet_rate', 0) or 0), 4),
            'top_10_holder_rate':    round(float(stat.get('top_10_holder_rate', 0) or 0), 4),
            'creator_hold_rate':     round(float(stat.get('creator_hold_rate', 0) or 0), 4),
            'rug_ratio':             round(float(sec.get('rug_ratio', 0) or 0), 4),
            'bundler_rate':          round(float(sec.get('bundler_rate', 0) or 0), 4),
            'buys_1m':               int(p.get('buys_1m', 0) or 0),
            'sells_1m':              int(p.get('sells_1m', 0) or 0),
            'volume_5m':             round(float(p.get('volume_5m', 0) or 0), 2),
            'buy_vol_1m':            round(bv, 2),
            'sell_vol_1m':           round(sv, 2),
            'net_buy_1m':            round(bv - sv, 2),
            'launchpad_platform':    info.get('launchpad_platform', ''),
        })
    except Exception as e:
        log.warning('[enrich] token_info %s: %s', ca[:8], e)

    try:
        sec2 = gmgn_client.token_security(ca)
        out.update({
            'burn_ratio':   round(float(sec2.get('burn_ratio', 0) or 0), 4),
            'is_renounced': int(bool(sec2.get('renounced'))),
        })
    except Exception as e:
        log.warning('[enrich] token_security %s: %s', ca[:8], e)

    return out


# ── Persistência ──────────────────────────────────────────────────────────────
pending = {}


def _load_pending():
    global pending
    if Path(PENDING_JSON).exists():
        try:
            pending = json.loads(Path(PENDING_JSON).read_text())
            log.info('[init] pending: %d tokens', len(pending))
        except Exception:
            pass


def _save_pending():
    try:
        Path(PENDING_JSON).write_text(json.dumps(pending))
    except Exception:
        pass


def _init_csv():
    if Path(OUT_CSV).exists():
        with open(OUT_CSV, 'r', newline='', encoding='utf-8') as f:
            existing = next(csv.reader(f), [])
        if existing != COLS:
            bak = OUT_CSV + '.bak'
            Path(OUT_CSV).rename(bak)
            log.warning('[init] header mudou — backup %s', bak)
    if not Path(OUT_CSV).exists():
        with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, COLS).writeheader()
        log.info('[init] criado %s', OUT_CSV)


def _write_outcome(row):
    with open(OUT_CSV, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, COLS, extrasaction='ignore').writerow(row)


# ── Outcome loop ──────────────────────────────────────────────────────────────
def _fetch_mc(ca):
    try:
        info   = gmgn_client.token_info(ca)
        p      = info.get('price', {}) or {}
        price  = float(p.get('price') or 0)
        supply = float(info.get('circulating_supply') or 0)
        return price * supply
    except Exception:
        return 0.0


async def outcome_loop():
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(CHECK_S)
        now      = time.time()
        done_cas = []

        for ca, rec in list(pending.items()):
            call_ts  = rec['call_ts']
            mc_in    = rec.get('mc_entry', 0)
            snaps    = rec.setdefault('snaps', {})
            changed  = False

            for m in SNAP_MINS:
                key = str(m)
                if snaps.get(key) is None and now >= call_ts + m * 60:
                    mc_now     = await loop.run_in_executor(None, _fetch_mc, ca)
                    snaps[key] = mc_now
                    x          = round(mc_now / mc_in, 4) if mc_in > 0 else 0.0
                    log.info('[snap %dm] %s  x=%.2fx', m, rec.get('symbol', ''), x)
                    changed = True

            if changed:
                _save_pending()

            if all(snaps.get(str(m)) is not None for m in SNAP_MINS):
                def _x(m):
                    mc = snaps.get(str(m), 0) or 0
                    return round(mc / mc_in, 4) if mc_in > 0 else 0.0

                row = {k: v for k, v in rec.items() if k != 'snaps'}
                row.update({'x_5m': _x(5), 'x_15m': _x(15), 'x_30m': _x(30), 'x_1h': _x(60)})
                _write_outcome(row)
                log.info('[done] %s  5m=%.2fx  15m=%.2fx  30m=%.2fx  1h=%.2fx',
                         rec.get('symbol', ''), _x(5), _x(15), _x(30), _x(60))

                if scars.kotte_is_winner(row):
                    scar = scars.kotte_append(row)
                    _kotte_winners.append(scar)
                    log.info('[SCARS ADD] %s  x_1h=%.2fx  fp=%s',
                             row.get('symbol', ''), _x(60), scar['fp'])

                done_cas.append(ca)

        for ca in done_cas:
            pending.pop(ca, None)
        if done_cas:
            _save_pending()


# ── Processar mensagem ────────────────────────────────────────────────────────
async def process_message(text, msg_ts):
    sig = parse_alert(text)
    if not sig:
        return

    ca = sig['ca']
    if ca in pending:
        return

    sig['call_ts'] = msg_ts

    loop     = asyncio.get_event_loop()
    enriched = await loop.run_in_executor(None, enrich_entry, ca)
    sig.update(enriched)

    pending[ca] = sig
    _save_pending()

    fp = scars.kotte_fingerprint(sig)
    ov, match = scars.kotte_check(fp, _kotte_winners)
    if match:
        log.info('[SCARS MATCH] %s  overlap=%d/8  like=%s (%.2fx)',
                 sig['symbol'], ov, match['symbol'], match['x_1h'])

    log.info('[%s T%s] %s  mc=$%.0f  age=%smin  wallet=%s  w7d=%s/x2=%s  '
             'smart=%d  rug=%.2f  net_buy=%.0f',
             sig['alert_type'], sig['tier'], sig['symbol'],
             sig['mc_entry'], sig['age_min'], sig['wallet_id'],
             sig['w7d_alerts'], sig['w7d_x2plus'],
             sig.get('smart_wallets', 0),
             sig.get('rug_ratio', 0),
             sig.get('net_buy_1m', 0))


# ── Poll loop (mais confiável que events para chat privado com bot) ────────────
async def poll_loop(client, bot_entity):
    last_id = 0
    # inicializar com o ID mais recente sem processar histórico
    async for msg in client.iter_messages(bot_entity, limit=1):
        last_id = msg.id
    log.info('[poll] iniciando a partir de msg_id=%d', last_id)

    while True:
        await asyncio.sleep(POLL_S)
        try:
            new_msgs = []
            async for msg in client.iter_messages(bot_entity, limit=20):
                if msg.id <= last_id:
                    break
                new_msgs.append(msg)

            log.info('[poll] tick  last_id=%d  novas=%d', last_id, len(new_msgs))

            for msg in reversed(new_msgs):
                last_id = msg.id
                ts = int(msg.date.replace(tzinfo=timezone.utc).timestamp())
                await process_message(msg.text or '', ts)
        except Exception as e:
            log.warning('[poll] erro: %s', e)


# ── Main ──────────────────────────────────────────────────────────────────────
_kotte_winners: list = []


async def main():
    global _kotte_winners
    _load_pending()
    _init_csv()
    _kotte_winners = scars.kotte_load()
    log.info('[scars] %s', scars.kotte_summary(_kotte_winners))

    api_id   = int(os.environ['TG_API_ID'])
    api_hash = os.environ['TG_API_HASH']

    client = TelegramClient('tg_session', api_id, api_hash)
    await client.start()

    bot_entity = await client.get_entity(BOT_USERNAME)
    log.info('[init] bot_id=%s  SNAP_MINS=%s  OUT_CSV=%s',
             bot_entity.id, SNAP_MINS, OUT_CSV)

    # handler event-driven (instantâneo quando a sessão recebe push)
    @client.on(events.NewMessage(chats=bot_entity.id))
    async def on_message(event):
        ts = int(event.message.date.replace(tzinfo=timezone.utc).timestamp())
        log.info('[event] mensagem recebida id=%d', event.message.id)
        await process_message(event.message.text or '', ts)

    # forçar sync de updates para ativar o push
    await client.get_dialogs()

    asyncio.create_task(outcome_loop())
    asyncio.create_task(poll_loop(client, bot_entity))

    log.info('[init] escutando @%s (event+poll %ds)...', BOT_USERNAME, POLL_S)
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
