import sqlite3, json
p=r'L:\Bullex_Robo\Raposo_Data\database\candles.db'
db=sqlite3.connect(p)
print('TABLES', [x[0] for x in db.execute("select name from sqlite_master where type='table' order by name")])
for t in ('demo_orders','broker_confirmed_results','decisions'):
    try: print('COLUMNS',t,db.execute('pragma table_info('+t+')').fetchall())
    except Exception as e: print('ERR',t,e)
for q in [
"select * from demo_orders order by id desc limit 15",
"select * from broker_confirmed_results order by rowid desc limit 15",
"select id,cycle_start,candle_exec,status,result,requested_at,executed_at,updated_at from decisions order by id desc limit 15"]:
    try:
        print('QUERY',q)
        for r in db.execute(q): print(r)
    except Exception as e: print('QUERY_ERR',e)
