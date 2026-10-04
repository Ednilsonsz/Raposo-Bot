import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import ast,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT / "app"))
import r11_restart as restart
class RestartTests(unittest.TestCase):
 def test_config_roundtrip_and_database_untouched(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder);db=p/'candles.db';db.write_bytes(b'persisted-ledger')
   restart.save_restart_state({'asset_id':'86','demo_stake':10,'session_goals':{'mode':'FULL'},'password':'never-save','_session_started_at':'old'},p)
   state=json.loads((p/restart.STATE_NAME).read_text());self.assertTrue(state['paused']);self.assertNotIn('password',state['config']);self.assertNotIn('_session_started_at',state['config'])
   cfg=restart.load_restart_state({'asset_id':'76'},p);self.assertEqual(cfg['asset_id'],'86');self.assertEqual(cfg['session_goals']['mode'],'FULL');self.assertEqual(db.read_bytes(),b'persisted-ledger')
 def test_missing_state_preserves_defaults(self):
  with tempfile.TemporaryDirectory() as folder:self.assertEqual(restart.load_restart_state({'x':1},folder),{'x':1})
 def test_invalid_state_does_not_silently_use_other_config(self):
  with tempfile.TemporaryDirectory() as folder:
   (Path(folder)/restart.STATE_NAME).write_text('{"version":9}')
   with self.assertRaises(ValueError):restart.load_restart_state({},folder)
 def test_helper_is_hidden_and_waits_for_parent(self):
  with patch.object(restart.subprocess,'Popen') as popen:
   restart.launch_after_exit(123)
   args,kwargs=popen.call_args
   self.assertEqual(Path(args[0][1]).name,'r11_restart.py')
   self.assertEqual(args[0][-2:],['--wait-pid','123']);self.assertTrue(kwargs['creationflags'])
 def test_actual_windows_wait_for_child_exit(self):
  child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(.15)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  restart.wait_for_exit(child.pid,5);self.assertEqual(child.wait(timeout=1),0)
 def test_refuse_self_pid(self):
  with self.assertRaises(ValueError):restart.wait_for_exit(os.getpid())
 def test_launch_new_main_only_after_old_exits(self):
  events=[]
  with patch.object(sys,'argv',['r11_restart.py','--wait-pid','123']),patch.object(restart,'wait_for_exit',side_effect=lambda pid:events.append('old-exited')),patch.object(restart.subprocess,'Popen',side_effect=lambda *a,**k:events.append(a[0][-1])):
   restart.main()
  self.assertEqual(events,['old-exited','--resume'])
 def test_restart_command_pauses_and_waits_for_executor(self):
  source=(ROOT/'app'/'main_v386_r11.py').read_text(encoding='utf-8-sig')
  self.assertIn("restart_pending=True;user_paused=True",source)
  self.assertIn("if analyzer: analyzer.paused=True",source)
  self.assertIn("if executor: executor.disarm()",source)
  self.assertIn("lock.acquire(blocking=False)",source)
  self.assertIn("restart_requested=True",source)
if __name__=='__main__':unittest.main(verbosity=2)


