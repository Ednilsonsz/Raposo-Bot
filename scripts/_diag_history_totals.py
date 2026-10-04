import sqlite3
p=r"L:\Bullex_Robo\Raposo_Data\database\candles.db"
c=sqlite3.connect('file:'+p.replace('\\','/')+'?mode=ro',uri=True,timeout=10)
c.execute('PRAGMA busy_timeout=10000')
print('tables', [r[0] for r in c.execute("select name from sqlite_master where type='table' and name in ('demo_orders','decisions','broker_confirmed_results','score_signals')")])
print('results', c.execute("select result,count(*) from broker_confirmed_results group by result").fetchall())
print('joined', c.execute("select count(*) from demo_orders o join decisions d on d.id=o.decision_id join broker_confirmed_results r on r.demo_order_id=o.id where o.status='EXECUTED' and r.result in ('WIN','LOSS','DRAW','WIN_GALE','LOSS_GALE')").fetchone())
q="""select substr(coalesce(r.closed_at,o.executed_at,o.requested_at),1,10) day,count(*) n,
 sum(case when r.result in ('WIN','WIN_GALE') then 1 else 0 end) wins,
 sum(case when r.result in ('LOSS','LOSS_GALE') then 1 else 0 end) losses,
 sum(r.pnl_minor) pnl
 from demo_orders o join decisions d on d.id=o.decision_id join broker_confirmed_results r on r.demo_order_id=o.id
 where o.status='EXECUTED' and r.result in ('WIN','LOSS','DRAW','WIN_GALE','LOSS_GALE') group by day order by day desc"""
for row in c.execute(q): print('DAY',row)
print('schema demo_orders', c.execute('pragma table_info(demo_orders)').fetchall())
