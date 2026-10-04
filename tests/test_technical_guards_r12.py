import sys,time,tempfile,json,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from demo_executor_v386_r11 import DemoExecutor
from asset_label_store import remember_label
class Guards(unittest.TestCase):
 def test_corrupt_asset_cache_preserved_and_rebuilt(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'labels.json';p.write_bytes(bytes(20))
   remember_label(p,'76','EURUSD-OTC',[('1975','SHIB/USD (OTC)')])
   self.assertEqual(json.loads(p.read_text())['1975'],'SHIB/USD (OTC)')
   self.assertEqual(p.with_suffix('.corrupt.json').read_bytes(),bytes(20))
if __name__=='__main__':unittest.main()
