import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import ast,contextlib,io,json,sqlite3,sys,time,unittest,tempfile,hashlib
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "app"))
from broker_results_r11 import BrokerResults,scoreboard
from demo_executor_v386_r11 import DemoExecutor
SCHEMA='''CREATE TABLE decisions(id INTEGER PRIMARY KEY,created_at TEXT,updated_at TEXT,asset_id TEXT,cycle_start INTEGER,c1_dir TEXT,prediction TEXT,result TEXT,status TEXT,decision_mode TEXT,c2_dir TEXT,c3_dir TEXT);
CREATE TABLE demo_orders(id INTEGER PRIMARY KEY,decision_id INTEGER,asset_id TEXT,cycle_start INTEGER,leg TEXT,direction TEXT,stake REAL,requested_at TEXT,executed_at TEXT,status TEXT,error TEXT,UNIQUE(decision_id,leg));
CREATE TABLE candles(timestamp INTEGER,asset_id TEXT,timeframe INTEGER,open REAL,high REAL,low REAL,close REAL);
CREATE TABLE bullex_live_state(asset_id TEXT,current_from INTEGER,current_to INTEGER,broker_at REAL,last_received_at TEXT);'''
class Tests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.executescript(SCHEMA);self.m=BrokerResults(self.db)
  self.now=time.time();self.stamp=datetime.now().isoformat();self.insert_order()
 def tearDown(self):self.db.close()
 def insert_order(self,oid=1,did=10,leg='SIG',direction='G',status='EXECUTED'):
  self.db.execute('INSERT OR IGNORE INTO decisions(id,created_at,updated_at,asset_id,cycle_start,c1_dir,prediction,result,status,decision_mode) VALUES(?,?,?,?,?,?,?,?,?,?)',(did,self.stamp,self.stamp,'86',int(self.now),'DP',direction,None,status,'STRATEGY'))
  self.db.execute('''INSERT INTO demo_orders
    (id,decision_id,asset_id,cycle_start,leg,direction,stake,requested_at,executed_at,status,error)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(oid,did,'86',int(self.now),leg,direction,10,self.stamp,self.stamp if status=='EXECUTED' else None,status,None));self.db.commit()
 def intent(self,oid=1,did=10,leg='SIG',name='intent'):
  self.db.execute('INSERT INTO broker_execution_intents VALUES(?,?,?,?,?,?,?,?,?)',(name,did,leg,'86','G',1000,self.now-65,self.now-63,oid));self.db.commit()
 def request(self,rid='req',price=10,account=42):return {'name':'sendMessage','request_id':rid,'msg':{'name':'binary-options.open-option','version':'1.0','body':{'price':price,'active_id':86,'expired':int(self.now)-3,'direction':'call','user_balance_id':account,'option_type_id':3}}}
 def ack(self,rid='req',bid=987):return {'name':'option','request_id':rid,'msg':{'id':bid,'active_id':86,'user_balance_id':42}}
 def close(self,bid=987,result='win',gross=18.8):return {'name':'option-closed','msg':{'option_id':bid,'balance_id':42,'active_id':86,'direction':'call','amount':10,'profit_amount':gross,'currency':'USD','result':result,'expiration_time':int(self.now)-3,'actual_expire':int(self.now)-3,'open_time':int(self.now)-64,'profit_percent':188,'future_vendor_field':{'retain':[1,2,'audit']}}}
 def position_close(self,bid=987,result='win',gross=18.8):
  event=dict(self.close(bid,result,gross)['msg'],option_type='blitz')
  return {'name':'position-changed','microserviceName':'portfolio','msg':{'raw_event':{'binary_options_option_changed1':event},'instrument_type':'blitz-option','status':'closed'}}
 def send(self,obj,received=True,socket='sock',raw=None):self.m.observe(obj,received,socket,self.now-64,raw)
 def run_reconcile(self):
  with contextlib.redirect_stdout(io.StringIO()):self.m.reconcile()
 def full(self,close=None):
  self.intent();self.send(self.request(),False);self.send(self.ack());self.send(close or self.close());self.run_reconcile()
 def test_execution_time_comes_from_broker_ack(self):
  from broker_day_r11 import closed_time
  self.db.execute("UPDATE demo_orders SET status='SUBMITTED'");self.db.commit()
  self.intent();self.send(self.request(),False)
  ack=self.ack();ack['msg']['created_millisecond']=int((self.now-64)*1000)
  self.send(ack);self.run_reconcile()
  self.assertEqual(self.db.execute('SELECT executed_at FROM demo_orders WHERE id=1').fetchone()[0],closed_time(ack['msg']['created_millisecond']/1000))
 def test_mobile_ignores_theoretical_results_and_uses_real_money(self):
  import mobile_server
  self.db.execute('CREATE TABLE score_signals(id INTEGER,setup TEXT,side TEXT,score REAL,outcome TEXT)')
  self.db.execute("UPDATE decisions SET result='WIN'");self.db.commit()
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'test.db'
   def status():
    with contextlib.closing(sqlite3.connect(path)) as target:self.db.backup(target)
    with patch.object(mobile_server,'DB',str(path)):
     return mobile_server.MobileControlServer({'payout':999}).status()
   pending=status();self.assertNotIn('db_error',pending);self.assertEqual((pending['wins'],pending['pl'],pending['pending']),(0,0,1));self.assertIn('UNKNOWN',pending['last'])
   self.full();settled=status();self.assertEqual((settled['wins'],settled['pl'],settled['currency']),(1,8.8,'USD'))
 def test_full_pipeline_and_ledger(self):
  self.full();s=scoreboard(self.db);self.assertEqual((s['WIN'],s['money'],s['pending']),(1,{'USD':880},0));self.assertEqual(self.db.execute('SELECT result,status FROM decisions').fetchone(),('WIN','FINALIZED'))
 def test_blitz_position_changed_closes_and_validates_internal_asset_table(self):
  self.db.execute("UPDATE demo_orders SET asset_name='EURUSD-OTC'");self.db.commit()
  self.full(self.position_close())
  self.assertEqual(scoreboard(self.db)['WIN'],1)
  self.assertEqual(self.db.execute('SELECT ID_BULLEX,NOME FROM ativos').fetchone(),('86','EURUSD-OTC'))
  self.assertFalse(self.db.execute("SELECT 1 FROM raposo_result_events WHERE payload LIKE '%UNSUPPORTED_BROKER_SCHEMA%'").fetchone())
 def test_pending_before_close(self):
  self.intent();self.send(self.request(),False);self.send(self.ack());self.run_reconcile();self.assertEqual(scoreboard(self.db)['pending'],1);self.assertIsNone(self.db.execute('SELECT result FROM decisions').fetchone()[0])
 def test_real_loss_money(self):
  self.full(self.close(result='loose',gross=0));s=scoreboard(self.db);self.assertEqual((s['LOSS'],s['money']),(1,{'USD':-1000}))
 def test_draw_not_win_loss(self):
  self.full(self.close(result='equal',gross=10));s=scoreboard(self.db);self.assertEqual((s['DRAW'],s['WIN'],s['LOSS'],s['money']),(1,0,0,{'USD':0}))
 def test_raw_payload_exact_preserved(self):
  raw=json.dumps(self.close(),indent=4,ensure_ascii=False);self.intent();self.send(self.request(),False);self.send(self.ack());self.send(self.close(),raw=raw);self.run_reconcile()
  self.assertEqual(self.db.execute('SELECT evidence FROM broker_confirmed_results').fetchone()[0],raw)
  self.assertEqual(self.db.execute("SELECT raw_payload FROM broker_operation_audit WHERE message_name='option-closed'").fetchone()[0],raw)
  self.assertIn('future_vendor_field',raw)
 def test_duplicate_and_reconnect(self):
  self.full();self.send(self.close());self.send(self.close(),socket='new-socket');self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raposo_result_events WHERE kind='RESULT_CONFIRMED'").fetchone()[0],1)
 def test_restart_persistence(self):
  self.full();other=BrokerResults(self.db)
  with contextlib.redirect_stdout(io.StringIO()):other.reconcile()
  self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_closed_before_ack_is_reconciled_later(self):
  self.intent();self.send(self.close());self.run_reconcile();self.assertEqual(scoreboard(self.db)['pending'],1)
  self.send(self.request(),False);self.send(self.ack());self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_legacy_orders_without_ids_remain_unknown(self):
  self.send(self.close());self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],0);self.assertEqual(scoreboard(self.db)['pending'],1)
 def test_manual_unmatched_operation_ignored(self):
  self.full();self.send(self.close(bid=999));self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_wrong_amount_or_direction_or_currency_or_pending(self):
  for field,value in [('amount',20),('direction','put'),('currency',''),('profit_amount',None),('result','pending'),('active_id',87),('expiration_time',self.now+90)]:
   with self.subTest(field=field):
    self.db.execute('DELETE FROM broker_confirmed_results');self.db.execute('DELETE FROM broker_trade_frames');self.db.execute('DELETE FROM broker_order_links');self.db.execute('DELETE FROM broker_execution_intents');self.db.commit()
    obj=self.close();obj['msg'][field]=value
    if field=='expiration_time':obj['msg']['actual_expire']=value
    self.full(obj);self.assertEqual(scoreboard(self.db)['WIN'],0)
 def test_mismatched_result_and_money(self):
  self.full(self.close(result='win',gross=0));self.assertEqual(scoreboard(self.db)['pending'],1)
 def test_wrong_account_id(self):
  obj=self.close();obj['msg']['balance_id']=99;self.full(obj);self.assertEqual(scoreboard(self.db)['pending'],1)
 def test_ambiguous_requests_not_linked(self):
  self.intent();self.send(self.request(),False);self.send(self.request('second'),False);self.send(self.ack());self.send(self.close());self.run_reconcile();self.assertEqual(scoreboard(self.db)['pending'],1)
 def test_two_intents_one_request_not_linked(self):
  self.intent();self.insert_order(2,11);self.intent(2,11,name='second');self.send(self.request(),False);self.send(self.ack());self.send(self.close());self.run_reconcile();self.assertEqual(scoreboard(self.db)['pending'],2)
 def test_conflicting_result_is_audited_not_overwritten(self):
  self.full();self.send(self.close(result='loose',gross=0));self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1)
  self.assertTrue(self.db.execute("SELECT 1 FROM raposo_result_events WHERE payload LIKE '%CONFLICTING_CLOSED_EVENT%'").fetchone())
 def test_unknown_schema_retains_payload_and_blocks(self):
  obj={'name':'position-changed','msg':{'id':'blitz-1','status':'closed','unknown':{'all':'kept'}}};self.send(obj);self.run_reconcile();self.assertIn('unknown',self.db.execute('SELECT payload FROM broker_trade_frames').fetchone()[0]);self.assertEqual(scoreboard(self.db)['pending'],1)
 def test_auth_messages_not_stored(self):
  self.send({'name':'profile','msg':{'token':'secret'}});self.assertEqual(self.db.execute('SELECT COUNT(*) FROM broker_trade_frames').fetchone()[0],0)
 def test_candle_and_theory_do_not_change_scoreboard(self):
  self.db.execute("UPDATE decisions SET result='WIN'");self.db.execute("INSERT INTO candles VALUES(1,'86',60,1,3,1,3)");self.db.commit();self.assertEqual(scoreboard(self.db)['WIN'],0)
 def test_gale_structure(self):
  self.db.execute("UPDATE demo_orders SET leg='C3'");self.db.commit();self.intent(leg='C3');self.send(self.request(),False);self.send(self.ack());self.send(self.close());self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN_GALE'],1)
 def test_notice_exactly_once_on_executed_replace(self):
  self.db.execute("INSERT OR REPLACE INTO demo_orders SELECT * FROM demo_orders WHERE id=1");self.db.commit();self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raposo_result_events WHERE kind='EXECUTED'").fetchone()[0],1)
 def test_observer_preserves_arguments_return_and_exceptions(self):
  seen=[]
  class E:
   def execute(e,*args,**kwargs):seen.append((args,kwargs));return 'same-result'
  e=E();self.m.observe_executor(e);self.assertEqual(e.execute(10,'86',123,'SIG','G',10,immediate=True),'same-result');self.assertEqual(seen,[((10,'86',123,'SIG','G',10),{'immediate':True})])
  class F:
   def execute(e,*args,**kwargs):raise RuntimeError('original-error')
  e=F();self.m.observe_executor(e)
  with self.assertRaisesRegex(RuntimeError,'original-error'):e.execute(10,'86',123,'SIG','G',10)
 def test_overlay_snapshot_and_daily_money(self):
  self.full()
  from ui_overlay_massa_v386_r11 import BullexOverlay
  o=BullexOverlay.__new__(BullexOverlay);o.db=self.db;o.asset='86';o.asset_label='EURUSD-OTC';o.analyzer=None;o.cfg={'_user_paused':True};o.driver=SimpleNamespace(execute_script=lambda *a:None);o._log_tail=lambda n=250:[];o._history_cache_key=None;o._history_cache_at=0;o._history_cache=[]
  s=o._snapshot();self.assertEqual((s['wins'],s['losses'],s['pl'],s['plnum']),(1,0,'USD +8.80',8.8));self.assertEqual(s['notifications'][0]['order_id'],1)
 def test_existing_executed_is_not_attributed_again(self):
  class E:
   def execute(e,*args,**kwargs):return False
  e=E();self.m.observe_executor(e);e.execute(10,'86',123,'SIG','G',10)
  self.run_reconcile()
  self.assertIsNone(self.db.execute('SELECT demo_order_id FROM broker_execution_intents').fetchone()[0])
 def test_single_capture_consumer_pipeline(self):
  from bullex_live_capture import BullexLiveCapture
  self.intent();objects=[(False,self.request()),(True,self.ack()),(True,self.close())]
  logs=[]
  for received,obj in objects:
   logs.append({'timestamp':(self.now-64)*1000,'message':json.dumps({'message':{'method':'Network.webSocketFrameReceived' if received else 'Network.webSocketFrameSent','params':{'requestId':'socket123','response':{'opcode':1,'payloadData':json.dumps(obj)}}}})})
  class D:
   calls=0
   def get_log(d,name):d.calls+=1;return logs
  cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.d=D();cap.results=self.m;cap._result_socket_prefix='test';cap.refresh_selected_asset_label=lambda force=False:None;cap.last_console=time.time();cap.ws_urls={'socket123':'wss://ws.bull-ex.com/echo/websocket'};cap.db=self.db
  with contextlib.redirect_stdout(io.StringIO()):cap.poll()
  self.assertEqual(cap.d.calls,1);self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_persisted_file_reopened(self):
  self.full()
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'ledger.db';c=sqlite3.connect(path);self.db.backup(c);c.close()
   c=sqlite3.connect(path);manager=BrokerResults(c)
   with contextlib.redirect_stdout(io.StringIO()):manager.reconcile()
   self.assertEqual(scoreboard(c)['money'],{'USD':880});c.close()
 def test_real_record_commit_creates_notice(self):
  self.db.execute('DELETE FROM demo_orders');self.db.execute('DELETE FROM raposo_result_events');self.db.commit()
  executor=DemoExecutor.__new__(DemoExecutor);executor.db=self.db;executor.cfg={'build_revision':'V3.86 · R11'};executor._current_asset_name=None;executor.selected_asset_provider=None
  executor._record(10,'86',123,'SIG','G',10,'EXECUTED')
  self.assertEqual(self.db.execute("SELECT kind FROM raposo_result_events").fetchone()[0],'EXECUTED')
  self.assertIsNone(self.db.execute('SELECT result FROM decisions').fetchone()[0])
 def test_confirmed_ledger_is_not_erased_by_mutable_order_status(self):
  self.full();self.db.execute("UPDATE demo_orders SET status='ERROR'");self.db.commit()
  self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_missing_event_time_still_preserves_raw_payload(self):
  from bullex_live_capture import BullexLiveCapture
  cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.results=self.m;cap._result_socket_prefix='test';cap.ws_urls={'s':'wss://ws.bull-ex.com/echo/websocket'}
  raw=json.dumps(self.close(),indent=2)
  cap._observe_trade_frame(self.close(),True,{'requestId':'s'},{},raw)
  self.assertEqual(self.db.execute('SELECT payload FROM broker_trade_frames').fetchone()[0],raw)
 def test_loss_gale_structure(self):
  self.db.execute("UPDATE demo_orders SET leg='C3'");self.db.commit();self.intent(leg='C3');self.send(self.request(),False);self.send(self.ack());self.send(self.close(result='loose',gross=0));self.run_reconcile()
  self.assertEqual(scoreboard(self.db)['LOSS_GALE'],1)
 def test_foreign_websocket_cannot_confirm_results(self):
  from bullex_live_capture import BullexLiveCapture
  cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.results=self.m;cap._result_socket_prefix='test';cap.ws_urls={'s':'wss://attacker.example/ws'}
  raw=json.dumps(self.close());cap._observe_trade_frame(self.close(),True,{'requestId':'s'},{'timestamp':time.time()*1000},raw)
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM broker_trade_frames').fetchone()[0],0)
 def test_executor_source_integrity(self):
  expected={'score_engine_v386_r11.py':'87331a970d8c5fc1a086c9352cc0fafd1759da49462aedef475db0b32fee65a6','adaptive_position_v386_r11.py':'5b0eabb0a9d268e0ceee8c6541a8d61cdb294ea84441afd153b6bc9f472c9acc','strategy_modules_v386_r11.py':'0e3d2c0a91de65952890b0edd90a76b36e2af1e12432a490c7bed99ee4954c37'}
  for name in expected:
   self.assertEqual(hashlib.sha256((ROOT/'app'/name).read_bytes()).hexdigest(),expected[name])
if __name__=='__main__':unittest.main(verbosity=2)


