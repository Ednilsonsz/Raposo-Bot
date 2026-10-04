"""Daily setup entry gate, based only on confirmed real operations."""
from broker_day_r11 import today as broker_today


def setup_gate(db, config, setup):
    rule = config.get('score_engine', {}).get('daily_setup_block', {})
    if not rule.get('enabled', False):
        return True, ''
    day = broker_today()
    db.execute('CREATE TABLE IF NOT EXISTS daily_setup_blocks(day TEXT, setup TEXT, reason TEXT, PRIMARY KEY(day,setup))')
    held = db.execute('SELECT reason FROM daily_setup_blocks WHERE day=? AND setup=?', (day, setup)).fetchone()
    if held:
        return False, held[0]
    rows = db.execute("""SELECT d.c1_dir,COUNT(DISTINCT r.demo_order_id),
        SUM(r.pnl_minor),COUNT(DISTINCT r.currency)
        FROM broker_confirmed_results r
        JOIN demo_orders o ON o.id=r.demo_order_id AND o.status='EXECUTED'
        JOIN decisions d ON d.id=o.decision_id
        WHERE substr(r.closed_at,1,10)=? AND r.result IN ('WIN','LOSS','DRAW')
        GROUP BY d.c1_dir""", (day,)).fetchall()
    total = sum(int(row[1]) for row in rows)
    for name, operations, pnl, currencies in rows:
        others = total - operations
        # With no other setup, participation is 100% of the day's operations.
        share = operations / others if others else float('inf')
        if (name == setup and operations >= int(rule.get('minimum_operations', 8))
                and share >= float(rule.get('share_vs_others', .30))
                and currencies == 1 and int(pnl or 0) < 0):
            reason = f'SETUP DIA BLOQUEADO: {setup}; operacoes={operations}; demais={others}; P/L={pnl/100:.2f}; dia={day}'
            db.execute('INSERT OR IGNORE INTO daily_setup_blocks VALUES(?,?,?)', (day, setup, reason))
            db.commit()
            return False, reason
    return True, ''
