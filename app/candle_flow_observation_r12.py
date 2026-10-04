"""CANDLE_FLOW observation only; paired theoretical outcomes, never broker P/L."""
import json
from datetime import datetime

ENTRY_ENABLED = False

def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS candle_flow_observations(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, asset_id TEXT NOT NULL,
        signal_ts INTEGER NOT NULL, target_ts INTEGER NOT NULL, observed_second REAL,
        direction_original TEXT NOT NULL, direction_inverted TEXT NOT NULL,
        score REAL, details_json TEXT, target_open REAL, target_close REAL,
        outcome_original TEXT, outcome_inverted TEXT, resolved_at TEXT,
        UNIQUE(asset_id,signal_ts))""")

def observe(db, asset, signal_ts, second, result, arm_second, minimum_score):
    if not result or not result.get('signal') or not arm_second <= float(second) < 60:
        return
    if float(result.get('score',0)) < minimum_score:
        return
    original='CALL' if int(result['signal']) == 1 else 'PUT'
    inverted='PUT' if original == 'CALL' else 'CALL'
    ensure_schema(db)
    db.execute("""INSERT OR IGNORE INTO candle_flow_observations(
        created_at,asset_id,signal_ts,target_ts,observed_second,
        direction_original,direction_inverted,score,details_json)
        VALUES(?,?,?,?,?,?,?,?,?)""",(datetime.now().isoformat(timespec='seconds'),
        str(asset),int(signal_ts),int(signal_ts)+60,float(second),original,inverted,
        float(result.get('score',0)),json.dumps(result,ensure_ascii=False)))
    db.commit()

def resolve(db, asset, current_from):
    ensure_schema(db)
    rows=db.execute("SELECT id,target_ts,direction_original FROM candle_flow_observations WHERE asset_id=? AND outcome_original IS NULL AND target_ts+60<=?",(str(asset),int(current_from))).fetchall()
    changed=False
    for oid,target,side in rows:
        candle=db.execute("SELECT open,close FROM candles WHERE asset_id=? AND timeframe=60 AND timestamp=?",(str(asset),int(target))).fetchone()
        if not candle: continue
        op,cl=map(float,candle)
        original='DRAW' if op == cl else ('WIN' if (cl>op) == (side=='CALL') else 'LOSS')
        inverse={'WIN':'LOSS','LOSS':'WIN','DRAW':'DRAW'}[original]
        db.execute("UPDATE candle_flow_observations SET target_open=?,target_close=?,outcome_original=?,outcome_inverted=?,resolved_at=? WHERE id=?",(op,cl,original,inverse,datetime.now().isoformat(timespec='seconds'),oid));changed=True
    if changed:db.commit()
