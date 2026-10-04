from turn_loss_control_r12 import shift_sql, shift_window
from broker_day_r11 import today as broker_today
import json, os, re, secrets, socket, sqlite3, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from database import DB
from broker_results_r11 import scoreboard
from paths_r11 import APP_DIR, RUNTIME_DIR

BASE = APP_DIR
WEB = BASE / 'mobile_app'

class MobileControlServer:
    def __init__(self, cfg, executor=None, analyzer=None, host='0.0.0.0', port=8765):
        self.cfg=cfg; self.executor=executor; self.analyzer=analyzer
        self.host=host; self.port=int(port)
        self.pin=str((cfg.get('mobile_control') or {}).get('pin') or '').strip()
        if not (self.pin.isdigit() and len(self.pin)==6):
            self.pin=f'{secrets.randbelow(1000000):06d}'
            cfg.setdefault('mobile_control',{})['pin']=self.pin
        self.command_lock=threading.Lock(); self.command=None
        self.control_handler=None
        self._signal_analytics_cache={'day':None,'at':0.0,'data':None}
        self.httpd=None; self.thread=None
        self.ip=self._local_ip(); self.url=f'http://{self.ip}:{self.port}'

    def _local_ip(self):
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8',80)); return s.getsockname()[0]
        except Exception:
            return '127.0.0.1'
        finally:
            s.close()

    def set_runtime(self, executor=None, analyzer=None):
        if executor is not None: self.executor=executor
        if analyzer is not None: self.analyzer=analyzer

    def pop_command(self):
        with self.command_lock:
            c=self.command; self.command=None; return c

    def _push(self, cmd):
        with self.command_lock: self.command=cmd

    def set_control_handler(self, handler):
        self.control_handler=handler

    def _signal_analytics(self, db, day):
        """Daily pre-trade signal analysis; separate from confirmed order results."""
        cached=self._signal_analytics_cache
        if cached.get('day')==day and cached.get('data') is not None and time.monotonic()-cached.get('at',0.0)<10:
            return cached['data']
        empty={'setups':[],'candles':[],'shifts':[]}
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_signals'").fetchone()
        if not exists:
            data=empty
        else:
            outcome_sql="""COUNT(*),
                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome='DRAW' THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END), AVG(score)"""
            params=(str(day),)
            setups=db.execute(f"""SELECT COALESCE(setup,'SEM SETUP'),{outcome_sql}
                FROM score_signals WHERE substr(created_at,1,10)=?
                GROUP BY COALESCE(setup,'SEM SETUP') ORDER BY COUNT(*) DESC""",params).fetchall()
            candles=db.execute(f"""SELECT COALESCE(setup,'SEM SETUP'),
                    CAST((signal_ts % 300) / 60 AS INTEGER)+1,{outcome_sql}
                FROM score_signals WHERE substr(created_at,1,10)=? AND signal_ts IS NOT NULL
                  AND CAST((signal_ts % 300) / 60 AS INTEGER) BETWEEN 0 AND 4
                GROUP BY COALESCE(setup,'SEM SETUP'),CAST((signal_ts % 300) / 60 AS INTEGER)+1
                ORDER BY CAST((signal_ts % 300) / 60 AS INTEGER)+1 ASC,
                         COALESCE(setup,'SEM SETUP') COLLATE NOCASE ASC""",params).fetchall()
            shift_expr=shift_sql("created_at")
            shifts=db.execute(f"""SELECT {shift_expr},{outcome_sql}
                FROM score_signals WHERE substr(created_at,1,10)=?
                GROUP BY {shift_expr} ORDER BY COUNT(*) DESC""",params).fetchall()
            def pack(row, dimensions):
                *keys,count,wins,losses,draws,pending,avg_score=row
                wins=int(wins or 0); losses=int(losses or 0); decided=wins+losses
                result=dict(zip(dimensions,keys))
                result.update(signals=int(count or 0),wins=wins,losses=losses,draws=int(draws or 0),
                              pending=int(pending or 0),rate=round(100*wins/decided,1) if decided else 0.0,
                              avg_score=round(float(avg_score or 0),1),roi=None)
                return result
            shift_summaries=[]
            for row in shifts:
                item=pack(row,('shift',))
                item['operations']=item['wins']+item['losses']
                if item['operations']:
                    shift_summaries.append(item)
            data={'setups':[pack(row,('setup',)) for row in setups],
                  'candles':[pack(row,('setup','candle')) for row in candles],
                  'shifts':shift_summaries}
            # Financial ROI is separate from theoretical signal WR: use only
            # executed orders, tied to the selected setup and its original signal.
            candle_expr="""CASE WHEN a.signal_ts IS NOT NULL
                THEN CAST((a.signal_ts % 300) / 60 AS INTEGER)+1 ELSE NULL END"""
            # Confirmed financial results use close time, matching the LOSS controller.
            shift_expr=shift_sql("r.closed_at")
            realized=db.execute(f"""SELECT COALESCE(d.c1_dir,'SEM SETUP'),{candle_expr},{shift_expr},
                    r.currency,SUM(r.pnl_minor),SUM(r.stake_minor),COUNT(*),
                    SUM(CASE WHEN r.result LIKE 'WIN%' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN r.result LIKE 'LOSS%' THEN 1 ELSE 0 END)
                FROM decisions d JOIN demo_orders o ON o.decision_id=d.id AND o.status='EXECUTED'
                JOIN broker_confirmed_results r ON r.demo_order_id=o.id
                LEFT JOIN adaptive_position_decisions a ON a.decision_id=d.id AND a.setup=d.c1_dir
                LEFT JOIN score_signals s ON s.id=(SELECT MAX(s0.id) FROM score_signals s0
                    WHERE s0.asset_id=o.asset_id AND s0.setup=d.c1_dir AND s0.signal_ts=a.signal_ts)
                WHERE substr(r.closed_at,1,10)=?
                GROUP BY COALESCE(d.c1_dir,'SEM SETUP'),{candle_expr},{shift_expr},r.currency""",(str(day),)).fetchall()
            setup_roi={}; candle_roi={}; shift_roi={}
            def add_financial(bucket,key,currency,pnl_minor,stake_minor,operations,wins,losses):
                if key is None or not currency:
                    return
                currencies=bucket.setdefault(key,{})
                totals=currencies.setdefault(str(currency),[0,0,0,0,0])
                totals[0]+=int(pnl_minor or 0); totals[1]+=int(stake_minor or 0)
                totals[2]+=int(operations or 0); totals[3]+=int(wins or 0); totals[4]+=int(losses or 0)
            for setup,candle,shift,currency,pnl_minor,stake_minor,operations,wins,losses in realized:
                add_financial(setup_roi,setup,currency,pnl_minor,stake_minor,operations,wins,losses)
                if candle is not None:
                    add_financial(candle_roi,(setup,int(candle)),currency,pnl_minor,stake_minor,operations,wins,losses)
                if shift is not None:
                    add_financial(shift_roi,shift,currency,pnl_minor,stake_minor,operations,wins,losses)
            def financial_roi(currency_totals):
                if not currency_totals or len(currency_totals)!=1:
                    return None,None,0,0,0
                pnl_minor,stake_minor,operations,wins,losses=next(iter(currency_totals.values()))
                roi=round(100*pnl_minor/stake_minor,2) if stake_minor>0 else None
                return roi,pnl_minor,operations,wins,losses
            for item in data['setups']:
                item['roi'],item['realized_pl_minor'],item['real_operations'],item['real_wins'],item['real_losses']=financial_roi(setup_roi.get(item['setup']))
            for item in data['candles']:
                item['roi'],item['realized_pl_minor'],item['real_operations'],item['real_wins'],item['real_losses']=financial_roi(candle_roi.get((item['setup'],int(item['candle']))))
            data['candles'].sort(key=lambda item:(
                int(item.get('candle') or 0),
                str(item.get('setup') or '').casefold()
            ))
            for item in data['shifts']:
                item['roi'],item['realized_pl_minor'],item['real_operations'],item['real_wins'],item['real_losses']=financial_roi(shift_roi.get(item['shift']))
        self._signal_analytics_cache={'day':day,'at':time.monotonic(),'data':data}
        return data

    def apply_control(self, command, command_id=''):
        command=str(command or '').upper()
        if command not in ('PAUSE','RESUME'):
            return {'ok':False,'error':'COMMAND','id':str(command_id or '')}
        if self.control_handler is None:
            return {'ok':False,'error':'MOTOR_INDISPONIVEL','id':str(command_id or '')}
        try:
            result=dict(self.control_handler(command, str(command_id or '')) or {})
            result.update(ok=not bool(result.get('error')), command=command, id=str(command_id or ''))
            if result['ok']:
                # Publish to the main loop as well as the fast handler. Otherwise
                # synchronize_pause reads the previous browser flag and undoes
                # the accepted command on its next iteration.
                self._push(command)
            return result
        except Exception as exc:
            return {'ok':False,'error':type(exc).__name__,'command':command,'id':str(command_id or '')}

    def status(self):
        out={'version':str(self.cfg.get('version','')),'armed':bool(getattr(self.executor,'armed',False)),
             'paused':bool(getattr(self.analyzer,'paused',True)),
             'feed_m1_frozen':bool(self.cfg.get('_feed_m1_frozen',False)),
             'asset':'EUR/USD OTC','stake':float(self.cfg.get('demo_stake',10)),'mode':str(self.cfg.get('run_mode','FULL')),
             'server_time':time.strftime('%H:%M:%S'),'setup':'AGUARDANDO SETUP','score':None,'side':'—','price':None,
             'remaining':None,'executed':0,'blocked':0,'wins':0,'losses':0,'pl':0.0,'last':'—'}
        controls=dict(getattr(self.executor,'_control_read_diagnostic',{}) or {})
        out['controls']={k:controls.get(k) for k in ('expiry','amount','source','error')}
        stamp=controls.get('captured_at')
        out['controls']['age_seconds']=round(time.monotonic()-stamp,2) if isinstance(stamp,(int,float)) else None
        try:
            db=sqlite3.connect(DB,timeout=5)
            st=db.execute("SELECT current_to,broker_at FROM bullex_live_state WHERE asset_id=?",(str(self.cfg.get('asset_id','76')),)).fetchone()
            if st: out['remaining']=max(0,int(round(float(st[0])-float(st[1]))))
            c=db.execute("SELECT close FROM candles WHERE asset_id=? AND timeframe=60 ORDER BY timestamp DESC LIMIT 1",(str(self.cfg.get('asset_id','76')),)).fetchone()
            if c: out['price']=float(c[0])
            sc=db.execute("SELECT setup,side,score,outcome FROM score_signals ORDER BY id DESC LIMIT 1").fetchone()
            if sc:
                out['setup']=sc[0] or out['setup']; out['side']='CALL' if sc[1]=='G' else 'PUT' if sc[1]=='R' else str(sc[1] or '—'); out['score']=float(sc[2]) if sc[2] is not None else None
            orders=db.execute("SELECT SUM(status='EXECUTED'),SUM(status='BLOCKED') FROM demo_orders").fetchone() or (0,0)
            out['executed']=int(orders[0] or 0); out['blocked']=int(orders[1] or 0)
            today=broker_today()
            confirmed=scoreboard(db,today)
            out['wins']=confirmed['WIN'];out['win_gale']=confirmed['WIN_GALE']
            out['losses']=confirmed['LOSS']+confirmed['LOSS_GALE']
            out['pending']=confirmed['pending']
            out['money']={currency:minor/100 for currency,minor in confirmed['money'].items()}
            out['currency']=next(iter(out['money']),None) if len(out['money'])<=1 else None
            out['pl']=next(iter(out['money'].values()),0.0) if len(out['money'])<=1 else None
            lo=db.execute("""SELECT o.direction,r.result,o.status FROM demo_orders o LEFT JOIN broker_confirmed_results r ON r.demo_order_id=o.id ORDER BY o.id DESC LIMIT 1""").fetchone()
            if lo:
                sd='CALL' if lo[0]=='G' else 'PUT' if lo[0]=='R' else str(lo[0] or '—')
                out['last']=f'{sd} · {lo[1] or ("UNKNOWN" if lo[2]=="EXECUTED" else lo[2]) or "—"}'
            db.close()
        except Exception as e:
            out['db_error']=type(e).__name__
        return out

    def dashboard(self, range_key='today', start='', end=''):
        """Read-only dashboard snapshot; never touches Selenium or execution."""
        db=sqlite3.connect(DB,timeout=5)
        try:
            from ui_overlay_massa_v386_r11 import BullexOverlay
            view=BullexOverlay.__new__(BullexOverlay)
            view.db=db; view._history_cache_key=None; view._history_cache=[]; view._history_cache_at=0.0
            request={'range':str(range_key or 'today'),'start':str(start or ''),'end':str(end or '')}
            history=view._history_rows({**request,'mode':'closed'})
            # A lista de Últimas Movimentações mostra apenas ordens aceitas pela
            # Bullex, ainda sem resultado. O contador continua usando SUBMITTED.
            pending_count_rows=view._history_rows({'range':'max','start':'','end':'','mode':'pending'})
            pending=view._history_rows({'range':'max','start':'','end':'','mode':'confirmed_pending'})
            confirmed=scoreboard(db,broker_today())
            wins=sum(1 for row in history if row.get('result') in ('WIN','WIN_GALE'))
            losses=sum(1 for row in history if row.get('result') in ('LOSS','LOSS_GALE'))
            decided=wins+losses
            money={}
            for row in history:
                currency=str(row.get('currency') or '')
                minor=row.get('pnl_minor')
                if currency and minor is not None:
                    money[currency]=money.get(currency,0)+int(minor)
            pl=' · '.join(f'{currency} {minor/100:+.2f}' for currency,minor in sorted(money.items())) or '0.00'
            pending_filtered=view._history_rows({**request,'mode':'pending'})
            signal_analytics=self._signal_analytics(db,broker_today())
            subtotals=[]
            settlement_times=dict(db.execute('SELECT demo_order_id,closed_at FROM broker_confirmed_results').fetchall())
            def shift_label(row):
                # Financial subtotals use settlement time, as does the loss gate.
                raw=str(settlement_times.get(row.get('id')) or '')
                try:
                    from datetime import datetime
                    name, _, _ = shift_window(datetime.fromisoformat(raw.replace('Z','+00:00')))
                    return 'Turno '+name[1:]
                except (ValueError, TypeError):
                    return 'Sem horário'
            for group, label in (('setup','Setup'), ('candle_exec','Candle'), ('shift','Turno')):
                buckets={}
                for row in history:
                    key=shift_label(row) if group == 'shift' else str(row.get(group) or '—')
                    item=buckets.setdefault(key, {'group':label,'key':key,'count':0,'wins':0,'losses':0,'money':{},'invested':{}})
                    item['count']+=1
                    if row.get('result') in ('WIN','WIN_GALE'):
                        item['wins']+=1
                    elif row.get('result') in ('LOSS','LOSS_GALE'):
                        item['losses']+=1
                    currency=str(row.get('currency') or '')
                    if currency and row.get('pnl_minor') is not None:
                        item['money'][currency]=item['money'].get(currency,0)+int(row['pnl_minor'])
                        item['invested'][currency]=item['invested'].get(currency,0)+int(row.get('stake_minor') or 0)
                for item in buckets.values():
                    item['pl']=' · '.join(f'{c} {v/100:+.2f}' for c,v in sorted(item['money'].items())) or '0.00'
                    item['roi']=(round(100*next(iter(item['money'].values()))/next(iter(item['invested'].values())),2)
                                 if len(item['money'])==1 and next(iter(item['invested'].values()),0)>0 else None)
                    item.pop('money',None)
                    item.pop('invested',None)
                subtotals.extend(buckets.values())
            return {'build':str(self.cfg.get('build_revision') or 'V3.86 · R12'),
                    'history':history,'pending_history':pending,
                    'subtotals':subtotals,
                    'signal_analytics':signal_analytics,
                    'operations':len(history),'wins':wins,'losses':losses,
                    'pending':len(pending_count_rows),'pending_filtered':len(pending_filtered),
                    'win_rate':round(100*wins/decided,2) if decided else 0.0,
                    'pl':pl,'paused':bool(getattr(self.analyzer,'paused',True)),
                    'armed':bool(getattr(self.executor,'armed',False)),
                    'feed_m1_frozen':bool(self.cfg.get('_feed_m1_frozen',False))}
        finally:
            db.close()

    def start(self):
        outer=self
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self,*a): pass
            def _json(self,obj,code=200):
                raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
                self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Access-Control-Allow-Origin','https://trade.bull-ex.com'); self.send_header('Access-Control-Allow-Headers','Content-Type,X-Bullex-Pin'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def _authorized(self):
                pin=self.headers.get('X-Bullex-Pin','')
                if not pin:
                    try:
                        from urllib.parse import parse_qs
                        pin=parse_qs(urlparse(self.path).query).get('pin',[''])[0]
                    except Exception: pin=''
                return secrets.compare_digest(str(pin),outer.pin)
            def do_GET(self):
                p=urlparse(self.path).path
                if p=='/api/status':
                    if not self._authorized(): return self._json({'ok':False,'error':'PIN'},401)
                    return self._json({'ok':True,'data':outer.status()})
                if p=='/api/dashboard':
                    if not self._authorized(): return self._json({'ok':False,'error':'PIN'},401)
                    from urllib.parse import parse_qs
                    q=parse_qs(urlparse(self.path).query)
                    data=outer.dashboard(q.get('range',['today'])[0],q.get('start',[''])[0],q.get('end',[''])[0])
                    return self._json({'ok':True,'data':data})
                if p=='/api/log':
                    if not self._authorized(): return self._json({'ok':False,'error':'PIN'},401)
                    from ui_overlay_massa_v386_r11 import BullexOverlay
                    view=BullexOverlay.__new__(BullexOverlay)
                    return self._json({'ok':True,'data':{'logs':view._log_tail(500)}})
                if p=='/api/ping': return self._json({'ok':True,'version':outer.cfg.get('version','')})
                rel='index.html' if p in ('','/') else p.lstrip('/')
                target=(WEB/rel).resolve()
                try: target.relative_to(WEB.resolve())
                except Exception: self.send_error(403); return
                if not target.exists() or not target.is_file(): self.send_error(404); return
                mime='text/html; charset=utf-8' if target.suffix=='.html' else 'application/javascript; charset=utf-8' if target.suffix=='.js' else 'application/manifest+json' if target.suffix=='.json' else 'image/svg+xml' if target.suffix=='.svg' else 'application/octet-stream'
                raw=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_POST(self):
                p=urlparse(self.path).path
                if p not in ('/api/arm','/api/pause','/api/stop','/api/restart','/api/control'): return self._json({'ok':False,'error':'NOT_FOUND'},404)
                if not self._authorized(): return self._json({'ok':False,'error':'PIN'},401)
                if p=='/api/control':
                    try:
                        size=min(4096,int(self.headers.get('Content-Length','0') or 0))
                        data=json.loads(self.rfile.read(size) or b'{}')
                    except Exception:
                        return self._json({'ok':False,'error':'JSON'},400)
                    result=outer.apply_control(data.get('command'),data.get('id'))
                    return self._json(result,200 if result.get('ok') else 409)
                cmd=p.rsplit('/',1)[-1].upper(); outer._push(cmd)
                return self._json({'ok':True,'command':cmd})
            def do_OPTIONS(self):
                self.send_response(204); self.send_header('Access-Control-Allow-Origin','https://trade.bull-ex.com'); self.send_header('Access-Control-Allow-Methods','POST,OPTIONS'); self.send_header('Access-Control-Allow-Headers','Content-Type,X-Bullex-Pin'); self.end_headers()
        try:
            self.httpd=ThreadingHTTPServer((self.host,self.port),Handler)
        except OSError:
            self.httpd=ThreadingHTTPServer((self.host,0),Handler); self.port=self.httpd.server_address[1]; self.url=f'http://{self.ip}:{self.port}'
        else:
            # Porta 0 significa “escolha uma porta livre”; publicar :0 quebra o Chrome.
            self.port=int(self.httpd.server_address[1]); self.url=f'http://{self.ip}:{self.port}'
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True,name='BullexMobileServer'); self.thread.start()
        try:
            (RUNTIME_DIR/'ACESSO_CELULAR.txt').write_text(f'BULLEX MOBILE V{self.cfg.get("version","")}\nURL: {self.url}\nPIN: {self.pin}\n\nUse somente na sua rede local.\n',encoding='utf-8')
        except Exception: pass
        return self.url,self.pin

    def stop(self):
        try:
            if self.httpd: self.httpd.shutdown(); self.httpd.server_close()
        except Exception: pass
