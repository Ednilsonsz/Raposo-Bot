import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import contextlib,io,json,sqlite3,time,unittest
from datetime import datetime,timedelta
from test_broker_results_r6 import Tests as BaseTests
from broker_day_r11 import today,closed_time,day_of
from broker_results_r11 import scoreboard

class HistoryTests(BaseTests):
 # Keep only dedicated history tests in this subclass to avoid duplicate inherited suite.
 def page(self,event=None):
  return {'name':'history-positions','request_id':'raposo-history:test','msg':{'positions':[{'id':987,'raw_event':event or self.close()['msg'],'future_field':{'audit':'preserve'}}]}}
 def history(self,event=None):
  p=self.page(event);raw=json.dumps(p,indent=2);self.m.observe(p,True,'sock',self.now,raw);return raw
 def link(self):
  self.intent();self.send(self.request(),False);self.send(self.ack());self.run_reconcile()
 def test_history_linked_recovers_real_settlement(self):
  self.link();raw=self.history();self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1)
  self.assertEqual(self.db.execute('SELECT evidence FROM broker_confirmed_results').fetchone()[0],raw)
  self.assertEqual(self.db.execute("SELECT broker_operation_id FROM broker_operation_audit WHERE message_name='history-positions'").fetchone()[0],'987')
  self.assertIn('future_field',self.db.execute('SELECT raw_payload FROM broker_history_operations').fetchone()[0])
 def test_history_before_ack_recovers_after_link(self):
  self.history();self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],0);self.link();self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_history_recovers_order_when_local_order_row_was_not_saved(self):
  self.db.execute('DELETE FROM demo_orders WHERE id=1');self.db.commit()
  self.history();self.run_reconcile()
  self.assertEqual(scoreboard(self.db)['WIN'],1)
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM broker_order_links').fetchone()[0],1)
 def test_unlinked_legacy_never_matches_by_time_stake(self):
  self.history();self.run_reconcile();self.assertEqual(scoreboard(self.db)['pending'],1);self.assertEqual(scoreboard(self.db)['WIN'],0)
 def test_history_duplicates_and_reconnect_once(self):
  self.link();raw=self.history();self.run_reconcile();self.history();self.m.observe(self.page(),True,'reconnected',self.now,raw);self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],1)
 def test_history_previous_day_not_added(self):
  self.link();event=self.close()['msg'];event['actual_expire']=self.now-86400;self.history(event);self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],0)
 def test_history_future_not_confirmed(self):
  self.link();event=self.close()['msg'];event['actual_expire']=self.now+180;self.history(event);self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],0)
 def test_history_malformed_keeps_raw(self):
  self.link();raw=self.history({'pnl':8.8});self.run_reconcile();self.assertEqual(scoreboard(self.db)['WIN'],0);self.assertEqual(self.db.execute('SELECT raw_payload FROM broker_history_pages').fetchone()[0],raw)
 def test_day_uses_sao_paulo_not_utc(self):
  self.assertEqual(day_of('2026-09-17T02:59:59+00:00'),'2026-09-16');self.assertEqual(day_of('2026-09-17T03:00:00+00:00'),'2026-09-17')
 def test_previous_day_ledger_excluded_without_deletion(self):
  self.full();self.db.execute('UPDATE broker_confirmed_results SET closed_at=?',(closed_time(self.now-86400),));self.db.commit();self.assertEqual(scoreboard(self.db)['WIN'],0);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM broker_confirmed_results').fetchone()[0],1)
 def test_pending_previous_day_excluded(self):
  self.db.execute('UPDATE demo_orders SET executed_at=?',(closed_time(self.now-86400),));self.db.commit();self.assertEqual(scoreboard(self.db)['pending'],0)
 def test_history_conflict_does_not_overwrite(self):
  self.link();self.history();self.run_reconcile();self.history(self.close(result='loose',gross=0)['msg']);self.run_reconcile();self.assertEqual(scoreboard(self.db)['money'],{'USD':880})

# Shared setup/helpers, not inherited tests.
for name in dir(BaseTests):
 if name.startswith('test_') and name not in HistoryTests.__dict__:setattr(HistoryTests,name,None)
del BaseTests
if __name__=='__main__':unittest.main()


