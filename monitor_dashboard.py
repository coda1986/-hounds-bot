#!/usr/bin/env python3
"""
monitor_dashboard.py — Signal Scanner (aibot / market_trending)
Acesse: http://34.23.5.231:8080
"""
import os, csv, json, time, datetime, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

BASE_DIR       = '/home/abib1/signal_bot'
SIGNAL_CSV     = os.path.join(BASE_DIR, 'signal_outcomes.csv')
SIGNAL_PENDING = os.path.join(BASE_DIR, 'signal_pending.json')
SIGNAL_PY      = os.path.join(BASE_DIR, 'signal_scanner.py')
KOTTE_CSV      = os.path.join(BASE_DIR, 'kotte_outcomes.csv')
KOTTE_PENDING  = os.path.join(BASE_DIR, 'kotte_pending.json')
REFRESH_S      = 60

_cache_lock = threading.Lock()
_cache      = {'html': b'<p>Carregando...</p>', 'ts': 0}


# ── I/O helpers ───────────────────────────────────────────────────────────────
def _read_csv(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _scanner_cfg():
    cfg = {'MAX_HOLD_MIN': 60, 'PRIORITY_MIN': 60, 'LIVE': False}
    try:
        with open(SIGNAL_PY, encoding='utf-8') as f:
            for line in f:
                l = line.strip()
                if l.startswith('LIVE ') or l.startswith('LIVE='):
                    val = l.split('=')[1].split('#')[0].strip()
                    cfg['LIVE'] = (val == 'True')
                    continue
                for k in ('MAX_HOLD_MIN', 'PRIORITY_MIN'):
                    if l.startswith(k + ' ') or l.startswith(k + '='):
                        try:
                            cfg[k] = float(l.split('=')[1].split('#')[0].strip())
                        except Exception:
                            pass
    except Exception:
        pass
    return cfg


# ── Formatters ────────────────────────────────────────────────────────────────
def _mc(v):
    try:
        v = float(v or 0)
        if v >= 1_000_000: return f'${v/1_000_000:.1f}M'
        if v >= 1_000:     return f'${v/1_000:.0f}k'
        return f'${v:.0f}'
    except Exception:
        return '-'

def _i(v):
    try: return str(int(float(v or 0)))
    except: return '-'

_BRT = datetime.timezone(datetime.timedelta(hours=-3))

def _age(unix_ts):
    try:
        s = int(time.time()) - int(unix_ts)
        if s < 3600: return f'{s//60}m'
        return f'{s//3600}h{(s%3600)//60}m'
    except: return '-'

def _xcol(x):
    try:
        x = float(x)
        if x >= 2.0: return '#57d97b'
        if x >= 1.5: return '#3fb950'
        if x >= 1.0: return '#f0b429'
        return '#e05c5c'
    except: return '#8b9eb0'

def _chg_col(v):
    try:
        v = float(v or 0)
        if v > 0.05:  return '#57d97b'
        if v > 0:     return '#3fb950'
        if v > -0.05: return '#f0b429'
        return '#e05c5c'
    except: return '#8b9eb0'

def _score_col(s):
    try:
        s = int(s)
        if s >= 75: return '#57d97b'
        if s >= 65: return '#f0b429'
        return '#8b9eb0'
    except: return '#8b9eb0'

def _bundler_badge(v):
    try:
        v = float(v or 0)
        c = '#e05c5c' if v > 0.20 else ('#f0b429' if v > 0.10 else '#57d97b')
        return f'<span style="color:{c}">{v*100:.0f}%</span>'
    except:
        return '-'


# ── Stats ─────────────────────────────────────────────────────────────────────
def _sig_stats(rows):
    valid = [r for r in rows if r.get('be_max_x') not in ('', None, '0')]
    if not valid:
        return dict(n=0, bmax=0, ev=0, wr15=0, wr20=0, rug=0)
    n = len(valid)
    try:
        bmax = round(sum(float(r.get('be_max_x') or 0) for r in valid) / n, 3)
        ev   = round(sum(float(r.get('ev_strat') or 0) for r in valid) / n, 3)
        wr15 = round(100 * sum(1 for r in valid if float(r.get('be_max_x') or 0) >= 1.5) / n, 1)
        wr20 = round(100 * sum(1 for r in valid if float(r.get('be_max_x') or 0) >= 2.0) / n, 1)
        rug  = round(100 * sum(1 for r in valid if float(r.get('ev_strat') or 1) < 0.7) / n, 1)
        return dict(n=n, bmax=bmax, ev=ev, wr15=wr15, wr20=wr20, rug=rug)
    except Exception:
        return dict(n=n, bmax=0, ev=0, wr15=0, wr20=0, rug=0)


# ── Table rows ────────────────────────────────────────────────────────────────
def _pending_rows(pending):
    if not pending:
        return '<tr><td colspan=11 class="empty">Aguardando sinais...</td></tr>'
    rows = ''
    for ca, p in sorted(pending.items(), key=lambda x: x[1].get('entry_ts', 0), reverse=True):
        sym      = p.get('symbol', ca[:8])
        link     = f'<a href="https://gmgn.ai/sol/token/{ca}" target="_blank">{ca[:8]}</a>'
        mc_entry = float(p.get('mc_entry') or 0)
        mc_high  = float(p.get('mc_high') or mc_entry)
        high_x   = round(mc_high / mc_entry, 2) if mc_entry > 0 else 1.0
        score    = int(float(p.get('priority_score') or 0))
        dev      = float(p.get('dev_score') or 0)
        chg5     = float(p.get('chg_5m') or 0)
        chg1h    = float(p.get('chg_1h') or 0)
        buy_r    = float(p.get('buy_ratio') or 0)
        smart    = int(float(p.get('smart_degen_count') or 0))

        rows += f'''<tr>
<td><b>{sym}</b> <span class="ca">{link}</span></td>
<td>{_mc(mc_entry)}</td>
<td class="dim">{_age(p.get("entry_ts", 0))}</td>
<td style="color:{_score_col(score)}">{score}</td>
<td style="color:#8b9eb0">{dev:.2f}</td>
<td style="color:{_chg_col(chg5)}">{chg5*100:+.1f}%</td>
<td style="color:{_chg_col(chg1h)}">{chg1h*100:+.1f}%</td>
<td style="color:{'#57d97b' if buy_r>=0.55 else '#f0b429'}">{buy_r*100:.0f}%</td>
<td style="color:#58a6ff">{smart}</td>
<td>{_bundler_badge(p.get("bundler_rate"))}</td>
<td style="color:{_xcol(high_x)}">{high_x:.2f}x</td>
</tr>'''
    return rows


def _outcome_rows(rows):
    valid = [r for r in rows if r.get('be_max_x') not in ('', None, '0')]
    if not valid:
        return '<tr><td colspan=9 class="empty">Acumulando outcomes...</td></tr>'
    result = ''
    for r in reversed(valid[-40:]):
        bx       = float(r.get('be_max_x') or 0)
        ev       = float(r.get('ev_strat') or 0)
        hold_min = r.get('hold_min', '-')
        score    = int(float(r.get('priority_score') or 0))
        chg5     = float(r.get('chg_5m') or 0)
        ca       = r.get('ca', '')
        lnk      = f'<a href="https://gmgn.ai/sol/token/{ca}" target="_blank">{ca[:8]}</a>' if ca else ''
        ts       = (r.get('ts') or '')
        try:
            ts = datetime.datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S') \
                     .replace(tzinfo=datetime.timezone.utc).astimezone(_BRT).strftime('%m-%d %H:%M')
        except: pass

        bx_badge = f'<span style="color:{_xcol(bx)};font-weight:bold">{bx:.2f}x</span>'
        ev_c     = '#57d97b' if ev >= 1.0 else '#e05c5c'

        if ev >= 2.0:
            tag = '<span class="tag-tp">2x</span>'
        elif ev >= 1.5:
            tag = '<span class="tag-win">WIN</span>'
        elif ev >= 1.0:
            tag = '<span class="tag-win" style="opacity:.7">+EV</span>'
        elif ev < 0.7:
            tag = '<span class="tag-rug">RUG</span>'
        else:
            tag = '<span class="tag-loss">LOSS</span>'

        result += f'''<tr>
<td class="dim">{ts}</td>
<td><b>{r.get("symbol","")}</b> <span class="ca">{lnk}</span></td>
<td>{_mc(r.get("mc_entry"))}</td>
<td style="color:{_score_col(score)}">{score}</td>
<td style="color:{_chg_col(chg5)}">{chg5*100:+.1f}%</td>
<td>{bx_badge}</td>
<td class="dim">{hold_min}m</td>
<td style="color:{ev_c}">{ev:.3f}x</td>
<td>{tag}</td>
</tr>'''
    return result


# ── Kotte stats / rows ────────────────────────────────────────────────────────
def _tier_badge(alert_type, tier):
    colors = {'1': '#f0b429', '2': '#8b949e', '3': '#cd7f32'}
    icons  = {'CLUSTER': '🔥', 'KOL': '👀', 'DEV_LAUNCH': '🚀', 'SMART_WALLET': '👛'}
    icon   = icons.get(alert_type, '?')
    if alert_type == 'SMART_WALLET':
        c = colors.get(str(tier), '#8b949e')
        return f'<span style="color:{c};font-size:.85em">{icon} T{tier}</span>'
    c = {'CLUSTER': '#58a6ff', 'KOL': '#c084fc', 'DEV_LAUNCH': '#57d97b'}.get(alert_type, '#8b949e')
    return f'<span style="color:{c};font-size:.85em">{icon}</span>'


def _kotte_stats(outcomes, pending):
    done = [r for r in outcomes if r.get('x_1h') not in ('', None, '0')]
    n_done = len(done)
    if n_done == 0:
        return dict(n_pending=len(pending), n_done=0, avg_1h=0, wr2x=0)
    avg_1h = round(sum(float(r.get('x_1h') or 0) for r in done) / n_done, 3)
    wr2x   = round(100 * sum(1 for r in done if float(r.get('x_1h') or 0) >= 2.0) / n_done, 1)
    return dict(n_pending=len(pending), n_done=n_done, avg_1h=avg_1h, wr2x=wr2x)


def _kotte_pending_rows(pending):
    if not pending:
        return '<tr><td colspan=9 class="empty">Aguardando sinais...</td></tr>'
    rows = ''
    for ca, p in sorted(pending.items(), key=lambda x: x[1].get('call_ts', 0), reverse=True):
        sym    = p.get('symbol', ca[:8])
        link   = f'<a href="https://gmgn.ai/sol/token/{ca}" target="_blank">{ca[:8]}</a>'
        badge  = _tier_badge(p.get('alert_type', ''), p.get('tier', 0))
        snaps  = p.get('snaps', {})
        done_s = sum(1 for m in ['5', '15', '30', '60'] if snaps.get(m) is not None)
        prog   = f'{done_s}/4'
        rows += f'''<tr>
<td><b>{sym}</b> <span class="ca">{link}</span></td>
<td>{badge}</td>
<td class="dim">{_age(p.get("call_ts", 0))}</td>
<td>{_mc(p.get("mc_entry"))}</td>
<td class="dim">{p.get("age_min","-")}min</td>
<td class="dim">{p.get("wallet_id","")[:12]}</td>
<td style="color:#58a6ff">{p.get("w7d_x2plus","-")}</td>
<td style="color:#8b949e">{p.get("w7d_best_x","-")}x</td>
<td style="color:#484f58">{prog}</td>
</tr>'''
    return rows


def _kotte_outcome_rows(outcomes):
    done = [r for r in outcomes if r.get('x_1h') not in ('', None, '0')]
    if not done:
        return '<tr><td colspan=10 class="empty">Acumulando outcomes...</td></tr>'
    result = ''
    for r in reversed(done[-30:]):
        ca    = r.get('ca', '')
        link  = f'<a href="https://gmgn.ai/sol/token/{ca}" target="_blank">{ca[:8]}</a>' if ca else ''
        badge = _tier_badge(r.get('alert_type', ''), r.get('tier', 0))
        x1h   = float(r.get('x_1h') or 0)
        ts    = r.get('call_ts', '')
        try:
            ts = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc) \
                     .astimezone(_BRT).strftime('%m-%d %H:%M')
        except: pass

        x_badge = f'<span style="color:{_xcol(x1h)};font-weight:bold">{x1h:.2f}x</span>'
        if x1h >= 2.0:   tag = '<span class="tag-tp">2x</span>'
        elif x1h >= 1.5: tag = '<span class="tag-win">WIN</span>'
        elif x1h >= 1.0: tag = '<span class="tag-win" style="opacity:.7">+EV</span>'
        elif x1h < 0.7:  tag = '<span class="tag-rug">RUG</span>'
        else:             tag = '<span class="tag-loss">LOSS</span>'

        result += f'''<tr>
<td class="dim">{ts}</td>
<td><b>{r.get("symbol","")}</b> <span class="ca">{link}</span></td>
<td>{badge}</td>
<td>{_mc(r.get("mc_entry"))}</td>
<td class="dim">{r.get("age_min","-")}m</td>
<td style="color:#8b9eb0">{r.get("w7d_x2plus","-")}</td>
<td style="color:{_xcol(float(r.get("x_5m") or 0))}">{float(r.get("x_5m") or 0):.2f}x</td>
<td style="color:{_xcol(float(r.get("x_15m") or 0))}">{float(r.get("x_15m") or 0):.2f}x</td>
<td style="color:{_xcol(float(r.get("x_30m") or 0))}">{float(r.get("x_30m") or 0):.2f}x</td>
<td>{x_badge} {tag}</td>
</tr>'''
    return result


# ── HTML builder ──────────────────────────────────────────────────────────────
CSS = '''
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
* { box-sizing: border-box; margin: 0; padding: 0 }
body {
  background: #0d1117; color: #c9d1d9;
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  font-size: 15px; line-height: 1.5; padding: 28px 36px;
}
a { color: #58a6ff; text-decoration: none }
a:hover { text-decoration: underline }
.topbar {
  display: flex; align-items: center; gap: 16px;
  margin-bottom: 32px; border-bottom: 1px solid #21262d; padding-bottom: 16px;
}
.topbar h1 { font-size: 1.15em; color: #f0f6fc; font-weight: 700 }
.topbar .ts { font-size: .82em; color: #484f58; margin-left: auto }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#3fb950;
       margin-right:10px; animation:pulse 2s infinite; box-shadow:0 0 6px #3fb95066 }
.dot-off { background:#484f58; animation:none; box-shadow:none }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
.section { margin-bottom: 48px }
.section-title {
  font-size:.8em; font-weight:700; letter-spacing:1.5px; text-transform:uppercase;
  color:#8b949e; margin-bottom:18px; padding-bottom:10px; border-bottom:1px solid #21262d;
}
.section-title span { color: #58a6ff }
.cfg { font-size:.82em; color:#6e7681; margin-bottom:20px }
.cfg b { color: #8b949e }
.live-on  { color:#57d97b; font-weight:700 }
.live-off { color:#484f58; font-weight:700 }
.cards { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:28px }
.card { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:16px 22px; min-width:110px }
.card.primary { border-color:#388bfd44 }
.card .val { font-size:1.5em; font-weight:700; color:#f0f6fc }
.card .lbl { font-size:.76em; color:#6e7681; margin-top:4px; text-transform:uppercase; letter-spacing:.8px }
.block-title { font-size:.8em; color:#8b949e; font-weight:600; margin-bottom:10px; margin-top:24px }
.tbl-wrap { overflow-x: auto }
table { width:100%; border-collapse:collapse; font-size:.88em }
th { text-align:left; padding:8px 12px; color:#8b949e; font-weight:600; font-size:.8em;
     border-bottom:1px solid #21262d; white-space:nowrap }
td { padding:9px 12px; border-bottom:1px solid #161b22; white-space:nowrap }
tr:hover td { background:#161b22 }
.dim { color:#484f58 }
.ca { font-size:.8em; color:#484f58; margin-left:6px }
.empty { color:#484f58; padding:18px 14px; font-style:italic }
.tag-tp   { background:#1f4a2e; color:#57d97b; border-radius:4px; padding:2px 7px; font-size:.8em; font-weight:700 }
.tag-win  { background:#2a3a1e; color:#7ee787; border-radius:4px; padding:2px 7px; font-size:.8em }
.tag-loss { background:#2d1e1e; color:#e05c5c; border-radius:4px; padding:2px 7px; font-size:.8em }
.tag-rug  { background:#3d1212; color:#ff6b6b; border-radius:4px; padding:2px 7px; font-size:.8em; font-weight:700 }
'''


def _build_html():
    now  = datetime.datetime.now(tz=_BRT).strftime('%Y-%m-%d %H:%M:%S BRT')
    cfg  = _scanner_cfg()
    sout = _read_csv(SIGNAL_CSV)
    spnd = _read_json(SIGNAL_PENDING)
    ss   = _sig_stats(sout)
    kout = _read_csv(KOTTE_CSV)
    kpnd = _read_json(KOTTE_PENDING)
    ks   = _kotte_stats(kout, kpnd)

    bmax_col   = '#6ab4ff' if ss['bmax'] >= 1.1 else ('#f0b429' if ss['bmax'] >= 1.0 else '#e05c5c')
    live_label = '<span class="live-on">LIVE</span>' if cfg['LIVE'] else '<span class="live-off">SHADOW</span>'
    dot_class  = 'dot' if cfg['LIVE'] else 'dot dot-off'

    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signal Dashboard</title>
<style>{CSS}</style>
<script>
window.addEventListener('load', function() {{
  var y = sessionStorage.getItem('sy');
  if (y) window.scrollTo(0, parseInt(y));
  setTimeout(function() {{
    sessionStorage.setItem('sy', window.scrollY);
    location.reload();
  }}, {REFRESH_S * 1000});
}});
</script>
</head>
<body>
<div class="topbar">
  <h1><span class="{dot_class}"></span>Signal Dashboard</h1>
  <span class="ts">{now} · refresh {REFRESH_S}s</span>
</div>
<div class="section">
  <div class="section-title"><span>Signal Scanner</span> — aibot / market_trending</div>
  <div class="cfg">
    mode: {live_label} &nbsp;·&nbsp;
    max_hold: <b>{int(cfg["MAX_HOLD_MIN"])}min</b> &nbsp;·&nbsp;
    priority_min: <b>{int(cfg["PRIORITY_MIN"])}</b>
  </div>
  <div class="cards">
    <div class="card"><div class="val">{ss["n"]}</div><div class="lbl">Outcomes</div></div>
    <div class="card primary"><div class="val" style="color:{bmax_col}">{ss["bmax"]}x</div><div class="lbl">be_max avg</div></div>
    <div class="card"><div class="val">{ss["wr15"]}%</div><div class="lbl">WR 1.5x</div></div>
    <div class="card"><div class="val">{ss["wr20"]}%</div><div class="lbl">WR 2.0x</div></div>
    <div class="card"><div class="val" style="color:{'#57d97b' if ss['ev']>=1 else '#e05c5c'}">{ss["ev"]}x</div><div class="lbl">EV exit</div></div>
    <div class="card"><div class="val" style="color:{'#e05c5c' if ss['rug']>5 else '#57d97b'}">{ss["rug"]}%</div><div class="lbl">Rug%</div></div>
    <div class="card"><div class="val">{len(spnd)}</div><div class="lbl">Pending</div></div>
  </div>
  <div class="block-title">Em tracking — {len(spnd)} token(s)</div>
  <div class="tbl-wrap"><table>
    <tr><th>Symbol</th><th>MC</th><th>Idade</th><th>Score</th><th>Dev</th><th>chg5m</th><th>chg1h</th><th>Buy%</th><th>Smart</th><th>Bundler</th><th>High</th></tr>
    {_pending_rows(spnd)}
  </table></div>
  <div class="block-title">Outcomes recentes — últimos {min(ss["n"], 40)}</div>
  <div class="tbl-wrap"><table>
    <tr><th>Hora</th><th>Symbol</th><th>MC</th><th>Score</th><th>chg5m</th><th>be_max</th><th>Hold</th><th>EV</th><th>Result</th></tr>
    {_outcome_rows(sout)}
  </table></div>
</div>

<div class="section">
  <div class="section-title"><span>Kotte Monitor</span> — @kotte_memescan_bot</div>
  <div class="cards">
    <div class="card"><div class="val">{ks["n_pending"]}</div><div class="lbl">Tracking</div></div>
    <div class="card"><div class="val">{ks["n_done"]}</div><div class="lbl">Outcomes</div></div>
    <div class="card primary"><div class="val" style="color:{_xcol(ks["avg_1h"])}">{ks["avg_1h"]}x</div><div class="lbl">avg 1h</div></div>
    <div class="card"><div class="val">{ks["wr2x"]}%</div><div class="lbl">WR 2x @1h</div></div>
  </div>
  <div class="block-title">Em tracking — {ks["n_pending"]} token(s)</div>
  <div class="tbl-wrap"><table>
    <tr><th>Symbol</th><th>Tipo</th><th>Idade</th><th>MC</th><th>Age Lançto</th><th>Wallet</th><th>7d x2+</th><th>7d best</th><th>Snaps</th></tr>
    {_kotte_pending_rows(kpnd)}
  </table></div>
  <div class="block-title">Outcomes recentes — últimos {min(ks["n_done"], 30)}</div>
  <div class="tbl-wrap"><table>
    <tr><th>Hora</th><th>Symbol</th><th>Tipo</th><th>MC</th><th>Age</th><th>7d x2+</th><th>5m</th><th>15m</th><th>30m</th><th>1h</th></tr>
    {_kotte_outcome_rows(kout)}
  </table></div>
</div>
</body></html>'''


# ── Cache refresher ───────────────────────────────────────────────────────────
def _refresh_loop():
    while True:
        try:
            html = _build_html().encode('utf-8')
            with _cache_lock:
                _cache['html'] = html
                _cache['ts']   = time.time()
        except Exception as e:
            print(f'[dashboard] refresh error: {e}')
        time.sleep(REFRESH_S)


# ── HTTP server ───────────────────────────────────────────────────────────────
class _ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with _cache_lock:
            body = _cache['html']
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def address_string(self):
        return self.client_address[0]

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    port = int(os.environ.get('DASHBOARD_PORT', 8080))
    try:
        html = _build_html().encode('utf-8')
        with _cache_lock:
            _cache['html'] = html
    except Exception as e:
        print(f'[dashboard] initial build error: {e}')

    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()

    srv = _ThreadingServer(('0.0.0.0', port), _Handler)
    print(f'[dashboard] http://34.23.5.231:{port}  (refresh={REFRESH_S}s)')
    srv.serve_forever()
