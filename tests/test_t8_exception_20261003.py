import sys,unittest,sqlite3,tempfile
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from turn_loss_control_r12 import effective_shift_settings,TurnLossController
from broker_day_r11 import SAO_PAULO
class T8Tests(unittest.TestCase):
 def setUp(self):
  self.cfg={'demo_only':True,'loss_control':{'count_from_shift_start':True,'shifts':{f'T{i}':{'mode':'LIMIT','limit':2} for i in range(1,9)},'dated_overrides':{'2026-10-03':{'T8':{'mode':'FREE','limit':None}}}}}
 def test_exact_date_shift_and_demo(self):
  today=datetime(2026,10,3,22,tzinfo=SAO_PAULO)
  self.assertEqual(effective_shift_settings(self.cfg,'T8',today)['mode'],'FREE')
  self.assertEqual(effective_shift_settings(self.cfg,'T7',today)['mode'],'LIMIT')
  self.assertEqual(effective_shift_settings(self.cfg,'T8',today.replace(day=4))['mode'],'LIMIT')
  self.cfg['demo_only']=False
  self.assertEqual(effective_shift_settings(self.cfg,'T8',today)['mode'],'LIMIT')
 def test_controller_reports_free_and_restores_tomorrow(self):
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE broker_confirmed_results(closed_at TEXT,result TEXT)')
  db.executemany('INSERT INTO broker_confirmed_results VALUES(?,?)',[(f'2026-10-03T21:0{i}:00-03:00','LOSS') for i in range(3)])
  with tempfile.TemporaryDirectory() as d:
   c=TurnLossController(db,self.cfg,state_path=Path(d)/'state.json')
   s=c.snapshot(datetime(2026,10,3,22,tzinfo=SAO_PAULO))
   self.assertTrue(s['allowed']);self.assertIsNone(s['limit']);self.assertEqual(s['losses'],3)
   s=c.snapshot(datetime(2026,10,4,22,tzinfo=SAO_PAULO));self.assertEqual(s['limit'],2)
  db.close()
if __name__=='__main__':unittest.main()
