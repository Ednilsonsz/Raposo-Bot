"""DEMO risk profile: persistent daily stop, single unresolved order, real M1 profitability."""
from datetime import datetime, timedelta
from broker_day_r11 import SAO_PAULO, day_of
from history_expiry_r12 import expiry_label

def daily_gate(db, config, moment=None):
    rule=config.get('risk_return', {})
    if not rule.get('enabled'): return True, ''
    now=moment or datetime.now(SAO_PAULO)
    day=now.astimezone(SAO_PAULO).date().isoformat() if now.tzinfo else now.date().isoformat()
    db.execute('CREATE TABLE IF NOT EXISTS risk_daily_blocks_r12(day TEXT PRIMARY KEY, reason TEXT NOT NULL)')
    held=db.execute('SELECT reason FROM risk_daily_blocks_r12 WHERE day=?',(day,)).fetchone()
    if held: return False, held[0]
    rows=db.execute("SELECT r.closed_at,r.pnl_minor,r.currency FROM broker_confirmed_results r JOIN demo_orders o ON o.id=r.demo_order_id WHERE o.status='EXECUTED' AND substr(r.closed_at,1,10)=? ORDER BY r.closed_at,r.demo_order_id",(day,)).fetchall()
    total=0; limit=int(rule.get('daily_net_loss_minor',4000)); currency=rule.get('currency','USD')
    for stamp,pnl,ccy in rows:
        if day_of(stamp)!=day: continue
        if ccy!=currency: return False,'RISCO DIA: moeda divergente; conciliar antes de operar'
        total+=int(pnl)
        if total <= -limit:
            reason=f'STOP DIARIO {day}: {currency} {total/100:.2f}; limite -{limit/100:.2f}; trava ate o proximo dia'
            db.execute('INSERT OR IGNORE INTO risk_daily_blocks_r12 VALUES(?,?)',(day,reason));db.commit()
            return False,reason
    return True, f'P/L diario {currency} {total/100:.2f}; limite -{limit/100:.2f}'

def entry_gate(db, config, setup, moment=None, asset_id=None, target_ts=None, requested_slots=1):
    rule=config.get('risk_return', {})
    if not rule.get('enabled'): return True,''
    ok,reason=daily_gate(db,config,moment)
    if not ok:return ok,reason
    now=moment or datetime.now(SAO_PAULO)
    day=now.astimezone(SAO_PAULO).date().isoformat()
    pnl=db.execute("SELECT COALESCE(SUM(r.pnl_minor),0) FROM broker_confirmed_results r JOIN demo_orders o ON o.id=r.demo_order_id WHERE o.status='EXECUTED' AND substr(r.closed_at,1,10)=?",(day,)).fetchone()[0]
    reserve_slots=max(1,int(requested_slots))
    if int(pnl)-reserve_slots*int(round(float(config.get('demo_stake',10))*100)) < -int(rule.get('daily_net_loss_minor',4000)):
        return False,'RISCO DIA: saldo de risco insuficiente para uma entrada inteira'
    if rule.get('single_open_order'):
        activated=rule.get('activated_at',now.isoformat())
        recent=(now-timedelta(minutes=2)).isoformat(timespec='seconds')
        multi=config.get('demo_only') and config.get('multi_setup_demo',{}).get('enabled') and asset_id is not None and target_ts is not None
        order_columns={r[1] for r in db.execute('PRAGMA table_info(demo_orders)')}
        cycle_field='o.cycle_start' if 'cycle_start' in order_columns else 'NULL'
        fields='o.id,o.asset_id,o.cycle_start,o.stake,d.c1_dir' if multi else 'o.id,NULL,'+cycle_field+',NULL,NULL'
        pending=db.execute("SELECT "+fields+" FROM demo_orders o LEFT JOIN broker_confirmed_results r ON r.demo_order_id=o.id LEFT JOIN decisions d ON d.id=o.decision_id WHERE o.status IN ('SUBMITTED','EXECUTED') AND r.demo_order_id IS NULL AND (o.requested_at>=? OR o.requested_at>=?)",(activated[:19],recent[:19])).fetchall()
        # Pending reconciliation is not an open M1 position after its candle expires.
        # Keep financial reservations below, but do not freeze execution on labels/API.
        active_pending=[r for r in pending if not r[2] or int(r[2])+60 > now.timestamp()]
        if active_pending:
            permitted=multi and len(active_pending)+reserve_slots<=2 and all(str(r[1])==str(asset_id) and int(r[2] or 0)//60==int(target_ts)//60 and r[4]!=setup for r in active_pending)
            if not permitted:return False,f'OPERACAO PENDENTE: ordem {active_pending[0][0]}; segunda entrada somente outro setup do mesmo candle' 
        if multi:
            reserved=sum(int(round(float(r[3])*100)) for r in pending)
            if int(pnl)-reserved-reserve_slots*int(round(float(config.get('demo_stake',10))*100)) < -int(rule.get('daily_net_loss_minor',4000)):
                return False,'RISCO DIA: reserva conjunta insuficiente'
            shift=now.hour//3+1
            from turn_loss_control_r12 import effective_shift_settings
            shift_cfg=effective_shift_settings(config,f'T{shift}',now)
            if shift_cfg.get('mode')=='LIMIT':
                start=now.replace(hour=(shift-1)*3,minute=0,second=0,microsecond=0).isoformat()[:19]
                losses=db.execute("SELECT count(*) FROM broker_confirmed_results WHERE substr(closed_at,1,19)>=? AND substr(closed_at,1,19)<=? AND result IN ('LOSS','LOSS_GALE')",(start,now.isoformat()[:19])).fetchone()[0]
                if losses+len(pending)+reserve_slots>int(shift_cfg.get('limit',2)):
                    return False,'RISCO TURNO: reserva de losses insuficiente para as ordens'
    if rule.get('profitable_setup_required'):
        rows=db.execute("SELECT r.result,r.pnl_minor,r.stake_minor,r.currency,r.evidence FROM broker_confirmed_results r JOIN demo_orders o ON o.id=r.demo_order_id JOIN decisions d ON d.id=o.decision_id WHERE o.status='EXECUTED' AND d.c1_dir=? ORDER BY r.closed_at DESC,r.demo_order_id DESC",(setup,)).fetchall()
        sample=[]
        for row in rows:
            if expiry_label(row[4])=='1m': sample.append(row)
            if len(sample)>=int(rule.get('setup_lookback',100)):break
        resolved=[r for r in sample if r[0] in ('WIN','LOSS','WIN_GALE','LOSS_GALE')]
        trial=config.get('estrategy_tg',{})
        trial_limit=int(trial.get('bootstrap_operations',10))
        if setup=='Estrategy_TG' and config.get('demo_only') and trial.get('enabled') and trial.get('bootstrap_enabled') and len(resolved)<trial_limit:
            # Explicit DEMO trial only. Daily/shift, pending order and stake gates remain.
            return True,f'Estrategy_TG: teste DEMO {len(resolved)}/{trial_limit}; ROI real exigido ao completar a amostra'
        if len(resolved)<int(rule.get('minimum_setup_operations',1)):
            return False,f'SETUP EM OBSERVACAO: {setup}; sem historico real M1 suficiente'
        if any(r[3]!=rule.get('currency','USD') for r in sample):return False,'SETUP: moedas divergentes'
        pnl=sum(int(r[1]) for r in sample);stake=sum(int(r[2]) for r in sample)
        if pnl<=0 or stake<=0:return False,f'SETUP ROI REAL NAO POSITIVO: {setup}; n={len(sample)}; P/L={pnl/100:.2f}'
    return True,''
