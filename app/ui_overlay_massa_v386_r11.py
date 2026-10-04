from history_expiry_r12 import expiry_label
from datetime import datetime, timedelta
from pathlib import Path
import time

from broker_day_r11 import SAO_PAULO, today as broker_today
from broker_results_r11 import ensure_schema, scoreboard, bullex_daily_scoreboard
from database import connect
from voice_announcer_r11 import announce as voice_announce
from paths_r11 import LOG_DIR


class BullexOverlay:
    BUILD_LABEL = 'V3.86 · R12'

    @staticmethod
    def _format_history_time(value):
        if not value:
            return '—'
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(SAO_PAULO)
            return parsed.strftime('%d/%m/%Y - %H:%M:%S')
        except (TypeError, ValueError, OverflowError):
            return str(value)

    @staticmethod
    def inject_shell(driver, version=None):
        return True

    @staticmethod
    def set_diag(driver, text, state=None):
        return None

    def __init__(self, driver, cfg, analyzer=None):
        self.driver = driver
        self.cfg = cfg
        self.analyzer = analyzer
        self.db = connect()
        ensure_schema(self.db)
        self.asset = str(cfg.get('asset_id', '76'))
        self.asset_label = str(cfg.get('_asset_label') or 'CARREGANDO') if analyzer else 'SINCRONIZANDO ATIVO'
        self.last_push = 0.0
        self._history_cache_key = None
        self._history_cache_at = 0.0
        self._history_cache = []

    def set_asset(self, asset_id, label=None):
        self.asset = str(asset_id or '')
        self.asset_label = str(label or 'NÃO IDENTIFICADO')

    def _visual_countdown_seconds(self):
        try:
            row = self.db.execute(
                'SELECT current_to,broker_at,last_received_at FROM bullex_live_state WHERE asset_id=?',
                (self.asset,)
            ).fetchone()
            if row and row[0] and row[1] and row[2]:
                received_at=datetime.fromisoformat(str(row[2])).timestamp()
                age=max(0.0,time.time()-received_at)
                # Extrapolate in the broker's time domain. Do not subtract the PC
                # wall clock from broker timestamps: their clocks can differ.
                if age <= 8.0:
                    remaining=float(row[0])-(float(row[1])+age)
                    if -0.5 <= remaining <= 60.5:
                        return min(59.0,max(0.0,remaining))
        except Exception:
            pass
        # Keep the visual M1 clock moving even during a short/missing feed sample;
        # the feed watchdog still reports and blocks on a frozen broker stream.
        local_second=time.localtime().tm_sec
        return float(min(59,max(1,60-local_second)))

    @staticmethod
    def _separate_log_events(lines):
        """Add a visual blank line when the runtime event category changes."""
        import json
        import re
        output=[]
        previous=None
        for line in lines:
            text=str(line).rstrip()
            if not text:
                if output and output[-1] != '': output.append('')
                continue
            stripped=text.strip()
            group=previous
            if stripped.startswith('{'):
                try:
                    event=json.loads(stripped).get('event')
                    if event: group='JSON:'+str(event)
                except (TypeError,ValueError):
                    pass
            elif stripped.startswith('Traceback ') or stripped.startswith('Exception occurred during processing'):
                group='TRACEBACK'
            elif stripped.startswith('---') and set(stripped)=={'-'}:
                group='SEPARATOR'
            elif not text[:1].isspace():
                tags=re.findall(r'\[([^\]]+)\]',stripped)
                if tags:
                    group='TAG:'+tags[-1]
                elif stripped.startswith('=== RAPOSO BOT '):
                    group='SESSION'
                elif stripped.startswith('['):
                    group='EVENT'
            if group is not None and previous is not None and group != previous and output and output[-1] != '':
                output.append('')
            output.append(text)
            if group is not None:
                previous=group
        return output

    def _log_tail(self, n=250):
        candidates = [
            LOG_DIR/'bullex_r12_runtime.log',
            LOG_DIR/'bullex_r12_runtime_local.log',
        ]
        existing = []
        for path in candidates:
            try:
                if path.exists():
                    existing.append((path.stat().st_mtime, path))
            except Exception:
                pass
        for _, path in sorted(existing, reverse=True):
            try:
                # O log compartilhado pode ter dezenas de MB. Ler o arquivo inteiro
                # a cada atualização prendia o laço que confirma PAUSE/RESUME.
                with path.open('rb') as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size-262144))
                    raw = handle.read()
                lines = [line.rstrip() for line in raw.decode('utf-8', errors='replace').splitlines() if line.strip()]
                if lines:
                    return self._separate_log_events(lines[-int(n):])
            except Exception:
                pass
        return [f'[{self.BUILD_LABEL}] aguardando eventos do runtime...']

    def _history_request(self):
        default = {'range': 'today', 'start': '', 'end': '', 'view': 'overview', 'mode': 'classic'}
        try:
            value = self.driver.execute_script(r"""
                const root=document.getElementById('bullex-pro-overlay');
                if(!root)return null;
                return {range:root.dataset.historyRange||'today',start:root.dataset.historyStart||'',end:root.dataset.historyEnd||'',view:root.dataset.dashboardView||'overview',mode:root.dataset.uiMode||'classic'};
            """)
            if isinstance(value, dict):
                default.update(value)
        except Exception:
            pass
        return default

    @staticmethod
    def _history_bounds(kind, custom_start='', custom_end=''):
        now = datetime.now(SAO_PAULO)
        day = now.date()
        start = end = None
        if kind == 'today':
            start, end = day, day
        elif kind == 'yesterday':
            start = end = day-timedelta(days=1)
        elif kind in ('7d', '14d', '28d', '30d'):
            days = int(kind[:-1])
            start, end = day-timedelta(days=days-1), day
        elif kind == 'this_week':
            start, end = day-timedelta(days=day.weekday()), day
        elif kind == 'last_week':
            end = day-timedelta(days=day.weekday()+1)
            start = end-timedelta(days=6)
        elif kind == 'this_month':
            start, end = day.replace(day=1), day
        elif kind == 'last_month':
            end = day.replace(day=1)-timedelta(days=1)
            start = end.replace(day=1)
        elif kind == 'this_quarter':
            month = ((day.month-1)//3)*3+1
            start, end = day.replace(month=month, day=1), day
        elif kind == 'custom':
            try:
                start = datetime.fromisoformat(str(custom_start)).date()
                end = datetime.fromisoformat(str(custom_end)).date()
                if end < start:
                    start, end = end, start
            except (TypeError, ValueError):
                start, end = day, day
        return (start.isoformat() if start else None, end.isoformat() if end else None)

    def _history_rows(self, request):
        key = (str(request.get('range') or 'today'), str(request.get('start') or ''), str(request.get('end') or ''), str(request.get('mode') or 'closed'))
        if key == self._history_cache_key and time.monotonic()-self._history_cache_at < 1.5:
            return self._history_cache
        start, end = self._history_bounds(*key[:3])
        mode = key[3]
        if mode == 'confirmed_pending':
            where = ["o.status='EXECUTED' AND b.broker_id IS NOT NULL AND r.demo_order_id IS NULL"]
        elif mode == 'pending':
            where = ["o.status='SUBMITTED'"]
        else:
            where = ["o.status='EXECUTED' AND r.result IN ('WIN','LOSS','DRAW','WIN_GALE','LOSS_GALE')"]
        params = []
        stamp = "substr(COALESCE(r.closed_at,o.executed_at,o.requested_at),1,10)"
        if start:
            where.append(f'{stamp}>=?')
            params.append(start)
        if end:
            where.append(f'{stamp}<=?')
            params.append(end)
        has_scores = bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_signals'").fetchone())
        setup_sql = "COALESCE(d.c1_dir,o.leg)"
        if has_scores:
            setup_sql = """CASE WHEN d.decision_mode='STRATEGY' THEN COALESCE(d.c1_dir,o.leg)
                ELSE COALESCE((SELECT s.setup FROM score_signals s
                    WHERE s.asset_id=o.asset_id AND s.signal_ts IN (o.cycle_start,o.cycle_start-60)
                    ORDER BY s.id DESC LIMIT 1),d.c1_dir,o.leg) END"""
        query = f"""SELECT o.id,COALESCE(r.broker_id,b.broker_id,'LOCAL #'||o.id),
            COALESCE(r.closed_at,o.executed_at,o.requested_at),COALESCE(o.asset_name,'ATIVO NÃO VINCULADO'),
            {setup_sql},o.direction,o.direction_original,o.direction_executed,o.candle_flow_inverted,
            o.stake,o.candle_exec,o.build_revision,o.status,
            r.result,r.pnl_minor,r.stake_minor,r.currency,o.error,o.requested_at,o.executed_at,r.evidence
          FROM demo_orders o
          JOIN decisions d ON d.id=o.decision_id
          LEFT JOIN broker_confirmed_results r ON r.demo_order_id=o.id
          LEFT JOIN broker_order_links b ON b.demo_order_id=o.id
          WHERE {' AND '.join(where)} ORDER BY o.id DESC"""
        rows = []
        for oid, position, at, asset, setup, direction, direction_original, direction_executed, flow_inverted, stake, candle, revision, status, result, pnl, stake_minor, currency, error, requested_at, executed_at, evidence in self.db.execute(query, params).fetchall():
            rows.append({
                'id': int(oid), 'position': str(position), 'time': self._format_history_time(at),
                'asset': str(asset or 'ATIVO NÃO VINCULADO').replace('ATIVO: ', '').replace('ATIVO ', ''),
                'setup': str(setup or '—'),
                'side': 'CALL' if (direction_executed or direction) == 'G' else 'PUT' if (direction_executed or direction) == 'R' else '—',
                'direction_original': 'CALL' if direction_original == 'G' else 'PUT' if direction_original == 'R' else '—',
                'direction_executed': 'CALL' if (direction_executed or direction) == 'G' else 'PUT' if (direction_executed or direction) == 'R' else '—',
                'candle_flow_inverted': '1' if int(flow_inverted or 0) == 1 else '0',
                'stake': f'R$ {float(stake or 0):.2f}',
                'candle_exec': f'C{int(candle)}' if candle and 1 <= int(candle) <= 5 else '—',
                'revision': str(revision or '—'), 'status': str(status or '—'),
                'result': str(result or ('PENDENTE' if status in ('SUBMITTED', 'EXECUTED') else '—')),
                'pnl': '—' if pnl is None else f'{currency or ""} {float(pnl)/100:+.2f}',
                'pnl_minor': pnl, 'stake_minor': stake_minor, 'currency': currency,
                'requested_at': self._format_history_time(requested_at),
                'executed_at': self._format_history_time(executed_at),
                'info': str(error or '—'),
                'expiry': expiry_label(evidence),
            })
        self._history_cache_key = key
        self._history_cache_at = time.monotonic()
        self._history_cache = rows
        return rows

    def _snapshot(self):
        try:
            state = self.db.execute('SELECT current_from FROM bullex_live_state WHERE asset_id=?', (self.asset,)).fetchone()
            current_from = int(state[0]) if state and state[0] else 0
        except Exception:
            current_from = 0
        try:
            price_row = self.db.execute('SELECT close FROM candles WHERE asset_id=? AND timeframe=60 ORDER BY timestamp DESC LIMIT 1', (self.asset,)).fetchone()
            price = price_row[0] if price_row else None
        except Exception:
            price = None
        real = scoreboard(self.db, broker_today())
        money = real.get('money', {})
        daily_text = ' · '.join(f'{currency} {minor/100:+.2f}' for currency, minor in sorted(money.items())) or '0.00'
        daily_pl = next(iter(money.values()))/100 if len(money) == 1 else 0.0 if not money else None
        score = getattr(self.analyzer, 'last_score', None) if self.analyzer else None
        activated=str(self.cfg.get('risk_return',{}).get('activated_at',''))[:19]
        pending_order=self.db.execute("""SELECT o.id FROM demo_orders o WHERE o.status IN ('SUBMITTED','EXECUTED')
          AND o.requested_at>=? AND NOT EXISTS(SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id)
          ORDER BY o.id DESC LIMIT 1""",(activated,)).fetchone()
        if pending_order:
            score={'setup':'AGUARDANDO RESULTADO','side':'—','score':None,
                   'parts_text':f'Ordem {pending_order[0]} pendente de confirmacao da corretora; novas entradas bloqueadas'}
        # Voz de setup: anuncia transições; cooldown evita repetição.
        try:
            if not score or not score.get('setup'):
                voice_announce('setup_waiting')
            elif not score.get('side') or score.get('score') is None:
                voice_announce('setup_analyzing')
            else:
                voice_announce('setup_favorable', str(score.get('setup')))
        except Exception:
            pass
        request = self._history_request()
        dashboard_open = request.get('mode') == 'dashboard'
        overview_open = dashboard_open and request.get('view') == 'overview'
        history = self._history_rows(request) if dashboard_open else []
        setup_stats = []
        if overview_open:
            try:
                setup_stats = [{'setup': str(name or 'SEM SETUP'), 'signals': int(total or 0), 'wins': int(wins or 0),
                                'losses': int(losses or 0), 'rate': round(100*int(wins or 0)/(int(wins or 0)+int(losses or 0)), 1)
                               if int(wins or 0)+int(losses or 0) else 0.0,
                                'roi': round(100*int(pnl_minor or 0)/int(stake_minor or 0), 2)
                               if int(stake_minor or 0) > 0 and int(currencies or 0) <= 1 else None}
                    for name, total, wins, losses, pnl_minor, stake_minor, currencies in self.db.execute("""
                        SELECT COALESCE(d.c1_dir,'SEM SETUP'),COUNT(*),
                          SUM(CASE WHEN r.result IN ('WIN','WIN_GALE') THEN 1 ELSE 0 END),
                          SUM(CASE WHEN r.result IN ('LOSS','LOSS_GALE') THEN 1 ELSE 0 END),
                          COALESCE(SUM(r.pnl_minor),0), COALESCE(SUM(r.stake_minor),0),
                          COUNT(DISTINCT r.currency)
                        FROM decisions d JOIN demo_orders o ON o.decision_id=d.id AND o.status='EXECUTED'
                        JOIN broker_confirmed_results r ON r.demo_order_id=o.id
                        WHERE substr(r.closed_at,1,10)=? GROUP BY COALESCE(d.c1_dir,'SEM SETUP')
                        ORDER BY COUNT(*) DESC""", (broker_today(),)).fetchall()]
            except Exception:
                pass
        adaptive_rankings = []
        adaptive = getattr(self.analyzer, 'adaptive', None) if self.analyzer else None
        if adaptive and overview_open:
            try:
                names = [row[0] for row in self.db.execute('SELECT DISTINCT setup FROM adaptive_position_decisions ORDER BY setup').fetchall()]
                for name in names:
                    for item in adaptive.rankings(name):
                        adaptive_rankings.append({'setup': str(name), 'position': int(item['position']), 'samples': int(item['samples']),
                                                  'wins': int(item['wins']), 'losses': int(item['losses']), 'wr': round(100*float(item['wr']), 1)})
            except Exception:
                pass
        notifications = []
        try:
            notifications = [{'id': eid, 'order_id': oid, 'side': 'CALL' if direction == 'G' else 'PUT', 'at': at}
                for eid, oid, direction, at in self.db.execute("""SELECT e.id,o.id,o.direction,e.created_at
                    FROM raposo_result_events e JOIN demo_orders o ON o.id=e.demo_order_id
                    WHERE e.kind='EXECUTED' ORDER BY e.id DESC LIMIT 20""").fetchall()[::-1]]
        except Exception:
            pass
        paused = bool(getattr(self.analyzer, 'paused', self.cfg.get('_user_paused', True)))
        executor = getattr(self.analyzer, 'executor', None) if self.analyzer else getattr(self, 'executor', None)
        controller = getattr(executor, 'loss_controller', None)
        try:
            loss_state = controller.snapshot() if controller else {'current':'—','stopped':False,'unconfigured':True,'shifts':[]}
        except Exception:
            loss_state = {'current':'—','stopped':False,'unconfigured':True,'shifts':[]}
        asset_text = str(self.asset_label or '').upper()
        cycle_index = int((current_from % 300)//60)+1 if current_from else 0
        return {
            'build': self.BUILD_LABEL, 'paused': paused,
            'feed_m1_frozen': bool(self.cfg.get('_feed_m1_frozen', False)),
            'armed': bool(getattr(executor, 'armed', False)),
            'loss_current': loss_state.get('current', '—'),
            'loss_stop': bool(loss_state.get('stopped', False)),
            'loss_unconfigured': bool(loss_state.get('unconfigured', False)),
            'loss_shifts': loss_state.get('shifts', []),
            'voice_enabled': bool((self.cfg.get('voice_alerts') or {}).get('enabled', False)),
            'asset_verified': asset_text not in ('', 'NÃO IDENTIFICADO', 'NAO IDENTIFICADO', 'CARREGANDO', 'SINCRONIZANDO ATIVO') and not asset_text.startswith('ATIVO WS '),
            'asset_label': 'ATIVO: '+str(self.asset_label or 'CARREGANDO').replace('ATIVO: ', '').replace('ATIVO ', ''),
            'price': price, 'remaining': self._visual_countdown_seconds(), 'cycle_index': cycle_index,
            'wins': int(real.get('WIN', 0))+int(real.get('WIN_GALE', 0)),
            'losses': int(real.get('LOSS', 0))+int(real.get('LOSS_GALE', 0)),
            'executed': int(real.get('total', 0))+int(real.get('pending', 0)),
            'pending': int(real.get('pending', 0)), 'pl': daily_text, 'plnum': daily_pl,
            'setup': (score or {}).get('setup', 'AGUARDANDO SETUP'),
            'score': '—' if (score or {}).get('score') is None else f"{float(score['score']):.1f}",
            'side': (score or {}).get('side', '—'), 'parts': (score or {}).get('parts_text', 'sem sinal'),
            'stake': f"R$ {float(self.cfg.get('demo_stake', 10)):.2f}", 'expiry': '1 MIN',
            'dashboard_url': str(self.cfg.get('_dashboard_url') or ''),
            'control_url': str(self.cfg.get('_control_url') or ''),
            'control_pin': str(self.cfg.get('_control_pin') or ''),
            'log_url': str(self.cfg.get('_log_url') or ''),
            'logs': self._log_tail() if request.get('mode') == 'log' else [],
            'history': history, 'history_range': request.get('range', 'today'),
            'setups': setup_stats, 'adaptive': adaptive_rankings, 'notifications': notifications,
        }

    def update(self, force=False):
        if not force and time.time()-self.last_push < 0.45:
            return
        self.last_push = time.time()
        try:
            payload = self._snapshot()
        except Exception as exc:
            print('[V3.86][OVERLAY] snapshot:', repr(exc), flush=True)
            payload = {'build': self.BUILD_LABEL, 'paused': True, 'armed': False, 'feed_m1_frozen': False, 'asset_verified': False,
                       'loss_current': '—', 'loss_stop': False, 'loss_unconfigured': True, 'loss_shifts': [],
                       'asset_label': 'ATIVO: CARREGANDO', 'price': None, 'remaining': None, 'cycle_index': 0,
                       'wins': 0, 'losses': 0, 'executed': 0, 'pending': 0, 'pl': '0.00', 'plnum': 0,
                       'setup': 'AGUARDANDO SETUP', 'score': '—', 'side': '—', 'parts': 'sem sinal',
                       'stake': 'R$ 10.00', 'expiry': '1 MIN', 'logs': self._log_tail(), 'history': [],
                       'history_range': 'today', 'setups': [], 'adaptive': [], 'notifications': []}
        js = r"""
        const p=arguments[0], W=340;
        let root=document.getElementById('bullex-pro-overlay');
        const esc=v=>String(v??'—');
        const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=esc(v)};
        if(!root){
          root=document.createElement('aside');root.id='bullex-pro-overlay';root.dataset.uiMode='classic';root.dataset.historyRange='today';root.dataset.dashboardView='overview';
          root.innerHTML=`<header id="bxh"><div class="bxidentity"><strong>RAPOSO</strong><span id="bxver"></span></div><nav id="bxmodes"><button data-mode="classic">CLÁSSICO</button><button data-mode="log">LOG</button><button data-mode="dashboard">DASHBOARD</button></nav><div class="bxactions"><button id="bxpause">RETOMAR</button><button id="bxstop">ENCERRAR</button><button id="bxrestart">REINICIAR</button><button id="bxvoice">VOZ: OFF</button></div></header>
          <main id="bxclassic"><div class="bxstate"><b id="bxstate">PAUSADO</b><span id="bxasset">ATIVO: CARREGANDO</span></div><div class="bxtiming"><span>M1</span><span id="bxexpiry">1 MIN</span><b id="bxremaining">--:--</b></div><section class="bxsignal"><div><b id="bxsetup">AGUARDANDO SETUP</b><b id="bxscore">SCORE —</b></div><strong id="bxside">—</strong><small id="bxparts">sem sinal</small></section><div class="bxresults"><div><span>WIN</span><b id="bxwins">0</b></div><div><span>LOSS</span><b id="bxlosses">0</b></div><div><span>PEND.</span><b id="bxpending">0</b></div></div><section id="bxlosscontrol"><strong>STOP LOSS POR TURNO</strong><div id="bxshiftrows"></div></section><div class="bxprofit"><span>Resultado do dia</span><b id="bxpl">0.00</b></div><div class="bxmeta"><span>Entrada <b id="bxstake">R$ 10.00</b></span><span>Candle <b id="bxcycle">—/5</b></span></div></main>
          <section id="bxlogview" class="bxworkspace"><header><div><strong>LOG DO RAPOSO</strong><small>Runtime em tempo real</small></div><button id="bxlogbottom">IR AO FIM</button></header><pre id="bxlogfull"></pre></section>
          <section id="bxdashboard" class="bxworkspace"><header><div><strong>DASHBOARD</strong><small>Banco real do Raposo</small></div><div id="bxdashtabs"><button data-view="overview">VISÃO GERAL</button><button data-view="history">HISTÓRICO</button></div></header><div id="bxoverview"><div class="bxkpis"><div><span>OPERAÇÕES</span><b id="bxkops">0</b></div><div><span>WIN</span><b id="bxkwin">0</b></div><div><span>LOSS</span><b id="bxkloss">0</b></div><div><span>PENDENTES</span><b id="bxkpend">0</b></div><div><span>WIN RATE</span><b id="bxkwr">0%</b></div><div><span>RESULTADO</span><b id="bxkpl">0.00</b></div></div><div class="bxgrid"><section><h3>SETUPS — HOJE</h3><table><thead><tr><th>Setup</th><th>Ops</th><th>WIN</th><th>LOSS</th><th>WR</th><th>ROI financeiro</th></tr></thead><tbody id="bxsetups"></tbody></table></section><section><h3>RANKING ADAPTATIVO</h3><table><thead><tr><th>Setup</th><th>Pos.</th><th>N</th><th>W/L</th><th>WR</th></tr></thead><tbody id="bxadaptive"></tbody></table></section></div><section><h3>ÚLTIMAS OPERAÇÕES REAIS</h3><div class="bxtable"><table><thead><tr><th>ID</th><th>Horário</th><th>Ativo</th><th>Setup</th><th>Direção</th><th>Candle Exec.</th><th>Versão/Revisão</th><th>Resultado</th><th>P/L</th></tr></thead><tbody id="bxrecent"></tbody></table></div></section></div>
          <div id="bxhistory"><div id="bxfilters"><button data-range="today">Hoje</button><button data-range="yesterday">Ontem</button><button data-range="7d">7 dias</button><button data-range="14d">14 dias</button><button data-range="28d">28 dias</button><button data-range="30d">30 dias</button><button data-range="this_week">Esta semana</button><button data-range="last_week">Semana passada</button><button data-range="this_month">Este mês</button><button data-range="last_month">Mês passado</button><button data-range="this_quarter">Este trimestre</button><button data-range="max">Máximo</button><button data-range="custom">Personalizado</button></div><div id="bxcustom"><label>De <input id="bxfrom" type="date"></label><label>Até <input id="bxto" type="date"></label></div><div class="bxtable"><table><thead><tr><th>ID</th><th>Horário</th><th>Ativo</th><th>Setup</th><th>Direção</th><th>Valor</th><th>Candle Exec.</th><th>Versão/Revisão</th><th>Status</th><th>Resultado</th><th>P/L</th><th>Expir.</th></tr></thead><tbody id="bxhistoryrows"></tbody></table></div></div></section>`;
          document.body.appendChild(root);
          const style=document.createElement('style');style.id='raposo-v386-style';style.textContent=`
          html{scroll-padding-left:${W}px!important}body{margin-left:${W}px!important;width:calc(100% - ${W}px)!important;max-width:calc(100% - ${W}px)!important;overflow-x:hidden!important}#bullex-pro-overlay{--accent:#9cff25;--rgb:156,255,37;position:fixed!important;left:0!important;top:0!important;width:${W}px!important;height:100vh!important;z-index:2147483646!important;background:radial-gradient(circle at 0 0,rgba(var(--rgb),.11),transparent 38%),#050b13!important;border-right:1px solid rgba(var(--rgb),.55)!important;color:#dce8f5!important;font:12px/1.4 Segoe UI,Arial,sans-serif!important;box-shadow:8px 0 24px #0008!important;user-select:none!important;overflow:hidden!important}#bullex-pro-overlay *{box-sizing:border-box!important;min-width:0!important;font-family:Segoe UI,Arial,sans-serif!important;line-height:1.4!important}#bullex-pro-overlay button{width:100%!important;min-width:0!important;height:30px!important;padding:0 5px!important;border:1px solid rgba(var(--rgb),.38)!important;border-radius:5px!important;background:#0b1522!important;color:#cbd8e7!important;font-weight:700!important;font-size:10px!important;cursor:pointer}#bullex-pro-overlay button:hover,#bullex-pro-overlay button.on{color:var(--accent)!important;border-color:var(--accent)!important;box-shadow:0 0 9px rgba(var(--rgb),.22)!important}#bxh{height:154px;padding:14px;border-bottom:1px solid rgba(var(--rgb),.25);background:linear-gradient(130deg,rgba(var(--rgb),.08),transparent)}.bxidentity{display:flex;justify-content:space-between;align-items:center}.bxidentity strong{font-size:20px!important;letter-spacing:2px;color:var(--accent);text-shadow:0 0 10px rgba(var(--rgb),.55)}#bxver{font-size:10px!important;border:1px solid rgba(var(--rgb),.38);padding:2px 6px;border-radius:4px;color:#bdcad9}#bxmodes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-top:12px}.bxactions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-top:8px}#bxpause{color:var(--accent)!important}#bxstop{color:#ff9eb0!important;border-color:#693545!important}#bxclassic{height:calc(100vh - 154px);padding:14px;overflow:auto}.bxstate{display:flex;flex-direction:column;gap:4px}.bxstate b{font-size:14px!important;color:#ffe66d}.bxstate span{font-size:12px!important;color:#c8d7e8;overflow-wrap:anywhere}.bxtiming{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin-top:12px;padding:10px 0;border-block:1px solid rgba(var(--rgb),.2);color:#91a5bb}.bxtiming span,.bxtiming b{font-size:12px!important}.bxtiming b{color:var(--accent)}.bxsignal{margin-top:12px;padding:12px;border:1px solid rgba(var(--rgb),.5);border-radius:7px;background:linear-gradient(120deg,rgba(var(--rgb),.09),#07101b)}.bxsignal>div{display:flex;justify-content:space-between;gap:8px}.bxsignal #bxsetup,.bxsignal #bxscore{font-size:12px!important}.bxsignal #bxsetup{color:var(--accent);overflow-wrap:anywhere}.bxsignal #bxscore{white-space:nowrap}.bxsignal strong,.bxsignal small{display:block;margin-top:7px;font-size:12px!important}.bxsignal small{color:#91a5bb}.bxresults{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:12px}.bxresults div{padding:9px;border:1px solid rgba(var(--rgb),.24);border-radius:5px;background:#09131f}.bxresults span{display:block;font-size:9px!important;color:#91a5bb}.bxresults b{font-size:18px!important}.bxprofit,.bxmeta{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid rgba(var(--rgb),.2)}.bxprofit span{font-size:12px!important}.bxprofit b{color:var(--accent);font-size:16px!important}.bxmeta,.bxmeta span,.bxmeta b{font-size:10px!important;color:#91a5bb}.bxworkspace{display:none;position:fixed!important;left:${W}px!important;top:0!important;width:calc(100vw - ${W}px)!important;height:100vh!important;padding:18px!important;background:radial-gradient(circle at 50% -20%,rgba(var(--rgb),.09),transparent 42%),#020914!important;overflow:auto!important;user-select:text!important}.bxworkspace>header{display:flex;align-items:center;justify-content:space-between;padding-bottom:14px;border-bottom:1px solid rgba(var(--rgb),.3)}.bxworkspace>header strong{display:block;font-size:24px!important;color:var(--accent)}.bxworkspace>header small{font-size:12px!important;color:#91a5bb}#bullex-pro-overlay[data-ui-mode=log] #bxlogview,#bullex-pro-overlay[data-ui-mode=dashboard] #bxdashboard{display:block}#bxlogfull{height:calc(100vh - 90px);margin:12px 0 0;padding:14px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid rgba(var(--rgb),.35);border-radius:7px;background:#040911;color:#bcd0e6;font:11px/1.55 Consolas,monospace!important}#bxdashtabs{display:flex;gap:7px}#bxdashtabs button{width:auto!important}.bxkpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin-top:14px;border:1px solid #21466b;border-radius:7px;overflow:hidden}.bxkpis div{padding:13px;text-align:center;background:#071526;border-right:1px solid #21466b}.bxkpis span{display:block;font-size:9px!important;color:#91a5bb}.bxkpis b{font-size:21px!important;color:var(--accent)}.bxgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}#bxdashboard section{margin-top:10px;padding:10px;border:1px solid #21466b;border-radius:7px;background:#061220}#bxdashboard h3{margin:0 0 7px;color:#dce8f5;font-size:12px!important}.bxtable{max-height:calc(100vh - 215px);overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:6px;border-bottom:1px solid #17304a;text-align:left;font-size:10px!important;white-space:nowrap}#bxdashboard th,#bxdashboard td{font-size:12px!important;padding:7px 8px!important}#bxdashboard #bxfilters button,#bxdashboard #bxdashtabs button{font-size:11px!important}th{position:sticky;top:0;background:#071526;color:#9db1c8}#bxhistory{display:none}#bxdashboard[data-view=history] #bxoverview{display:none}#bxdashboard[data-view=history] #bxhistory{display:block}#bxfilters{display:flex;flex-wrap:wrap;gap:5px;margin:14px 0 8px}#bxfilters button{width:auto!important}#bxcustom{display:none;gap:12px;margin-bottom:8px;color:#91a5bb}#bxcustom input{margin-left:5px;background:#071526;color:#dce8f5;border:1px solid #21466b;padding:5px}#bxdashboard[data-custom=true] #bxcustom{display:flex}@media(max-width:1000px){.bxkpis{grid-template-columns:repeat(3,minmax(0,1fr))}.bxgrid{grid-template-columns:1fr}}@media(max-height:700px){#bxh{height:126px!important;padding:10px 12px!important}.bxidentity strong{font-size:17px!important}#bxmodes{margin-top:8px!important}.bxactions{margin-top:6px!important}#bullex-pro-overlay button{height:27px!important;font-size:9px!important}#bxclassic{height:calc(100vh - 126px)!important;padding:10px 12px!important}.bxstate{gap:2px!important}.bxstate b{font-size:12px!important}.bxstate span{font-size:11px!important}.bxtiming{margin-top:8px!important;padding:7px 0!important}.bxsignal{margin-top:8px!important;padding:9px!important}.bxsignal strong,.bxsignal small{margin-top:4px!important}.bxresults{margin-top:8px!important;gap:5px!important}.bxresults div{padding:6px 8px!important}.bxresults b{font-size:16px!important}.bxprofit,.bxmeta{padding:8px 0!important}.bxworkspace{padding:12px!important}.bxworkspace>header{padding-bottom:9px!important}.bxworkspace>header strong{font-size:19px!important}#bxfilters{margin:9px 0 6px!important}.bxkpis{margin-top:9px!important}.bxkpis div{padding:8px!important}}
          #bxlosscontrol{margin-top:10px;padding:9px;border:1px solid rgba(var(--rgb),.28);border-radius:5px;background:#07111d}#bxlosscontrol>strong{font-size:10px!important;color:#91a5bb}#bxshiftrows{margin-top:5px}#bxshiftrows div{padding:2px 0;font-size:10px!important;color:#afc1d4}#bxshiftrows div.current{color:var(--accent);font-weight:700}#bxshiftrows div.stop{color:#ff6b8b}
          `;document.head.appendChild(style);window.dispatchEvent(new Event('resize'));
          const issue=command=>{if(root.__controlPending)return;const id=Date.now().toString(36)+'-'+(crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2));root.__controlPending={id,command,expected:command==='PAUSE',started:Date.now()};root.dataset.raposoCommand=command;root.dataset.raposoCommandId=id;document.documentElement.dataset.raposoCommand=command;document.documentElement.dataset.raposoCommandId=id;const b=document.getElementById('bxpause');b.disabled=true;b.textContent=command==='PAUSE'?'PAUSANDO...':'RETOMANDO...';
            if(root.dataset.controlUrl)fetch(root.dataset.controlUrl,{method:'POST',headers:{'Content-Type':'application/json','X-Bullex-Pin':root.dataset.controlPin||''},body:JSON.stringify({command,id})}).then(r=>r.json()).then(a=>{
              if(!a.ok||a.id!==id)return;
              window.__RAPOSO_PAUSE_REQUEST__=a.paused===true;
              root.dataset.raposoAckId=id;root.dataset.raposoAckPaused=String(a.paused===true);root.dataset.raposoAckError='';
              if(root.__controlPending&&root.__controlPending.id===id){root.__controlPending=null;b.disabled=false;b.textContent=a.paused?'RETOMAR':'PAUSAR';delete b.dataset.failedUntil;}
            }).catch(()=>{});
            setTimeout(()=>{const q=root.__controlPending;if(q&&q.id===id){root.__controlPending=null;b.disabled=false;b.textContent='FALHA - TENTE NOVAMENTE';b.dataset.failedUntil=String(Date.now()+1400)}},2500)};
          document.getElementById('bxpause').onclick=()=>issue(root.__lastPaused?'RESUME':'PAUSE');
          const stop=document.getElementById('bxstop');stop.onclick=()=>{if(stop.dataset.confirm==='1'){const id=Date.now().toString(36)+'-stop';root.dataset.raposoCommand='STOP';root.dataset.raposoCommandId=id;document.documentElement.dataset.raposoCommand='STOP';document.documentElement.dataset.raposoCommandId=id;stop.textContent='ENCERRANDO...';stop.disabled=true}else{stop.dataset.confirm='1';stop.textContent='CONFIRMAR';setTimeout(()=>{if(!stop.disabled){delete stop.dataset.confirm;stop.textContent='ENCERRAR'}},5000)}};
          document.getElementById('bxvoice').onclick=()=>{const on=root.dataset.voiceEnabled!=='false';root.dataset.voiceEnabled=String(!on);document.getElementById('bxvoice').textContent='VOZ: '+(!on?'ON':'OFF');root.dataset.raposoCommand='VOICE_'+(!on?'ON':'OFF');root.dataset.raposoCommandId=Date.now().toString(36)+'-voice'};document.getElementById('bxrestart').onclick=()=>{const id=Date.now().toString(36)+'-restart';root.dataset.raposoCommand='RESTART';root.dataset.raposoCommandId=id;document.documentElement.dataset.raposoCommand='RESTART';document.documentElement.dataset.raposoCommandId=id;window.__RAPOSO_RESTART_REQUEST__=true};
          root.querySelectorAll('#bxmodes [data-mode]').forEach(b=>b.onclick=()=>{if(b.dataset.mode==='dashboard'){const u=root.dataset.dashboardUrl;if(u){const w=window.open(u,'raposo-r12-dashboard-window','popup=yes,width=1180,height=760,resizable=yes,scrollbars=yes');if(w)w.focus()}return}if(b.dataset.mode==='log'){const u=root.dataset.logUrl;if(u)window.open(u,'raposo-r12-log');return}root.dataset.uiMode=b.dataset.mode;root.querySelectorAll('#bxmodes button').forEach(x=>x.classList.toggle('on',x===b))});root.querySelector('#bxmodes [data-mode=classic]').classList.add('on');
          root.querySelectorAll('#bxdashtabs [data-view]').forEach(b=>b.onclick=()=>{const d=document.getElementById('bxdashboard');d.dataset.view=b.dataset.view;root.dataset.dashboardView=b.dataset.view;root.querySelectorAll('#bxdashtabs button').forEach(x=>x.classList.toggle('on',x===b))});root.querySelector('#bxdashtabs [data-view=overview]').classList.add('on');document.getElementById('bxdashboard').dataset.view='overview';
          root.querySelectorAll('#bxfilters [data-range]').forEach(b=>b.onclick=()=>{root.dataset.historyRange=b.dataset.range;root.querySelectorAll('#bxfilters button').forEach(x=>x.classList.toggle('on',x===b));document.getElementById('bxdashboard').dataset.custom=String(b.dataset.range==='custom')});root.querySelector('#bxfilters [data-range=today]').classList.add('on');
          document.getElementById('bxfrom').onchange=e=>root.dataset.historyStart=e.target.value;document.getElementById('bxto').onchange=e=>root.dataset.historyEnd=e.target.value;
          document.getElementById('bxlogbottom').onclick=()=>{const e=document.getElementById('bxlogfull');e.scrollTop=e.scrollHeight};
        }
        root.__lastPaused=!!p.paused;
        root.dataset.dashboardUrl=String(p.dashboard_url||'');
        root.dataset.logUrl=String(p.log_url||'');
        root.dataset.voiceEnabled=String(!!p.voice_enabled);set('bxvoice','VOZ: '+(p.voice_enabled?'ON':'OFF'));
        const ackId=root.dataset.raposoAckId||document.documentElement.dataset.raposoAckId||'',ackPaused=(root.dataset.raposoAckPaused||document.documentElement.dataset.raposoAckPaused)==='true',ackError=root.dataset.raposoAckError||document.documentElement.dataset.raposoAckError||'';
        const pause=document.getElementById('bxpause'),pendingControl=root.__controlPending;
        if(pendingControl&&ackId===pendingControl.id){root.__controlPending=null;pause.disabled=false;if(ackError||ackPaused!==pendingControl.expected){pause.textContent='FALHA - TENTE NOVAMENTE';pause.dataset.failedUntil=String(Date.now()+1400)}else{delete pause.dataset.failedUntil;pause.textContent=ackPaused?'RETOMAR':'PAUSAR'}}
        if(!root.__controlPending&&!(Number(pause.dataset.failedUntil||0)>Date.now())){pause.disabled=false;pause.textContent=p.paused?'RETOMAR':'PAUSAR'}
        root.dataset.controlUrl=p.control_url||'';root.dataset.controlPin=p.control_pin||'';
        const stopText=p.loss_stop?('STOP LOSS ATINGIDO · '+p.loss_current):(p.loss_unconfigured?('CONFIGURE LIMITE LOSS · '+p.loss_current):null);
        set('bxver',p.build);set('bxstate',p.paused?'PAUSADO':(p.feed_m1_frozen?'FEED M1 CONGELADO':(stopText||(p.armed?'OPERANDO':'ANÁLISE ATIVA · ORDENS BLOQUEADAS'))));set('bxasset',p.asset_label);set('bxexpiry',p.expiry);set('bxsetup',p.setup);set('bxscore','SCORE '+p.score);set('bxside',p.side);set('bxparts',p.parts);set('bxwins',p.wins);set('bxlosses',p.losses);set('bxpending',p.pending);set('bxpl',p.pl);set('bxstake',p.stake);set('bxcycle',(p.cycle_index||'—')+'/5');
        const shiftRows=document.getElementById('bxshiftrows');shiftRows.textContent='';(p.loss_shifts||[]).forEach(s=>{const row=document.createElement('div');row.textContent=s.label;row.className=(s.current?'current ':'')+(s.stopped||s.unconfigured?'stop':'');shiftRows.appendChild(row)});
        const state=document.getElementById('bxstate');state.style.color=p.paused?'#ffe66d':(p.feed_m1_frozen?'#ff9f43':(p.armed?'#39ff88':'#ff6b8b'));document.getElementById('bxpl').style.color=Number(p.plnum||0)>=0?'#39ff88':'#ff6b8b';
        if(p.remaining!==null&&p.remaining!==undefined&&Number.isFinite(Number(p.remaining))){root.__deadline=performance.now()+Number(p.remaining)*1000}else{root.__deadline=null;set('bxremaining','--:--')}
        if(!root.__clock){root.__clock=setInterval(()=>{if(root.__deadline===null||root.__deadline===undefined){set('bxremaining','--:--');return}const n=Math.max(0,Math.ceil((root.__deadline-performance.now())/1000));set('bxremaining',String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0'))},100)}
        const log=document.getElementById('bxlogfull'),nearBottom=log.scrollHeight-log.scrollTop-log.clientHeight<45;log.textContent=(p.logs||[]).join('\n');if(nearBottom)log.scrollTop=log.scrollHeight;
        const decided=Number(p.wins)+Number(p.losses),wr=decided?100*Number(p.wins)/decided:0;set('bxkops',p.executed);set('bxkwin',p.wins);set('bxkloss',p.losses);set('bxkpend',p.pending);set('bxkwr',wr.toFixed(2).replace('.',',')+'%');set('bxkpl',p.pl);
        const fill=(id,rows,columns)=>{const body=document.getElementById(id);body.textContent='';rows.forEach(row=>{const tr=document.createElement('tr');columns.forEach(key=>{const td=document.createElement('td');td.textContent=esc(typeof key==='function'?key(row):row[key]);tr.appendChild(td)});body.appendChild(tr)})};
        fill('bxsetups',p.setups||[],['setup','signals','wins','losses',r=>Number(r.rate).toFixed(1)+'%',r=>r.roi===null?'N/D':Number(r.roi).toFixed(2)+'%']);fill('bxadaptive',p.adaptive||[],['setup',r=>r.position+'/5','samples',r=>r.wins+'/'+r.losses,r=>Number(r.wr).toFixed(1)+'%']);
        const history=p.history||[];fill('bxrecent',history.slice(0,12),['position','time','asset','setup','side','candle_exec','revision','result','pnl']);fill('bxhistoryrows',history,['position','time','asset','setup','side','stake','candle_exec','revision','status','result','pnl','expiry']);
        const activeRange=root.dataset.historyRange||'today';root.querySelectorAll('#bxfilters button').forEach(b=>b.classList.toggle('on',b.dataset.range===activeRange));
        """
        try:
            self.driver.execute_script(js, payload)
        except Exception as exc:
            print('[V3.86][OVERLAY][ERRO]', repr(exc), flush=True)
            try:
                self.driver.execute_script("document.getElementById('bullex-pro-overlay')?.remove();document.getElementById('raposo-v386-style')?.remove();")
            except Exception:
                pass
