"""Durable, broker-ID-based settlement. No candle or configured payout inputs."""
import functools
import hashlib
import json
import math
import queue
import threading
import time
import uuid
from datetime import datetime
from voice_announcer_r11 import announce as voice_announce
from decimal import Decimal, InvalidOperation
from broker_day_r11 import today, closed_time, day_of
from asset_catalog import resolve as resolve_asset_catalog


def money(value):
    number = Decimal(str(value))
    if not number.is_finite() or number * 100 != (number * 100).to_integral_value():
        raise ValueError('invalid monetary precision')
    return int(number * 100)


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS raposo_result_events(
      id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
      demo_order_id INTEGER, payload TEXT NOT NULL, created_at TEXT NOT NULL,
      logged INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS broker_execution_intents(
      id TEXT PRIMARY KEY, decision_id INTEGER, leg TEXT, asset_id TEXT,
      direction TEXT, stake_minor INTEGER, started REAL, finished REAL,
      demo_order_id INTEGER);
    CREATE TABLE IF NOT EXISTS broker_trade_frames(
      fingerprint TEXT PRIMARY KEY, socket_key TEXT NOT NULL, received INTEGER NOT NULL,
      observed_at REAL NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS broker_websocket_sources(
      socket_key TEXT PRIMARY KEY, origin TEXT NOT NULL, first_seen TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS broker_order_links(
      demo_order_id INTEGER PRIMARY KEY, account_id TEXT NOT NULL,
      broker_id TEXT NOT NULL, socket_key TEXT NOT NULL, request_id TEXT NOT NULL,
      intent_id TEXT UNIQUE NOT NULL, UNIQUE(account_id,broker_id),
      UNIQUE(socket_key,request_id));
    CREATE TABLE IF NOT EXISTS broker_operation_audit(
      frame_fingerprint TEXT PRIMARY KEY, broker_operation_id TEXT, account_id TEXT,
      active_id TEXT, direction TEXT, stake_raw TEXT, opened_at_raw TEXT,
      closed_at_raw TEXT, payout_raw TEXT, gross_return_raw TEXT,
      original_status TEXT, message_name TEXT NOT NULL, raw_payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS broker_confirmed_results(
      demo_order_id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL,
      account_id TEXT NOT NULL, broker_id TEXT NOT NULL,
      result TEXT NOT NULL CHECK(result IN ('WIN','LOSS','WIN_GALE','LOSS_GALE','DRAW')),
      stake_minor INTEGER NOT NULL, return_minor INTEGER NOT NULL,
      pnl_minor INTEGER NOT NULL, currency TEXT NOT NULL,
      closed_at TEXT NOT NULL, confirmed_at TEXT NOT NULL, evidence TEXT NOT NULL,
      UNIQUE(account_id,broker_id));
    CREATE TRIGGER IF NOT EXISTS r6_executed_notice_insert
    AFTER INSERT ON demo_orders WHEN NEW.status='EXECUTED' BEGIN
      INSERT OR IGNORE INTO raposo_result_events(event_key,kind,demo_order_id,payload,created_at)
      VALUES('EXECUTED:'||NEW.id,'EXECUTED',NEW.id,'{}',NEW.executed_at);
    END;
    CREATE TRIGGER IF NOT EXISTS r6_executed_notice_update
    AFTER UPDATE OF status ON demo_orders WHEN NEW.status='EXECUTED' BEGIN
      INSERT OR IGNORE INTO raposo_result_events(event_key,kind,demo_order_id,payload,created_at)
      VALUES('EXECUTED:'||NEW.id,'EXECUTED',NEW.id,'{}',NEW.executed_at);
    END;
    ''')
    from asset_catalog import ensure_schema as ensure_asset_catalog
    ensure_asset_catalog(db)
    # demo_orders pode ser criada neste ponto, depois da primeira conexÃ£o.
    # Repetir a migraÃ§Ã£o garante as colunas usadas pelo painel (cycle_start).
    from database import _ensure_runtime_schema
    _ensure_runtime_schema(db)


def scoreboard(db, day=None):
    # Only the operational day; never reset persisted history.
    day = day or today()
    db.create_function('r6_day',1,lambda value: day_of(value) if value else None)
    rows = db.execute('''SELECT r.result,r.pnl_minor,r.currency FROM broker_confirmed_results r
      WHERE r6_day(r.closed_at)=?''', (day,)).fetchall()
    counts = dict(WIN=0, WIN_GALE=0, LOSS=0, LOSS_GALE=0, DRAW=0)
    currencies = {}
    for result, pnl, currency in rows:
        counts[result] += 1
        currencies[currency] = currencies.get(currency, 0) + pnl
    counts['money'] = currencies
    counts['pending'] = db.execute('''SELECT COUNT(*) FROM demo_orders o
      WHERE status IN ('SUBMITTED','EXECUTED') AND r6_day(COALESCE(executed_at,requested_at))=? AND NOT EXISTS
      (SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id)''',(day,)).fetchone()[0]
    counts['total'] = sum(counts[k] for k in ('WIN','WIN_GALE','LOSS','LOSS_GALE','DRAW'))
    return counts


def bullex_daily_scoreboard(db, day=None):
    """Daily counters from Bullex's own operation feed, independent of Raposo decisions."""
    day = day or today()
    counts = dict(WIN=0, WIN_GALE=0, LOSS=0, LOSS_GALE=0, DRAW=0)
    currencies = {}
    seen = set()

    def closed_events(value):
        if isinstance(value, dict):
            required=('option_id','balance_id','amount','profit_amount','result')
            has_close=('actual_expire' in value or 'expiration_time' in value)
            if has_close and all(k in value for k in required):
                yield value
            for child in value.values():
                yield from closed_events(child)
        elif isinstance(value,list):
            for child in value:
                yield from closed_events(child)

    rows=db.execute('SELECT DISTINCT raw_payload FROM broker_operation_audit').fetchall()
    for payload_raw, in rows:
        try:
            payload=json.loads(payload_raw)
        except (TypeError,ValueError,json.JSONDecodeError):
            continue
        for event in closed_events(payload):
            key=(str(event['balance_id']),str(event['option_id']))
            if key in seen:
                continue
            try:
                stamp=float(event.get('actual_expire') or event['expiration_time'])
                if stamp>10_000_000_000: stamp/=1000.0
                if stamp>time.time()+2 or day_of(closed_time(stamp))!=day:
                    continue
                stake=money(event['amount']); returned=money(event['profit_amount'])
                pnl=returned-stake
                status=str(event['result']).lower()
                outcome={'win':'WIN','won':'WIN','loose':'LOSS','loss':'LOSS','lost':'LOSS',
                         'equal':'DRAW','draw':'DRAW'}.get(status)
                derived='WIN' if pnl>0 else 'LOSS' if pnl<0 else 'DRAW'
                if outcome is None or outcome!=derived:
                    continue
                currency=str(event.get('currency') or 'BRL').upper()
                if len(currency)!=3 or not currency.isalpha():
                    continue
            except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError,OSError):
                continue
            seen.add(key)
            counts[outcome]+=1
            currencies[currency]=currencies.get(currency,0)+pnl
    counts['money']=currencies
    counts['pending']=0
    counts['total']=sum(counts[k] for k in ('WIN','LOSS','DRAW'))
    return counts


def reconciliation_frames(manager):
    if not manager.config.get('risk_return',{}).get('enabled'):
        return manager.db.execute('SELECT socket_key,received,observed_at,payload FROM broker_trade_frames').fetchall()
    activated=str(manager.config.get('risk_return',{}).get('activated_at',''))[:19]
    starts=manager.db.execute("""SELECT i.started FROM broker_execution_intents i
      JOIN demo_orders o ON o.id=i.demo_order_id
      WHERE i.finished IS NOT NULL AND o.requested_at>=?
      AND NOT EXISTS(SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id)""",(activated,)).fetchall()
    # Preserve old open requests/ACKs while their active order remains unresolved.
    cutoff=min([time.time()-120]+[float(r[0])-1 for r in starts])
    return manager.db.execute('SELECT socket_key,received,observed_at,payload FROM broker_trade_frames WHERE observed_at>=?',(cutoff,)).fetchall()


class BrokerResults:
    def __init__(self, db, config=None):
        self.db = db
        self.config=config or {}
        self._async_enabled=bool(self.config.get('risk_return',{}).get('enabled'))
        self._frame_queue=queue.SimpleQueue()
        self._frame_backlog=None
        self._worker_guard=threading.Lock()
        self._metadata_worker=None
        # Reconciliation is retried on the next poll; it must not freeze capture for 30s.
        self.db.execute('PRAGMA busy_timeout=100')
        self.lock = threading.RLock()
        self.intent_queue = queue.SimpleQueue()
        self._intent_backlog = []
        ensure_schema(db)
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_trade_frame_observed ON broker_trade_frames(observed_at)')
        self.db.commit()
        from broker_history_r11 import schema
        schema(db)

    def enqueue_frame(self, snapshot, obj, received, params, entry, raw):
        self._frame_queue.put((snapshot,obj,received,params,entry,raw))

    def schedule_reconcile(self):
        # The driver/capture thread only enqueues. This worker never uses a driver.
        with self._worker_guard:
            if self._metadata_worker is not None and self._metadata_worker.is_alive():return
            self._metadata_worker=threading.Thread(target=self._metadata_cycle,name='raposo-results',daemon=True)
            self._metadata_worker.start()

    def _metadata_cycle(self):
        from bullex_live_capture import BullexLiveCapture
        try:
            for _ in range(200):
                item=self._frame_backlog
                if item is None:
                    if self._frame_queue.empty():break
                    item=self._frame_queue.get()
                self._frame_backlog=item
                snapshot,obj,received,params,entry,raw=item
                BullexLiveCapture._observe_trade_frame(snapshot,obj,received,params,entry,raw,from_worker=True)
                self._frame_backlog=None
            self.reconcile()
        except Exception as exc:
            self.db.rollback()
            print(json.dumps({'event':'RESULT_READ_FAILURE','stage':'metadata_worker','error':repr(exc)}),flush=True)

    def register_source(self, socket_key, origin):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO broker_websocket_sources VALUES(?,?,?)',
                            (socket_key,origin,datetime.now().isoformat()))

    def diagnostic(self, kind, key, order_id=None, **details):
        self.db.execute('''INSERT OR IGNORE INTO raposo_result_events
          (event_key,kind,demo_order_id,payload,created_at) VALUES(?,?,?,?,?)''',
          (kind + ':' + str(key), kind, order_id,
           json.dumps(details, ensure_ascii=False, sort_keys=True), datetime.now().isoformat()))

    def observe_executor(self, executor):
        """Add an observation boundary; preserve original args, result and exceptions.

        Never calls a driver, changes controls, retries execution or sends an order.
        Metadata failure must not change the executor's established behavior.
        """
        original = executor.execute
        @functools.wraps(original)
        def observed(*args, **kwargs):
            if len(args) < 6:
                return original(*args, **kwargs)
            decision_id, asset_id, cycle_start, leg, direction, stake=args[:6]
            intent = uuid.uuid4().hex
            # Do not add SQLite locks or browser commands to the validated execution path.
            self.intent_queue.put(('begin',intent,decision_id,leg,str(asset_id),direction,stake,time.time()))
            executed = False
            try:
                result = original(*args, **kwargs)
                executed = result is True
                return result
            finally:
                self.intent_queue.put(('end',intent,decision_id,leg,time.time(),executed))
        executor.execute = observed

    def _flush_intents(self):
        # A failed transaction keeps its backlog for the next poll/retry.
        with self.lock:
            while not self.intent_queue.empty():
                self._intent_backlog.append(self.intent_queue.get_nowait())
            with self.db:
                for event in self._intent_backlog:
                    if event[0]=='begin':
                        _,iid,did,leg,asset,direction,stake,started=event
                        self.db.execute('INSERT OR IGNORE INTO broker_execution_intents VALUES(?,?,?,?,?,?,?,?,?)',
                            (iid,did,leg,asset,direction,money(stake),started,None,None))
                    else:
                        _,iid,did,leg,finished,executed=event
                        row=self.db.execute("SELECT id FROM demo_orders WHERE decision_id=? AND leg=? AND status='SUBMITTED'",
                                            (did,leg)).fetchone() if executed else None
                        self.db.execute('UPDATE broker_execution_intents SET finished=?,demo_order_id=? WHERE id=?',
                                        (finished,row[0] if row else None,iid))
            self._intent_backlog.clear()

    def observe(self, obj, received, socket_key, observed_at, raw_payload=None):
        """Receive a copy from the existing single performance-log consumer."""
        from broker_history_r11 import is_history, ingest_history
        if received and is_history(obj):
            with self.lock, self.db:
                ingest_history(self, obj, socket_key, raw_payload)
            return
        name = str(obj.get('name') or '')
        msg = obj.get('msg')
        nested_name = str(msg.get('name') or '') if isinstance(msg, dict) else ''
        position_event = None
        if name == 'position-changed' and isinstance(msg, dict):
            raw_event=msg.get('raw_event')
            if isinstance(raw_event, dict):
                position_event=raw_event.get('binary_options_option_changed1')
        relevant = name in ('option','option-closed','socket-option-closed','position-changed') or (
            name == 'sendMessage' and ('open-option' in nested_name or 'get-options' in nested_name))
        if not relevant:
            return
        # Trade envelopes are retained verbatim as JSON, not reduced to the parser's
        # current fields. Auth/profile messages never enter this allowlisted path.
        canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False)
        raw = raw_payload if raw_payload is not None else canonical
        if json.loads(raw) != obj:
            raise ValueError('raw payload differs from parsed envelope')
        fingerprint = hashlib.sha256((socket_key + '|' + str(received) + '|' + canonical).encode()).hexdigest()
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO broker_trade_frames VALUES(?,?,?,?,?)',
                            (fingerprint,socket_key,int(received),observed_at,raw))
            if received and isinstance(msg, dict):
                # Raw values are intentionally not coerced; the original envelope
                # remains available even if a future parser interprets them differently.
                field=lambda *names: next((json.dumps(msg[k],ensure_ascii=False) for k in names if k in msg),None)
                self.db.execute('INSERT OR IGNORE INTO broker_operation_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (fingerprint, str(msg.get('option_id',msg.get('id'))) if ('option_id' in msg or 'id' in msg) else None,
                     field('balance_id','user_balance_id'),field('active_id'),field('direction','dir'),
                     field('sum','amount'),field('open_time_millisecond','open_time','created'),
                     field('close_time','actual_expire','expiration_time','expired'),
                     field('profit_percent','profit_income','payout'),field('profit_amount','win_amount'),
                     field('result','win','status','game_state'),name,raw))
            if (name not in ('option','option-closed') and nested_name != 'binary-options.open-option'
                    and not isinstance(position_event, dict)):
                self.diagnostic('RESULT_READ_FAILURE', 'schema:'+name+':'+nested_name,
                                reason='UNSUPPORTED_BROKER_SCHEMA', message_name=name, nested_name=nested_name)

    def reconcile(self):
        # WAL read snapshots can become obsolete before a later write. Restart
        # this metadata-only, idempotent transaction; never retry an order click.
        import sqlite3
        for attempt in range(3):
            try:
                return self._reconcile_once()
            except sqlite3.OperationalError as exc:
                self.db.rollback()
                if not any(word in str(exc).lower() for word in ('locked','busy')) or attempt==2:
                    raise
                time.sleep(.01)

    def _reconcile_once(self):
        self._flush_intents()
        with self.lock, self.db:
            # Reserve the writer before reading; avoid upgrading an obsolete WAL snapshot.
            self.db.execute('BEGIN IMMEDIATE')
            frames = reconciliation_frames(self)
            requests, replies, closed = {}, {}, []
            for socket, received, stamp, raw in frames:
                obj=json.loads(raw); name=obj['name']; msg=obj.get('msg')
                if not isinstance(msg,dict):
                    continue
                key=(socket,str(obj.get('request_id')))
                if not received and name=='sendMessage' and msg.get('name')=='binary-options.open-option':
                    requests.setdefault(key,[]).append((stamp,msg.get('body') or {}))
                elif received and name=='option':
                    replies.setdefault(key,[]).append(msg)
                elif received and name=='option-closed':
                    closed.append((msg,raw))
                elif received and name=='position-changed':
                    raw_event=msg.get('raw_event')
                    event=(raw_event or {}).get('binary_options_option_changed1') if isinstance(raw_event,dict) else None
                    # Bullex/portfolio fecha opcoes blitz neste envelope. Eventos
                    # "opened" continuam apenas como auditoria; somente um fechamento
                    # com retorno monetario e expiracao pode liquidar o placar.
                    if (isinstance(event,dict) and event.get('profit_amount') is not None
                            and event.get('actual_expire') is not None
                            and str(msg.get('status') or '').lower() == 'closed'):
                        closed.append((event,raw))
            intents=self.db.execute('''SELECT id,decision_id,leg,asset_id,direction,stake_minor,
              started,finished,demo_order_id FROM broker_execution_intents
              WHERE finished IS NOT NULL AND demo_order_id IS NOT NULL''').fetchall()
            candidates={}
            for intent in intents:
                iid,did,leg,asset,direction,stake,start,end,oid=intent
                if self.db.execute('SELECT 1 FROM broker_order_links WHERE demo_order_id=?',(oid,)).fetchone():
                    continue
                matches=[]
                for key, reqs in requests.items():
                    if len(reqs)!=1 or key[1] in ('None',''):
                        continue
                    stamp, body=reqs[0]
                    try:
                        good=(math.isfinite(stamp) and start <= stamp <= end
                              and str(body['active_id'])==asset
                              and body['direction']=={'G':'call','R':'put'}.get(direction)
                              and money(body['price'])==stake
                              and body.get('user_balance_id') is not None)
                    except (KeyError,ValueError,InvalidOperation):
                        good=False
                    if good: matches.append((key,body))
                # A click operates the visible chart. Recover a stale local asset
                # label only through a unique captured request and its broker ACK.
                if not matches:
                    for key, reqs in requests.items():
                        if len(reqs)!=1 or len(replies.get(key,[]))!=1:
                            continue
                        stamp,body=reqs[0]; ack=replies[key][0]
                        try:
                            good=(math.isfinite(stamp) and start <= stamp <= end
                                  and body['direction']=={'G':'call','R':'put'}.get(direction)
                                  and money(body['price'])==stake
                                  and body.get('user_balance_id') is not None
                                  and ack.get('id') is not None
                                  and (ack.get('active_id') is None or str(ack['active_id'])==str(body['active_id']))
                                  and (ack.get('user_balance_id') is None or str(ack['user_balance_id'])==str(body['user_balance_id'])))
                        except (KeyError,ValueError,InvalidOperation):
                            good=False
                        if good: matches.append((key,body))
                if len(matches)==1:
                    key,body=matches[0]
                    candidates.setdefault(key,[]).append((intent,body))
                elif not matches and end < time.time() - 120:
                    # Missing capture is not proof of rejection or execution.
                    # Keep the order recoverable and expose why it is unresolved.
                    self.db.execute("UPDATE demo_orders SET error=? WHERE id=? AND status='SUBMITTED'",
                        ('clique sem confirmacao: requisicao de abertura nao capturada; resultado desconhecido', oid))
                    self.diagnostic('ORDER_LINK_PENDING', 'no-request:' + iid, oid,
                                    reason='OPEN_REQUEST_NOT_CAPTURED', result='UNKNOWN')
                elif len(matches)>1:
                    self.diagnostic('RESULT_READ_FAILURE','ambiguous:'+iid,oid,reason='AMBIGUOUS_OPEN_REQUEST')
            for key, items in candidates.items():
                if len(items)!=1 or len(replies.get(key,[]))!=1:
                    continue
                intent,body=items[0]; ack=replies[key][0]; oid=intent[8]
                broker_id=ack.get('id'); account=str(body['user_balance_id'])
                if broker_id is None:
                    continue
                # Optional echo fields must agree; IDs are linked through the request ACK.
                if ('active_id' in ack and str(ack['active_id'])!=str(body['active_id'])) or (
                    'user_balance_id' in ack and str(ack['user_balance_id'])!=account):
                    self.diagnostic('RESULT_READ_FAILURE','ack:'+str(key),oid,reason='ACK_MISMATCH');continue
                try:
                    self.db.execute('INSERT INTO broker_order_links VALUES(?,?,?,?,?,?)',
                                    (oid,account,str(broker_id),key[0],key[1],intent[0]))
                    actual_asset=str(body['active_id'])
                    if actual_asset != intent[3]:
                        from asset_catalog import lookup
                        catalog=lookup(self.db,actual_asset)
                        label=(catalog or {}).get('asset_name') or 'ATIVO WS '+actual_asset
                        self.db.execute('UPDATE demo_orders SET asset_id=?,bullex_active_id=?,asset_name=?,our_asset_id=? WHERE id=?',
                            (actual_asset,actual_asset,label,(catalog or {}).get('our_asset_id'),oid))
                        self.diagnostic('ASSET_LABEL_RECOVERED','asset-recovered:'+intent[0],oid,
                            reason='UNIQUE_CAPTURED_REQUEST_ACK',original_asset=intent[3],actual_asset=actual_asset)
                    now=datetime.now().isoformat(timespec='seconds')
                    executed_at=now
                    broker_open=ack.get('created_millisecond') or ack.get('created')
                    if broker_open is not None:
                        try:
                            stamp=float(broker_open)
                            if stamp > 100000000000: stamp /= 1000.0
                            if math.isfinite(stamp) and abs(stamp-time.time()) < 86400 * 366:
                                executed_at=closed_time(stamp)
                        except (TypeError, ValueError, OverflowError):
                            pass
                    self.db.execute("UPDATE demo_orders SET status='EXECUTED', executed_at=?, error=NULL WHERE id=? AND status='SUBMITTED'",(executed_at,oid))
                    self.db.execute("UPDATE decisions SET status='EXECUTED', updated_at=? WHERE id=?",(now,intent[1]))
                    try:
                        live=self.db.execute('SELECT broker_at FROM bullex_live_state ORDER BY last_received_at DESC LIMIT 1').fetchone()
                        executed_second=int(float(live[0])%60) if live and live[0] is not None else int(time.time()%60)
                    except Exception:
                        # Telemetry must never interfere with the already validated
                        # broker-id link or with the EXECUTED state transition.
                        executed_second=int(time.time()%60)
                    print(f"[BROKER][EXECUTED] decision_id={intent[1]} | demo_order_id={oid} | segundo={executed_second}", flush=True)
                except Exception as exc:
                    self.diagnostic('RESULT_READ_FAILURE','link:'+str(key),oid,reason='LINK_CONFLICT',error=type(exc).__name__)
            # Libera o write-lock acumulado no vÃ­nculo de frames antes de
            # percorrer milhares de itens do histÃ³rico persistido.
            self.db.commit()
            for close_index,(msg,raw) in enumerate(closed,1):
                self._settle(msg,raw)
                # O histÃ³rico pode conter muitas pÃ¡ginas. Commits em lotes
                # curtos permitem que o analisador e o executor persistam
                # sem alterar a ordem ou o conteÃºdo da reconciliaÃ§Ã£o.
                if close_index % 25 == 0:
                    self.db.commit()
            self.db.commit()
            from broker_history_r11 import recover_history
            rule=self.config.get('risk_return',{})
            needs_history=True
            if rule.get('enabled'):
                needs_history=bool(self.db.execute("SELECT 1 FROM demo_orders o WHERE o.status IN ('SUBMITTED','EXECUTED') AND o.requested_at>=? AND NOT EXISTS (SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id) LIMIT 1",(str(rule.get('activated_at',''))[:19],)).fetchone())
            if needs_history:recover_history(self)
            for oid, in self.db.execute('''SELECT id FROM demo_orders WHERE status='SUBMITTED'
              AND NOT EXISTS(SELECT 1 FROM broker_order_links b WHERE b.demo_order_id=demo_orders.id)''').fetchall():
                self.diagnostic('ORDER_LINK_PENDING','unlinked:'+str(oid),oid,reason='BROKER_ID_NOT_LINKED')
            for oid, in self.db.execute('''SELECT o.id FROM demo_orders o JOIN broker_order_links b ON b.demo_order_id=o.id
              WHERE o.status='EXECUTED' AND o.executed_at < ? AND NOT EXISTS
              (SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id)''',
              (datetime.fromtimestamp(time.time()-120).isoformat(),)).fetchall():
                self.diagnostic('RESULT_READ_FAILURE','waiting-close:'+str(oid),oid,reason='CLOSED_EVENT_NOT_RECEIVED',result='UNKNOWN')
            events=self.db.execute('''SELECT e.id,e.kind,e.demo_order_id,e.payload,o.decision_id,
              o.asset_id,o.direction,o.stake,o.executed_at
              FROM raposo_result_events e LEFT JOIN demo_orders o ON o.id=e.demo_order_id WHERE logged=0''').fetchall()
            for eid,kind,oid,payload,did,asset,direction,stake,executed_at in events:
                details=json.loads(payload)
                if kind=='EXECUTED':
                    details.update(active_id=asset,direction=direction,stake=stake,executed_at=executed_at,result='UNKNOWN')
                    self.db.execute('UPDATE raposo_result_events SET payload=? WHERE id=?',(json.dumps(details),eid))
                print(json.dumps(dict(event=kind,event_id=eid,demo_order_id=oid,decision_id=did,**details),ensure_ascii=False),flush=True)
                self.db.execute('UPDATE raposo_result_events SET logged=1 WHERE id=?',(eid,))

    def _settle(self, msg, raw):
        account=msg.get('balance_id'); broker_id=msg.get('option_id')
        if account is None or broker_id is None:
            self.diagnostic('RESULT_READ_FAILURE',hashlib.sha256(raw.encode()).hexdigest(),reason='CLOSE_ID_MISSING');return
        row=self.db.execute('''SELECT o.id,o.decision_id,o.leg,o.asset_id,o.asset_name,o.direction,o.stake
          FROM broker_order_links b JOIN demo_orders o ON o.id=b.demo_order_id
          WHERE b.account_id=? AND b.broker_id=? AND o.status='EXECUTED' ''',(str(account),str(broker_id))).fetchone()
        if not row:
            return  # Manual or unmatched order: cannot affect this robot's scoreboard.
        oid,did,leg,asset,asset_name,direction,stake=row
        # O nome capturado no grÃƒÂ¡fico jÃƒÂ¡ foi salvo com o ID provisÃƒÂ³rio (76/86).
        # Agora o active_id real retornado pela Bullex fecha o vÃƒÂ­nculo no catÃƒÂ¡logo.
        if asset_name and msg.get('active_id') is not None:
            try: resolve_asset_catalog(self.db,asset_name,str(msg.get('active_id')),confirmed_by='broker-result')
            except Exception as exc: self.diagnostic('RESULT_READ_FAILURE','asset-catalog:'+str(oid),oid,reason=repr(exc))
        try:
            if str(msg['active_id'])!=asset or msg['direction']!={'G':'call','R':'put'}[direction]:
                raise ValueError('asset/direction mismatch')
            deposited=money(msg['amount']); returned=money(msg['profit_amount'])
            if deposited!=money(stake) or deposited<=0 or returned<0:
                raise ValueError('stake/return mismatch')
            # In option-closed, profit_amount is the credited gross return, not net P/L.
            pnl=returned-deposited
            outcome={'win':'WIN','loose':'LOSS','loss':'LOSS','equal':'DRAW'}.get(str(msg['result']).lower())
            if outcome is None or outcome != ('WIN' if pnl>0 else 'LOSS' if pnl<0 else 'DRAW'):
                raise ValueError('outcome disagrees with actual money')
            currency=str(msg['currency']).upper()
            if len(currency)!=3 or not currency.isalpha(): raise ValueError('currency missing')
            stamp=float(msg.get('actual_expire') or msg['expiration_time'])
            if not math.isfinite(stamp) or stamp>time.time()+2: raise ValueError('not closed yet')
            closed_at=closed_time(stamp)
            if leg=='C3' and outcome in ('WIN','LOSS'): outcome += '_GALE'
        except (KeyError,ValueError,TypeError,InvalidOperation,OverflowError,OSError) as exc:
            self.diagnostic('RESULT_READ_FAILURE','close:'+str(account)+':'+str(broker_id),oid,reason=str(exc),result='UNKNOWN');return
        data=(oid,did,str(account),str(broker_id),outcome,deposited,returned,pnl,currency,closed_at)
        existing=self.db.execute('''SELECT demo_order_id,decision_id,account_id,broker_id,result,
          stake_minor,return_minor,pnl_minor,currency,closed_at FROM broker_confirmed_results WHERE demo_order_id=?''',(oid,)).fetchone()
        if existing:
            if tuple(existing)!=data:
                self.diagnostic('RESULT_READ_FAILURE','conflict:'+str(oid),oid,reason='CONFLICTING_CLOSED_EVENT')
            return
        self.db.execute('INSERT INTO broker_confirmed_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        data+(datetime.now().isoformat(),raw))
        # This is the sole operational settlement writer. Candle history stays separate.
        self.db.execute("UPDATE decisions SET result=?,status='FINALIZED',updated_at=? WHERE id=?",
                        (outcome,datetime.now().isoformat(),did))
        self.diagnostic('RESULT_CONFIRMED',oid,oid,broker_id=str(broker_id),result=outcome,
                        pnl_minor=pnl,currency=currency,closed_at=closed_at)
        voice_announce({'WIN':'win','LOSS':'loss','WIN_GALE':'win','LOSS_GALE':'loss','DRAW':'draw'}.get(outcome, 'draw'))

