import sys,unittest,time,json
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
sys.path.insert(0,str(Path(__file__).parent))
from test_broker_results_r6 import Tests
from broker_results_r11 import reconciliation_frames
from broker_history_r11 import active_history_rows
class ActiveRecoveryTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=getattr(Tests,'tearDown',unittest.TestCase.tearDown)
 insert_order=Tests.insert_order
 intent=Tests.intent
 request=Tests.request
 ack=Tests.ack
 close=Tests.close
 send=Tests.send
 run_reconcile=Tests.run_reconcile
 full=Tests.full
 def activate(self):self.m.config={'risk_return':{'enabled':True,'activated_at':(datetime.now()-timedelta(hours=2)).isoformat()}}
 def test_active_direct_close_still_settles(self):
  self.activate();self.full()
  self.assertEqual(self.db.execute('select result from broker_confirmed_results where demo_order_id=1').fetchone()[0],'WIN')
 def test_older_unresolved_request_kept_and_archive_excluded(self):
  self.activate();self.intent()
  self.db.execute('UPDATE broker_execution_intents SET started=?,finished=?',(self.now-900,self.now-895));self.db.commit()
  self.m.observe(self.request(),False,'sock',self.now-899)
  self.m.observe(self.ack(),True,'sock',self.now-898)
  self.m.observe(self.close(),True,'sock',self.now-86400)
  self.assertEqual(len(reconciliation_frames(self.m)),2)
 def test_history_only_queries_matching_account_and_id(self):
  self.activate()
  self.db.execute("INSERT INTO broker_order_links VALUES(1,'42','987','s','r','i')")
  from broker_history_r11 import ingest_history
  item=self.close()['msg'];envelope={'name':'history-positions','msg':{'positions':[item]}}
  ingest_history(self.m,envelope,'s')
  self.db.commit()
  self.assertEqual(len(active_history_rows(self.m)),1)
  self.db.execute("UPDATE broker_order_links SET account_id='99'");self.db.commit()
  self.assertEqual(active_history_rows(self.m),[])
 def test_no_pending_no_archive_recovery(self):
  self.activate();self.full()
  self.assertEqual(active_history_rows(self.m),[])
 def test_stale_label_recovered_only_with_request_and_ack(self):
  from asset_catalog import ensure_schema,resolve
  ensure_schema(self.db);resolve(self.db,'AUDCAD-OTC','86')
  self.intent();self.db.execute("UPDATE broker_execution_intents SET asset_id='76'")
  self.db.execute("UPDATE demo_orders SET asset_id='76',asset_name='EURUSD-OTC'");self.db.commit()
  self.send(self.request(),False);self.send(self.ack());self.send(self.close(result='loose',gross=0));self.run_reconcile()
  self.assertEqual(self.db.execute('SELECT asset_id,asset_name FROM demo_orders WHERE id=1').fetchone(),('86','AUDCAD-OTC'))
  self.assertEqual(self.db.execute('SELECT result FROM broker_confirmed_results WHERE demo_order_id=1').fetchone()[0],'LOSS')
if __name__=='__main__':unittest.main()
