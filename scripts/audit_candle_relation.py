import sqlite3,datetime,json
p=r'L:\Bullex_Robo\Raposo_Data\database\candles.db'; db=sqlite3.connect(p)
def fmt(x):
 try:return datetime.datetime.fromtimestamp(int(x),datetime.timezone(datetime.timedelta(hours=-3))).strftime('%Y-%m-%d %H:%M:%S')
 except:return str(x)
q='''select o.id,o.cycle_start,o.candle_exec,o.requested_at,o.executed_at,o.status,r.result,r.closed_at,r.evidence from demo_orders o left join broker_confirmed_results r on r.demo_order_id=o.id where o.status in ('EXECUTED','SUBMITTED') order by o.id desc limit 20'''
for row in db.execute(q):
 oid,cycle,candle,req,exe,status,result,closed,evidence=row
 exp=None; open_time=None; actual=None
 try:
  d=json.loads(evidence or '{}'); raw=d.get('msg',{}).get('raw_event',{}).get('binary_options_option_changed1',{}) or d.get('raw_event',{})
  open_time=raw.get('open_time'); exp=raw.get('expiration_time'); actual=raw.get('actual_expire')
 except: pass
 print({'id':oid,'cycle':fmt(cycle),'candle_exec':candle,'requested':req,'executed':exe,'status':status,'result':result,'open':fmt(open_time) if open_time else None,'expire':fmt(exp) if exp else None,'actual_expire':fmt(actual) if actual else None,'closed':closed})
