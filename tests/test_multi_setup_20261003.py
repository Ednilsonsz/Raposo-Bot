import unittest,sqlite3,sys,json,ast
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from risk_return_r12 import entry_gate
from broker_day_r11 import SAO_PAULO
class MultiTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:')
  self.db.executescript('CREATE TABLE demo_orders(id INTEGER,decision_id INTEGER,status TEXT,requested_at TEXT,asset_id TEXT,cycle_start INTEGER,stake REAL); CREATE TABLE decisions(id INTEGER,c1_dir TEXT); CREATE TABLE broker_confirmed_results(demo_order_id INTEGER,closed_at TEXT,result TEXT,pnl_minor INTEGER,stake_minor INTEGER,currency TEXT,evidence TEXT);')
  self.now=datetime(2026,10,3,13,0,tzinfo=SAO_PAULO);self.target=int(self.now.timestamp())
  self.cfg={'demo_only':True,'demo_stake':10,'risk_return':{'enabled':True,'daily_net_loss_minor':4000,'currency':'USD','single_open_order':True,'activated_at':self.now.isoformat()},'multi_setup_demo':{'enabled':True},'loss_control':{'shifts':{'T5':{'mode':'LIMIT','limit':2}}}}
 def tearDown(self):self.db.close()
 def gate(self,name='B',slots=1,target=None):return entry_gate(self.db,self.cfg,name,self.now,asset_id='79',target_ts=self.target if target is None else target,requested_slots=slots)[0]
 def pending(self,i,name):
  self.db.execute('INSERT INTO decisions VALUES(?,?)',(i,name))
  self.db.execute('INSERT INTO demo_orders VALUES(?,?,?,?,?,?,?)',(i,i,'SUBMITTED',self.now.isoformat(),'79',self.target,10))
 def test_pair_and_third(self):
  self.assertTrue(self.gate(slots=2));self.pending(1,'A');self.assertTrue(self.gate())
  self.pending(2,'B');self.assertFalse(self.gate('C'))
 def test_same_setup_other_candle_and_real_mode_rejected(self):
  self.pending(1,'A');self.assertFalse(self.gate('A'));self.assertFalse(self.gate(target=self.target+60))
  self.cfg['demo_only']=False;self.assertFalse(self.gate('B'))
 def test_combined_daily_reservation(self):
  self.pending(1,'A')
  self.db.execute("INSERT INTO demo_orders VALUES(9,9,'EXECUTED',?,'79',?,10)",(self.now.isoformat(),self.target-60))
  self.db.execute("INSERT INTO broker_confirmed_results VALUES(9,?,'LOSS',-2500,1000,'USD','{}')",(self.now.isoformat(),))
  self.assertFalse(self.gate())
 def test_shift_reservation(self):
  self.db.execute("INSERT INTO broker_confirmed_results VALUES(9,?,'LOSS',-1000,1000,'USD','{}')",(self.now.isoformat(),))
  self.assertFalse(self.gate(slots=2));self.assertTrue(self.gate(slots=1))
 def test_dispatch_both_directions_and_no_repeat(self):
  tree=ast.parse((Path(__file__).parents[1]/'app/realtime_analyzer_v386_r11.py').read_text(encoding='utf-8'))
  method=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='strategy_engine')
  ns={};exec(ast.unparse(ast.Module(body=[ast.ClassDef(name='Probe',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[])),ns)
  for sides in [('CALL','CALL'),('CALL','PUT')]:
   p=ns['Probe']();p.cfg=self.cfg;p.db=self.db;p.asset='79';p.secfg={'min_score':5};p.mcfg={};p.broker_clock_snapshot=lambda:(45.,0.)
   p._batch_candidates=[{'setup':n,'side':s,'score':7,'ts':self.target} for n,s in zip(['A','B','C'],[*sides,'CALL'])]
   p.last_score=p._batch_candidates[0];calls=[]
   p._strategy_engine_one=lambda by,t:calls.append((p.last_score['setup'],p.last_score['side']))
   p.strategy_engine({},self.target);p.strategy_engine({},self.target)
   self.assertEqual(calls,[('A',sides[0]),('B',sides[1])])
if __name__=='__main__':unittest.main()
