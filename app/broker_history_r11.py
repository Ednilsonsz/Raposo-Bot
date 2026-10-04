"""Read-only broker history evidence. Never link legacy orders by time/price guesses."""
import hashlib
import json
import time
from datetime import datetime
from broker_day_r11 import today, day_of, closed_time
from asset_catalog import resolve as resolve_asset


def _link_bullex_asset(manager, demo_order_id, active_id):
    """Confirm the Bullex active ID from a returned movement after execution."""
    if demo_order_id is None or active_id in (None, '', 'None'):
        return
    row=manager.db.execute('SELECT our_asset_id,asset_name FROM demo_orders WHERE id=?',
                            (int(demo_order_id),)).fetchone()
    manager.db.execute('UPDATE demo_orders SET bullex_active_id=? WHERE id=?',
                       (str(active_id), int(demo_order_id)))
    if row and row[0] is not None:
        conflict=manager.db.execute('SELECT our_asset_id FROM asset_bullex_ids WHERE bullex_active_id=?',
                                    (str(active_id),)).fetchone()
        if not conflict or int(conflict[0])==int(row[0]):
            now=datetime.now().isoformat(timespec='seconds')
            manager.db.execute('INSERT OR IGNORE INTO asset_bullex_ids(our_asset_id,bullex_active_id,confirmed_at,confirmed_by) VALUES(?,?,?,?)',
                               (int(row[0]),str(active_id),now,'broker-movement'))
            manager.db.execute('UPDATE asset_catalog SET updated_at=? WHERE our_asset_id=?',
                               (now,int(row[0])))
    label=' '.join(str(row[1] or '').split()) if row else ''
    if (label and not label.upper().startswith('ATIVO WS ') and
            label.upper() not in ('ATIVO NÃO VINCULADO', 'ATIVO NAO VINCULADO',
                                  'ATIVO CARREGANDO', 'AGUARDANDO ATIVO')):
        resolve_asset(manager.db, label, str(active_id), confirmed_by='broker-movement')

HISTORY_NAMES = {'history-positions', 'position-history', 'portfolio.history-positions', 'options'}

def is_history(obj):
    # The broker first ACKs our request with name='result' and the same request_id.
    # That ACK is not a history page and must not be diagnosed as a schema failure.
    if obj.get('name') in HISTORY_NAMES:
        return True
    if str(obj.get('request_id','')).startswith('raposo-history:'):
        msg = obj.get('msg')
        return isinstance(msg, dict) and isinstance(msg.get('positions', msg.get('options')), list)
    return False

def opened_time(event):
    """Return the broker's actual order-open time in the app's local time."""
    raw=event.get('open_time_millisecond') or event.get('open_time') or event.get('created')
    try:
        stamp=float(raw or 0)
        if stamp>100000000000: stamp/=1000.0
        if 0<stamp<=time.time()+60:
            return closed_time(stamp)
    except (TypeError,ValueError,OverflowError,OSError):
        pass
    try:
        parsed=datetime.fromisoformat(str(raw).strip().replace('Z','+00:00'))
        return parsed.isoformat(timespec='seconds')
    except (TypeError,ValueError,OverflowError):
        return None

def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS broker_history_pages(
      fingerprint TEXT PRIMARY KEY, socket_key TEXT NOT NULL, request_id TEXT,
      received_at REAL NOT NULL, raw_payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS broker_history_operations(
      page_fingerprint TEXT NOT NULL, item_index INTEGER NOT NULL,
      broker_operation_id TEXT, raw_payload TEXT NOT NULL,
      PRIMARY KEY(page_fingerprint,item_index));
      CREATE INDEX IF NOT EXISTS idx_history_broker_operation ON broker_history_operations(broker_operation_id);
      CREATE INDEX IF NOT EXISTS idx_history_received ON broker_history_pages(received_at);''')

def ingest_history(manager, obj, socket, raw=None):
    raw = raw if raw is not None else json.dumps(obj,ensure_ascii=False)
    if json.loads(raw)!=obj: raise ValueError('history raw payload mismatch')
    fp=hashlib.sha256((socket+'|'+raw).encode()).hexdigest()
    manager.db.execute('INSERT OR IGNORE INTO broker_history_pages VALUES(?,?,?,?,?)',
        (fp,socket,str(obj.get('request_id','')),time.time(),raw))
    body=obj.get('msg')
    items=body.get('positions',body.get('options')) if isinstance(body,dict) else None
    if not isinstance(items,list):
        manager.diagnostic('RESULT_READ_FAILURE','history-schema:'+fp,
            reason='HISTORY_SCHEMA_UNSUPPORTED',message_name=obj.get('name'))
        return
    for index,item in enumerate(items):
        item_raw=json.dumps(item,ensure_ascii=False)
        event=item.get('raw_event',item) if isinstance(item,dict) else {}
        if isinstance(event,dict) and event.get('name')=='option-closed': event=event.get('msg',{})
        # Bullex history wraps binary closes as raw_event.binary_options_option_changed1.
        if isinstance(event,dict) and isinstance(event.get('binary_options_option_changed1'),dict):
            event=event['binary_options_option_changed1']
        event=event if isinstance(event,dict) else {}
        identifier=event.get('option_id')
        field=lambda *keys: next((json.dumps(event[k],ensure_ascii=False) for k in keys if k in event),None)
        manager.db.execute('INSERT OR IGNORE INTO broker_operation_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (fp+':'+str(index),str(identifier) if identifier is not None else None,
             field('balance_id','user_balance_id'),field('active_id'),field('direction'),
             field('amount','sum'),field('open_time_millisecond','open_time','created'),
             field('actual_expire','expiration_time','close_time'),field('profit_percent','payout'),
             field('profit_amount','win_amount'),field('result','status'),str(obj.get('name')),item_raw))
        manager.db.execute('INSERT OR IGNORE INTO broker_history_operations VALUES(?,?,?,?)',
            (fp,index,str(identifier) if identifier is not None else None,item_raw))
    manager.diagnostic('HISTORY_RECEIVED',fp,request_id=obj.get('request_id'),items=len(items))

def active_history_rows(manager):
    """Target explicit pending IDs; never scan all archived JSON for live recovery."""
    activated=str(manager.config.get('risk_return',{}).get('activated_at',''))[:19]
    pending=manager.db.execute("""SELECT o.id,b.account_id,b.broker_id FROM demo_orders o
      LEFT JOIN broker_order_links b ON b.demo_order_id=o.id
      WHERE o.status IN ('SUBMITTED','EXECUTED','UNKNOWN','CLICK_SENT') AND o.requested_at>=?
      AND NOT EXISTS(SELECT 1 FROM broker_confirmed_results r WHERE r.demo_order_id=o.id)""",(activated,)).fetchall()
    rows=[];unlinked=False
    for oid,account,bid in pending:
        if bid is None:unlinked=True;continue
        row=manager.db.execute("""SELECT o.page_fingerprint,o.item_index,o.raw_payload,p.raw_payload
          FROM broker_history_operations o JOIN broker_history_pages p ON p.fingerprint=o.page_fingerprint
          JOIN broker_operation_audit a ON a.frame_fingerprint=o.page_fingerprint||':'||o.item_index
          WHERE o.broker_operation_id=? AND CAST(json_extract(a.account_id,'$') AS TEXT)=?
          ORDER BY p.received_at DESC LIMIT 1""",(str(bid),str(account))).fetchone()
        if row:rows.append(row)
    if unlinked:
        rows.extend(manager.db.execute("""SELECT o.page_fingerprint,o.item_index,o.raw_payload,p.raw_payload
          FROM broker_history_pages p JOIN broker_history_operations o ON o.page_fingerprint=p.fingerprint
          WHERE p.received_at>=?""",(time.time()-300,)).fetchall())
    return list({(r[0],r[1]):r for r in rows}.values())


def recover_history(manager):
    # Closed history only needs retrying briefly for strict time-window recovery
    # of an unlinked order. Older events are revisited only while their explicit
    # broker ID is linked but still has no confirmed settlement. This avoids
    # loading the whole historical archive on every live reconciliation.
    if manager.config.get('risk_return',{}).get('enabled'):
        rows=active_history_rows(manager)
    else:
        rows=manager.db.execute('''SELECT o.page_fingerprint,o.item_index,o.raw_payload,p.raw_payload
          FROM broker_history_operations o
          JOIN broker_history_pages p ON p.fingerprint=o.page_fingerprint
          LEFT JOIN broker_operation_audit a
            ON a.frame_fingerprint=o.page_fingerprint||':'||o.item_index
          WHERE ((p.received_at>=? AND (
            EXISTS (
              SELECT 1 FROM demo_orders o
              WHERE o.status IN ('SUBMITTED','UNKNOWN','CLICK_SENT')
                AND NOT EXISTS (SELECT 1 FROM broker_order_links b0 WHERE b0.demo_order_id=o.id)
                AND o.direction=CASE json_extract(a.direction,'$')
                  WHEN 'call' THEN 'G' WHEN 'put' THEN 'R' ELSE '' END
                AND ABS(o.stake-CAST(json_extract(a.stake_raw,'$') AS REAL))<0.000001
                AND ABS(o.cycle_start-CASE
                  WHEN CAST(json_extract(a.opened_at_raw,'$') AS REAL)>100000000000
                    THEN CAST(json_extract(a.opened_at_raw,'$') AS REAL)/1000.0
                  ELSE CAST(json_extract(a.opened_at_raw,'$') AS REAL) END)<=180)
            OR EXISTS (
              SELECT 1 FROM decisions d
              WHERE d.prediction=CASE json_extract(a.direction,'$')
                  WHEN 'call' THEN 'G' WHEN 'put' THEN 'R' ELSE '' END
                AND NOT EXISTS (SELECT 1 FROM demo_orders o2 WHERE o2.decision_id=d.id)
                AND ABS(d.cycle_start-CASE
                  WHEN CAST(json_extract(a.opened_at_raw,'$') AS REAL)>100000000000
                    THEN CAST(json_extract(a.opened_at_raw,'$') AS REAL)/1000.0
                  ELSE CAST(json_extract(a.opened_at_raw,'$') AS REAL) END)<=180)
            )) OR EXISTS (
            SELECT 1 FROM broker_order_links b
            WHERE b.account_id=json_extract(a.account_id,'$')
              AND b.broker_id=o.broker_operation_id))
            AND NOT EXISTS (
              SELECT 1 FROM broker_confirmed_results r
              WHERE r.account_id=json_extract(a.account_id,'$')
                AND r.broker_id=o.broker_operation_id)''', (time.time()-300,)).fetchall()
    for fp,index,item_raw,page_raw in rows:
        item=json.loads(item_raw)
        key=fp+':'+str(index)
        try:
            if not isinstance(item,dict): raise ValueError('HISTORY_ITEM_UNSUPPORTED')
            # Only explicit broker close fields; generic position P/L is not silently reinterpreted.
            event=item.get('raw_event',item)
            if not isinstance(event,dict): raise ValueError('HISTORY_EVENT_UNSUPPORTED')
            if event.get('name')=='option-closed': event=event.get('msg',{})
            if isinstance(event.get('binary_options_option_changed1'),dict):
                event=event['binary_options_option_changed1']
            required={'option_id','balance_id','active_id','direction','amount','profit_amount','currency','result'}
            if not required.issubset(event): raise ValueError('HISTORY_CLOSE_FIELDS_MISSING')
            stamp=float(event.get('actual_expire') or event['expiration_time'])
            # Reprocessar páginas antigas: ordens executadas podem ter ficado
            # pendentes quando a sessão anterior não recebeu o fechamento.
            # A associação continua baseada somente no broker_id explícito.
            if stamp>time.time(): continue
            execution_at=opened_time(event)
            # Prefer the explicit order link. If the click succeeded but the
            # local link was not persisted, first recover an existing pending
            # demo_order with a strict match. This must happen before creating
            # a synthetic historical row; otherwise SUBMITTED rows accumulate.
            linked=manager.db.execute('SELECT demo_order_id FROM broker_order_links WHERE account_id=? AND broker_id=?',
                (str(event['balance_id']),str(event['option_id']))).fetchone()
            if not linked:
                raw_open=event.get('open_time_millisecond') or event.get('open_time') or event.get('created')
                open_ts=float(raw_open or 0)
                if open_ts > 100000000000: open_ts /= 1000.0
                direction={'call':'G','put':'R'}.get(str(event.get('direction','')).lower())
                try: stake=float(event.get('amount'))
                except (TypeError,ValueError): stake=0.0
                existing=[]
                if open_ts and direction and stake>0:
                    existing=manager.db.execute('''SELECT o.id,o.decision_id,o.asset_id,o.cycle_start
                      FROM demo_orders o
                      WHERE o.status IN ('SUBMITTED','UNKNOWN','CLICK_SENT')
                        AND NOT EXISTS (SELECT 1 FROM broker_order_links b WHERE b.demo_order_id=o.id)
                        AND o.direction=? AND ABS(o.stake-?)<0.000001
                        AND ABS(o.cycle_start-?)<=180
                        AND (o.asset_id=? OR o.bullex_active_id=? OR EXISTS
                             (SELECT 1 FROM asset_bullex_ids ab
                              WHERE ab.our_asset_id=o.our_asset_id
                                AND ab.bullex_active_id=?))
                      ORDER BY ABS(o.cycle_start-?) ASC,o.id DESC LIMIT 2''',
                        (direction,stake,int(open_ts),str(event['active_id']),
                         str(event['active_id']),str(event['active_id']),int(open_ts))).fetchall()
                if len(existing)==1:
                    oid,did,asset,cycle=existing[0]
                    now=__import__('datetime').datetime.now().isoformat(timespec='seconds')
                    manager.db.execute('''UPDATE demo_orders
                      SET bullex_active_id=?,executed_at=?,status='EXECUTED',error=NULL
                      WHERE id=? AND status IN ('SUBMITTED','UNKNOWN','CLICK_SENT')''',
                      (str(event.get('active_id')),execution_at or now,int(oid)))
                    manager.db.execute('INSERT OR IGNORE INTO broker_order_links VALUES(?,?,?,?,?,?)',
                      (oid,str(event['balance_id']),str(event['option_id']),'history',key,'history:'+str(event['option_id'])))
                    manager.db.execute("UPDATE decisions SET status='EXECUTED',updated_at=? WHERE id=?",(now,did))
                    manager.db.commit()
                    linked=(oid,)
                    manager.diagnostic('HISTORY_ORDER_RECOVERED',key,oid,broker_id=str(event['option_id']),reason='STRICT_EXISTING_PENDING_MATCH')
                elif len(existing)>1:
                    manager.diagnostic('RESULT_READ_FAILURE','ambiguous-history:'+key,
                        reason='AMBIGUOUS_PENDING_MATCH',broker_id=str(event['option_id']),result='UNKNOWN')
                    continue
                else:
                    candidate=None
                    if open_ts and direction and stake>0:
                        candidate=manager.db.execute('''SELECT d.id,d.asset_id,d.cycle_start,d.prediction
                          FROM decisions d LEFT JOIN demo_orders o ON o.decision_id=d.id
                          WHERE o.id IS NULL AND d.prediction=? AND ABS(d.cycle_start-?)<=180
                          ORDER BY ABS(d.cycle_start-?) ASC LIMIT 1''',(direction,int(open_ts),int(open_ts))).fetchone()
                    if candidate:
                        did,asset,cycle,pred=candidate
                        known=manager.db.execute('''SELECT 1 FROM asset_bullex_ids
                          WHERE our_asset_id IN (SELECT our_asset_id FROM demo_orders WHERE asset_id=? LIMIT 1)
                          AND bullex_active_id=? LIMIT 1''',(str(asset),str(event['active_id']))).fetchone()
                        if not known and str(asset) not in (str(event.get('active_id')),):
                            candidate=None
                    if candidate:
                        did,asset,cycle,pred=candidate
                        now=__import__('datetime').datetime.now().isoformat(timespec='seconds')
                        manager.db.execute('''INSERT INTO demo_orders
                          (decision_id,asset_id,asset_name,bullex_active_id,cycle_start,leg,direction,stake,requested_at,executed_at,status,error)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (did,str(asset),f'HISTÓRICO {event.get("active_id")}',str(event.get('active_id')),int(cycle),'SIG',direction,stake,now,execution_at or now,'EXECUTED',None))
                        oid=manager.db.execute('SELECT last_insert_rowid()').fetchone()[0]
                        manager.db.execute('INSERT OR IGNORE INTO broker_order_links VALUES(?,?,?,?,?,?)',
                          (oid,str(event['balance_id']),str(event['option_id']),'history',key,'history:'+str(event['option_id'])))
                        manager.db.execute("UPDATE decisions SET status='EXECUTED',updated_at=? WHERE id=?",(now,did))
                        manager.db.commit()
                        linked=(oid,)
                        manager.diagnostic('HISTORY_ORDER_RECOVERED',key,oid,broker_id=str(event['option_id']),reason='STRICT_NEW_MATCH')
                    if not linked:
                        manager.diagnostic('RESULT_READ_FAILURE','history-unlinked:'+key,
                            reason='HISTORY_BROKER_ID_NOT_LINKED',broker_id=str(event['option_id']),result='UNKNOWN')
                        continue
            if execution_at:
                manager.db.execute('UPDATE demo_orders SET executed_at=? WHERE id=? AND COALESCE(executed_at,\'\')<>?',
                                   (execution_at,int(linked[0]),execution_at))
                manager.db.commit()
            # A movimentação retornada é a confirmação final do ID Bullex. Vincula
            # ao cadastro próprio depois da execução, sem usar o saldo da conta.
            _link_bullex_asset(manager, linked[0], event.get('active_id'))
            manager._settle(event,page_raw)
        except (KeyError,ValueError,TypeError,OverflowError,OSError) as exc:
            manager.diagnostic('RESULT_READ_FAILURE','history:'+key,reason=str(exc),result='UNKNOWN')
