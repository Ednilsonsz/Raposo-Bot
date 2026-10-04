import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import sqlite3
import unittest

from adaptive_position_v386_r11 import AdaptivePositionRanker
from demo_executor_v386_r11 import DemoExecutor
from strategy_modules_v386_r11 import candle_flow, revz, sr_nivel


class AdaptivePositionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE score_signals(id INTEGER PRIMARY KEY,asset_id TEXT,setup TEXT,signal_ts INTEGER);
            CREATE TABLE decisions(id INTEGER PRIMARY KEY,asset_id TEXT,c1_dir TEXT,cycle_start INTEGER);
            CREATE TABLE demo_orders(id INTEGER PRIMARY KEY,decision_id INTEGER,status TEXT);
            CREATE TABLE broker_confirmed_results(demo_order_id INTEGER,result TEXT,closed_at TEXT);
        """)
        self.ranker = AdaptivePositionRanker(self.db, {
            "enabled": True, "min_samples_per_position": 12, "recent_window": 30,
            "min_win_rate": 0.52, "min_confidence_lower": 0.40,
            "max_active_positions": 3, "fallback_new_positions": [2, 3]
        })

    def tearDown(self):
        self.db.close()

    def test_new_setup_uses_safe_fallback_without_sample(self):
        self.assertFalse(self.ranker.evaluate("DIDI NEW", 1)[0])
        self.assertTrue(self.ranker.evaluate("DIDI NEW", 2)[0])
        self.assertEqual(self.ranker.evaluate("DIDI NEW", 2)[1], "FALLBACK")

    def test_confident_real_results_replace_fixed_fallback(self):
        order_id = 1
        for position, wins in ((1, 11), (2, 2)):
            for index in range(12):
                decision_id = order_id
                self.db.execute("INSERT INTO adaptive_position_decisions VALUES(?,?,?,?,?,?,?,?)",
                                (decision_id, "76", "DIDI NEW", decision_id * 60, position,
                                 "TEST", "real result", "2026-09-21T10:00:00"))
                self.db.execute("INSERT INTO demo_orders VALUES(?,?,?)", (order_id, decision_id, "EXECUTED"))
                result = "WIN" if index < wins else "LOSS"
                self.db.execute("INSERT INTO broker_confirmed_results VALUES(?,?,?)",
                                (order_id, result, f"2026-09-21T10:{index:02d}:00"))
                order_id += 1
        self.db.commit()
        allowed_one, mode_one, _, _ = self.ranker.evaluate("DIDI NEW", 1)
        allowed_two, mode_two, _, _ = self.ranker.evaluate("DIDI NEW", 2)
        self.assertTrue(allowed_one)
        self.assertFalse(allowed_two)
        self.assertEqual(mode_one, "ADAPTIVE")
        self.assertEqual(mode_two, "ADAPTIVE")


class StrategyModuleTests(unittest.TestCase):
    @staticmethod
    def row(ts, open_, high, low, close):
        return (ts, open_, high, low, close)

    def test_candle_flow_uses_trend_force_context_volatility_and_payout(self):
        rows = []
        for index in range(50):
            open_ = 100.0 + index * 0.12
            close = open_ + 0.10
            rows.append(self.row(index * 60, open_, close + 0.03, open_ - 0.02, close))
        result = candle_flow(rows, {"min_confirmations": 4, "min_payout": 0.70}, 0.88)
        self.assertEqual(result["signal"], 1)
        self.assertGreaterEqual(result["score"], 5.0)
        self.assertIn("payout", result["components"])

    def test_revz_requires_reversal_confirmation_after_extreme(self):
        rows = []
        for index in range(120):
            close = 100.0 + (0.08 if index % 2 else -0.08)
            rows.append(self.row(index * 60, close - 0.02, close + 0.05, close - 0.05, close))
        rows.append(self.row(120 * 60, 100.2, 103.2, 100.1, 103.0))
        rows.append(self.row(121 * 60, 103.0, 103.2, 101.9, 102.0))
        result = revz(rows, {"lookback": 120, "z_threshold": 2.0})
        self.assertEqual(result["signal"], -1)
        self.assertTrue(result["components"]["z_receding"])
        self.assertTrue(result["components"]["toward_mean"])

    def test_sr_nivel_needs_touch_and_rejection(self):
        rows = []
        for index in range(70):
            low = 99.0 if index in (5, 15, 25, 35, 45, 55, 65) else 99.35
            rows.append(self.row(index * 60, 99.55, 99.75, low, 99.60))
        rows.append(self.row(70 * 60, 99.20, 99.48, 98.98, 99.42))
        result = sr_nivel(rows, {"lookback": 60, "atr_tolerance": 0.35, "min_touches": 2})
        self.assertEqual(result["signal"], 1)
        self.assertGreaterEqual(result["components"]["touches"], 2)


class ExecutorSchemaTests(unittest.TestCase):
    def test_record_matches_current_fourteen_column_schema(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
            CREATE TABLE decisions(id INTEGER PRIMARY KEY,status TEXT,updated_at TEXT);
            INSERT INTO decisions(id,status) VALUES(10,'SIGNAL_PENDING');
            CREATE TABLE demo_orders(
              id INTEGER PRIMARY KEY,decision_id INTEGER NOT NULL,asset_id TEXT,
              cycle_start INTEGER,leg TEXT NOT NULL,direction TEXT NOT NULL,stake REAL NOT NULL,
              requested_at TEXT,executed_at TEXT,status TEXT,error TEXT,
              our_asset_id INTEGER,asset_name TEXT,bullex_active_id TEXT,
              candle_exec INTEGER,build_revision TEXT,direction_original TEXT,
              direction_executed TEXT,candle_flow_inverted INTEGER NOT NULL DEFAULT 0,
              UNIQUE(decision_id,leg)
            );
            CREATE TABLE asset_catalog(
              our_asset_id INTEGER PRIMARY KEY,asset_name TEXT,normalized_name TEXT,
              enabled INTEGER,created_at TEXT,updated_at TEXT
            );
            CREATE TABLE asset_bullex_ids(
              bullex_active_id TEXT PRIMARY KEY,our_asset_id INTEGER,confirmed_by TEXT,
              first_seen_at TEXT,last_seen_at TEXT
            );
        """)
        executor = DemoExecutor.__new__(DemoExecutor)
        executor.db = db
        executor.cfg = {'build_revision': 'V3.86 · R11'}
        executor._current_asset_name = None
        executor.selected_asset_provider = None
        executor._record(10, "80", 1234567800, "SIG", "G", 10.0, "EXECUTED")
        row = db.execute("SELECT decision_id,asset_id,cycle_start,candle_exec,build_revision,status FROM demo_orders").fetchone()
        self.assertEqual(row, (10, "80", 1234567800, 1, "V3.86 · R11", "EXECUTED"))
        self.assertEqual(db.execute("SELECT status FROM decisions WHERE id=10").fetchone()[0], "EXECUTED")
        db.close()


if __name__ == "__main__":
    unittest.main()


