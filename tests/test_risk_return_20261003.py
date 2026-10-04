import unittest,sqlite3,sys,json,tempfile,ast,time
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from broker_day_r11 import SAO_PAULO
from risk_return_r12 import daily_gate,entry_gate
from turn_loss_control_r12 import TurnLossController
class RiskTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.now=datetime(2026,10,3,10,0,tzinfo=SAO_PAULO)
  self.db.executescript('CREATE TABLE demo_orders(id INTEGER,decision_id INTEGER,status TEXT,requested_at TEXT); CREATE TABLE decisions(id INTEGER,c1_dir TEXT); CREATE TABLE broker_confirmed_results(demo_order_id INTEGER,closed_at TEXT,result TEXT,pnl_minor INTEGER,stake_minor INTEGER,currency TEXT,evidence TEXT);')
  self.cfg={'demo_stake':10,'risk_return':{'enabled':True,'daily_net_loss_minor':4000,'currency':'USD','activated_at':self.now.isoformat(),'single_open_order':True},'loss_control':{'count_from_shift_start':True,'shifts':{f'T{i}':{'mode':'LIMIT','limit':2} for i in range(1,9)}}}
 def tearDown(self):
  self.db.close()
 def add(self,i,result,pnl,stamp=None):
  self.db.execute('INSERT INTO demo_orders VALUES(?,?,?,?)',(i,i,'EXECUTED',(stamp or self.now-timedelta(minutes=2)).isoformat()))
  self.db.execute('INSERT INTO decisions VALUES(?,?)',(i,'DIDI'))
  self.db.execute('INSERT INTO broker_confirmed_results VALUES(?,?,?,?,?,?,?)',(i,(stamp or self.now-timedelta(minutes=1)).isoformat(),result,pnl,1000,'USD',json.dumps({'expiration_size':60})))
 def test_daily_latches_even_after_win_and_restart(self):
  for i in range(4):self.add(i,'LOSS',-1000,self.now-timedelta(minutes=5-i))
  self.add(5,'WIN',880,self.now-timedelta(seconds=5))
  self.assertFalse(daily_gate(self.db,self.cfg,self.now)[0])
  self.assertFalse(daily_gate(self.db,self.cfg,self.now)[0])
  self.assertTrue(daily_gate(self.db,self.cfg,self.now+timedelta(days=1))[0])
 def test_two_accumulated_losses_win_does_not_reset(self):
  self.add(1,'LOSS',-1000);self.add(2,'WIN',880);self.add(3,'LOSS',-1000)
  with tempfile.TemporaryDirectory() as folder:
   c=TurnLossController(self.db,self.cfg,state_path=Path(folder)/'state.json')
   self.assertFalse(c.allows_new_entries(self.now)[0]);self.assertTrue(c.allows_new_entries(self.now+timedelta(hours=3))[0])
 def test_pending_blocks(self):
  self.db.execute('INSERT INTO demo_orders VALUES(1,1,?,?)',('SUBMITTED',self.now.isoformat()))
  self.assertFalse(entry_gate(self.db,self.cfg,'DIDI',self.now)[0])
 def test_reserve_full_stake(self):
  self.add(1,'LOSS',-3500)
  self.assertFalse(entry_gate(self.db,self.cfg,'DIDI',self.now)[0])
 def test_positive_real_roi_required(self):
  self.cfg['risk_return']['profitable_setup_required']=True
  self.assertFalse(entry_gate(self.db,self.cfg,'DIDI',self.now)[0])
  self.add(1,'WIN',880);self.assertTrue(entry_gate(self.db,self.cfg,'DIDI',self.now)[0])
  self.add(2,'LOSS',-1000);self.assertFalse(entry_gate(self.db,self.cfg,'DIDI',self.now)[0])
 def test_tg_demo_bootstrap_bounded_and_guards_preserved(self):
  self.cfg['risk_return']['profitable_setup_required']=True
  self.cfg['estrategy_tg']={'enabled':True,'bootstrap_enabled':True,'bootstrap_operations':10}
  self.cfg['demo_only']=False
  self.assertFalse(entry_gate(self.db,self.cfg,'Estrategy_TG',self.now)[0])
  self.cfg['demo_only']=True
  self.assertTrue(entry_gate(self.db,self.cfg,'Estrategy_TG',self.now)[0])
  self.db.execute('INSERT INTO demo_orders VALUES(99,99,?,?)',('SUBMITTED',self.now.isoformat()))
  self.assertFalse(entry_gate(self.db,self.cfg,'Estrategy_TG',self.now)[0])
  self.db.execute('DELETE FROM demo_orders WHERE id=99')
  for i in range(10):self.add(i,'LOSS',-10)
  self.db.execute("UPDATE decisions SET c1_dir='Estrategy_TG'")
  self.assertFalse(entry_gate(self.db,self.cfg,'Estrategy_TG',self.now)[0])
 def test_late_click_never_passes(self):
  tree=ast.parse((Path(__file__).parents[1]/'app/demo_executor_v386_r11.py').read_text(encoding='utf-8'))
  method=next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name=='_assert_entry_deadline')
  ns={'time':time,'datetime':datetime};exec(ast.unparse(ast.Module(body=[ast.ClassDef(name='Probe',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[])),ns)
  p=ns['Probe']();p._entry_click_deadline=time.monotonic()-1
  with self.assertRaises(RuntimeError):p._assert_entry_deadline()
if __name__=='__main__':unittest.main()
