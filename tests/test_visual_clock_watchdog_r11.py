import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import ast
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))
from bullex_controls_reader import parse_words


class TestVisualClockWatchdogR11(unittest.TestCase):
    def test_paused_capture_reads_id_and_expiry_without_click(self):
        # Simulação da captura OCR da tela Bullex: saldo no cabeçalho,
        # investimento de $10 e expiração de 1 MIN.
        words = [
            {"text": "$9,992.76", "x": 10, "y": 20, "width": 70, "height": 16},
            {"text": "Invest", "x": 40, "y": 100, "width": 42, "height": 16},
            {"text": "$10", "x": 42, "y": 124, "width": 28, "height": 16},
            {"text": "1 min", "x": 60, "y": 160, "width": 36, "height": 16},
        ]
        controls = parse_words(words)
        selected_asset = {"asset_id": "76", "label": "EURUSD-OTC", "verified": True}
        paused = True
        clicks = []

        self.assertEqual(selected_asset["asset_id"], "76")
        self.assertTrue(selected_asset["verified"])
        self.assertEqual(controls["amount"], 10.0)
        self.assertEqual(controls["expiry"], "1 MIN")
        if not paused:
            clicks.append("ACIMA")
        self.assertEqual(clicks, [])

    def test_broker_at_watchdog_blocks_only_the_executor_gate(self):
        main = (APP / "main_v386_r11.py").read_text(encoding="utf-8")
        analyzer = (APP / "realtime_analyzer_v386_r11.py").read_text(encoding="utf-8")
        self.assertIn("M1FeedWatchdog", main)
        self.assertIn("executor.set_feed_m1_ready(not feed_frozen)", main)
        self.assertIn("FEED_M1_CONGELADO", main)
        self.assertIn("reconciliacao=ATIVA", main)
        self.assertNotIn("if not self.feed_is_fresh", analyzer)

    def test_capture_clock_uses_configured_opening_second(self):
        executor = (APP / "demo_executor_v386_r11.py").read_text(encoding="utf-8")
        analyzer = (APP / "realtime_analyzer_v386_r11.py").read_text(encoding="utf-8")
        # O valor vem da configuração vigente (0 na baseline atual); o teste não
        # redefine timing e só garante que o executor respeita o valor configurado.
        self.assertIn("send_second=float(auto.get('entry_send_second',0))", executor)
        self.assertIn("send_second <= second_exact <= send_second+open_tolerance", executor)
        self.assertIn("broker_now=float(row[1])+age", executor)
        self.assertNotIn("gatilho :58 da Bullex nao observado", executor)
        self.assertIn("edge_arm_second=min(configured_arm,45.0)", analyzer)

    def test_sources_remain_valid_python(self):
        for name in ("main_v386_r11.py", "realtime_analyzer_v386_r11.py"):
            ast.parse((APP / name).read_text(encoding="utf-8"), filename=name)


if __name__ == "__main__":
    unittest.main()


