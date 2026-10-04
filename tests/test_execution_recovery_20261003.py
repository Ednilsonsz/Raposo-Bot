import unittest,sqlite3,tempfile,sys,threading,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from bullex_live_capture import BullexLiveCapture
class RecoveryTests(unittest.TestCase):
 def test_capture_releases_writer_before_result_connection(self):
  with tempfile.TemporaryDirectory() as folder:
   a=sqlite3.connect(str(Path(folder)/'db'));b=sqlite3.connect(str(Path(folder)/'db'),timeout=.1)
   a.execute('CREATE TABLE proof(value TEXT)');a.commit();a.execute("INSERT INTO proof VALUES('capture')")
   class Results:
    def register_source(self,*args):
     b.execute("INSERT INTO proof VALUES('result')");b.commit()
    def observe(self,*args,**kwargs):pass
   cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.db=a;cap.results=Results();cap.ws_urls={'s':'wss://api.bull-ex.com/ws'};cap._result_socket_prefix='test'
   cap._observe_trade_frame({'name':'test'},True,{'requestId':'s'},{'timestamp':time.time()*1000},'{}')
   self.assertEqual(b.execute('SELECT COUNT(*) FROM proof').fetchone()[0],2);a.close();b.close()
 def test_provider_releases_catalog_writer(self):
  with tempfile.TemporaryDirectory() as folder:
   a=sqlite3.connect(str(Path(folder)/'db'));b=sqlite3.connect(str(Path(folder)/'db'),timeout=.1)
   a.execute('CREATE TABLE proof(value TEXT)');a.commit()
   cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.db=a;cap._selected_asset={'asset_id':'79'}
   cap.refresh_selected_asset_label=lambda **kwargs:a.execute("INSERT INTO proof VALUES('catalog')")
   cap.selected_asset_snapshot(force=True)
   b.execute("INSERT INTO proof VALUES('executor')");b.commit();self.assertEqual(b.execute('SELECT COUNT(*) FROM proof').fetchone()[0],2);a.close();b.close()
 def test_unknown_expiry_never_clicks_without_exact_target(self):
  from types import SimpleNamespace
  from unittest.mock import patch
  from expiry_recovery_r12 import restore_step
  class Driver:
   def __init__(self):self.sent=[]
   def execute_script(self,*args):return None
   def execute_cdp_cmd(self,*args):self.sent.append(args)
  d=Driver();e=SimpleNamespace(d=d,cfg={'demo_only':True},_control_read_diagnostic={'expiry':None},_loss_gate=lambda:(True,{}),session_valid=lambda:True,disarm=lambda:None)
  with patch('expiry_recovery_r12.visual_target',return_value=None):self.assertFalse(restore_step(e))
  self.assertEqual(d.sent,[])
 def test_exact_five_seconds_restores_but_requires_independent_confirmation(self):
  from types import SimpleNamespace
  from expiry_recovery_r12 import restore_step
  class Driver:
   def __init__(self):self.sent=[]
   def execute_script(self,*args):return {'x':1250,'y':260,'label':'5 seg' if args[-1]=='open' else '1 min'}
   def execute_cdp_cmd(self,*args):self.sent.append(args)
  d=Driver();e=SimpleNamespace(d=d,cfg={'demo_only':True},_control_read_diagnostic={'expiry':None},_loss_gate=lambda:(True,{}),session_valid=lambda:True,disarm=lambda:None)
  self.assertTrue(restore_step(e));self.assertTrue(restore_step(e))
  self.assertEqual(len(d.sent),4);self.assertFalse(e._expiry_1m_confirmed_by_code)
 def test_async_capture_preserves_raw_frame_without_driver_call(self):
  import test_broker_results_r6
  from broker_results_r11 import BrokerResults
  base=test_broker_results_r6.Tests();base.setUp()
  db=sqlite3.connect(':memory:',check_same_thread=False);base.db.backup(db)
  manager=BrokerResults(db,{'risk_return':{'enabled':True,'activated_at':'2026-10-03T00:00:00'}})
  cap=BullexLiveCapture.__new__(BullexLiveCapture);cap.results=manager;cap.db=None;cap.ws_urls={'s':'wss://api.bull-ex.com/ws'};cap._result_socket_prefix='async'
  raw=json.dumps(base.close());cap._observe_trade_frame(base.close(),True,{'requestId':'s'},{'timestamp':time.time()*1000},raw)
  self.assertEqual(db.execute('SELECT COUNT(*) FROM broker_trade_frames').fetchone()[0],0)
  manager.schedule_reconcile();manager._metadata_worker.join(5)
  self.assertFalse(manager._metadata_worker.is_alive());self.assertEqual(db.execute('SELECT payload FROM broker_trade_frames').fetchone()[0],raw)
  db.close();base.db.close()
 def test_legacy_policy_disabled(self):
  config=json.loads((Path(__file__).parents[1]/'config/config.json').read_text())
  self.assertFalse(config['score_engine']['daily_setup_block']['enabled'])
  self.assertFalse(config['experimental_modules']['veto_opposite'])
  self.assertTrue(config['risk_return']['replace_legacy_gates'])
if __name__=='__main__':unittest.main()
