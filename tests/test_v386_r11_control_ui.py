import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import ast
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from main_v386_r11 import acknowledge_control, wait_for_bullex_workspace
from pause_control_r11 import synchronize_pause
from ui_overlay_massa_v386_r11 import BullexOverlay
from bullex_controls_reader import parse_money, parse_words, read_controls
from demo_executor_v386_r11 import DemoExecutor


class FakeDriver:
    def __init__(self):
        self.pause = True
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if 'typeof window.__RAPOSO_PAUSE_REQUEST__' in script:
            return self.pause
        if 'window.__RAPOSO_PAUSE_REQUEST__=arguments[0]' in script:
            self.pause = bool(args[0])
        return None


class ControlTests(unittest.TestCase):
    def test_control_reader_ignores_account_balance(self):
        self.assertEqual(parse_money('$ 9,992.76'), 9992.76)
        self.assertEqual(parse_money('$ 9.992,76'), 9992.76)
        self.assertIsNone(parse_money('saldo 9.992,76'))
        words = [
            {'text':'$ 9,992.76','x':20,'y':15,'width':90,'height':20},
            {'text':'Invest','x':220,'y':80,'width':45,'height':18},
            {'text':'$ 10','x':220,'y':105,'width':40,'height':18},
            {'text':'Expiração','x':220,'y':140,'width':70,'height':18},
            {'text':'1 min','x':240,'y':165,'width':40,'height':18},
        ]
        parsed=parse_words(words)
        self.assertNotIn('account_balance', parsed)
        self.assertEqual(parsed['amount'], 10.0)
        self.assertEqual(parsed['expiry'], '1 MIN')

    def test_account_balance_does_not_affect_investment_control_reading(self):
        words = [
            {'text':'$9,992.76','x':20,'y':15,'width':90,'height':20},
            {'text':'Depositar','x':140,'y':16,'width':70,'height':20},
            {'text':'Invest','x':220,'y':80,'width':45,'height':18},
            {'text':'$','x':220,'y':105,'width':8,'height':18},
            {'text':'10','x':232,'y':105,'width':20,'height':18},
            {'text':'Expiração','x':220,'y':140,'width':70,'height':18},
            {'text':'1 min','x':240,'y':165,'width':40,'height':18},
        ]
        parsed=parse_words(words)
        self.assertNotIn('account_balance', parsed)
        self.assertEqual(parsed['amount'], 10.0)

    def test_execution_gate_uses_demo_configuration_not_account_balance_reader(self):
        executor=DemoExecutor.__new__(DemoExecutor)
        executor.cfg={'demo_only':True,'execute_orders':True,'demo_stake':10.0}
        executor.runtime_ready=True
        executor._expiry_1m_confirmed_by_code=True
        executor.session_valid=lambda: True
        executor.refresh_order_buttons=lambda: True
        self.assertTrue(executor.safety_ready())
        executor.cfg['demo_only']=False
        self.assertFalse(executor.safety_ready())
        executor.cfg['demo_only']=True
        executor.cfg['execute_orders']=False
        self.assertFalse(executor.safety_ready())

    def test_order_button_gate_recovers_without_clicking(self):
        executor=DemoExecutor.__new__(DemoExecutor)
        executor._buttons_ready=True
        executor.armed=True
        executor._button_rects={}
        executor._persist_button_rect=lambda *args: None
        samples=iter((None, None,
                      {'x':100,'y':100,'width':100,'height':50,'source':'PIXEL'},
                      {'x':100,'y':200,'width':100,'height':50,'source':'PIXEL'}))
        executor._probe_button_no_click=lambda side: next(samples)
        self.assertFalse(executor.refresh_order_buttons())
        self.assertFalse(executor.armed)
        self.assertTrue(executor.refresh_order_buttons())
        self.assertTrue(executor._buttons_ready)

    def test_explicit_pause_resume_repeated_100_times(self):
        driver = FakeDriver()
        analyzer = SimpleNamespace(paused=True)
        disarms = []
        executor = SimpleNamespace(disarm=lambda: disarms.append(True))
        paused = True
        for index in range(100):
            command = 'RESUME' if paused else 'PAUSE'
            paused = synchronize_pause(driver, paused, command, analyzer, executor)
            analyzer.paused = paused
            acknowledge_control(driver, f'command-{index}', command, paused)
            self.assertEqual(paused, command == 'PAUSE')
            self.assertEqual(driver.pause, paused)
        self.assertTrue(any('raposoAckId' in script for script, _ in driver.calls))

    def test_bootstrap_waits_for_four_stable_ready_samples(self):
        class ReadyDriver:
            def __init__(self): self.count = 0
            def execute_script(self, script):
                self.count += 1
                return {'ready': self.count >= 3}
        driver = ReadyDriver()
        self.assertTrue(wait_for_bullex_workspace(driver, timeout=3))
        self.assertEqual(driver.count, 6)

    def test_overlay_javascript_parses_and_has_required_contract(self):
        source = (ROOT/'app'/'ui_overlay_massa_v386_r11.py').read_text(encoding='utf-8')
        self.assertIn("setTimeout(()=>", source)
        self.assertIn("2500", source)
        self.assertIn("crypto.randomUUID", source)
        pause_source = (ROOT/'app'/'pause_control_r11.py').read_text(encoding='utf-8')
        self.assertIn("'PAUSE'", pause_source)
        self.assertIn("'RESUME'", pause_source)
        for label in ('CLÁSSICO', 'LOG', 'DASHBOARD', 'VISÃO GERAL', 'HISTÓRICO', 'Candle Exec.', 'Versão/Revisão'):
            self.assertIn(label, source)
        self.assertNotIn('id="bxrestoreui"', source)
        self.assertNotIn('id="bxlogwrap"', source)
        ast.parse(source)

    def test_live_control_reader_is_packaged_and_classic_skips_heavy_views(self):
        self.assertTrue(callable(read_controls))
        source = (ROOT/'app'/'ui_overlay_massa_v386_r11.py').read_text(encoding='utf-8')
        self.assertIn("dashboard_open = request.get('mode') == 'dashboard'", source)
        self.assertIn("self._history_rows(request) if dashboard_open else []", source)
        self.assertIn("self._log_tail() if request.get('mode') == 'log' else []", source)
        self.assertIn("window.open(u,'raposo-r12-dashboard-window','popup=yes", source)
        self.assertIn("'_dashboard_url'", source)

    def test_amount_ocr_never_blocks_control_loop(self):
        executor = DemoExecutor.__new__(DemoExecutor)
        executor._amount_10_confirmed_by_code = False
        executor._amount_read_thread = None
        executor._amount_read_result = None
        executor._last_amount_set_attempt = 0.0
        executor.document_token = 'document-1'
        executor._control_read_diagnostic = {}
        def slow_read():
            time.sleep(.2)
            executor._control_read_diagnostic = {'source': 'TEST'}
            return 10.0
        executor.read_amount_value = slow_read
        started = time.monotonic()
        self.assertFalse(executor._confirm_amount_10())
        self.assertLess(time.monotonic()-started, .1)
        time.sleep(.3)
        self.assertTrue(executor._confirm_amount_10())

    def test_history_ranges(self):
        for key in ('today','yesterday','7d','14d','28d','30d','this_week','last_week','this_month','last_month','this_quarter','max','custom'):
            start, end = BullexOverlay._history_bounds(key, '2026-09-01', '2026-09-22')
            if key == 'max':
                self.assertIsNone(start); self.assertIsNone(end)
            else:
                self.assertIsNotNone(start); self.assertIsNotNone(end)

    def test_history_timestamp_uses_brazilian_display_format(self):
        self.assertEqual(BullexOverlay._format_history_time('2026-09-21T19:59:58'),
                         '21/09/2026 - 19:59:58')
        self.assertEqual(BullexOverlay._format_history_time('2026-09-21T22:59:58Z'),
                         '21/09/2026 - 19:59:58')


if __name__ == '__main__':
    unittest.main(verbosity=2)


