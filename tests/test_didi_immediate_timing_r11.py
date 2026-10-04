import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import realtime_analyzer_v386_r11 as analyzer_module


class _AdaptiveAllow:
    def position_for(self, asset, setup, signal_ts):
        return 1

    def evaluate(self, setup, position):
        return True, "FALLBACK", "teste autorizado", []

    def record(self, *args):
        return None


class _ExecutorExecuted:
    def __init__(self, db):
        self.db = db
        self.last_execute = None

    def record_favorable_setup(self, decision_id, asset_id, cycle_start, leg,
                               direction, stake, setup, score=None, event_second=None):
        print(f"[DEMO][SETUP_FAVORAVEL] {setup} | segundo={event_second}")

    def execute(self, decision_id, asset_id, cycle_start, leg, direction, stake, immediate=False,
                direction_original=None, candle_flow_inverted=False, event_second=None,
                event_observed_at=None):
        self.last_execute = {
            "immediate": immediate, "event_second": event_second,
            "event_observed_at": event_observed_at,
        }
        self.db.execute(
            "INSERT INTO demo_orders(decision_id,leg,status,error) VALUES(?,?,?,NULL)",
            (decision_id, leg, "EXECUTED"),
        )
        self.db.commit()
        print("[DEMO][DIDI_IMEDIATO] segundo=1")
        print("[DEMO][SUBMITTED] segundo=1")
        print("[BROKER][EXECUTED] segundo=1")
        return True


def _rows(current_from):
    rows = {}
    for offset in range(-35, 1):
        ts = current_from + offset * 60
        price = 1.0 + (offset + 35) * 0.001
        rows[ts] = (ts, price, price + 0.002, price - 0.001, price + 0.001)
    return rows


def _analyzer(current_from, second):
    obj = analyzer_module.RealtimeAnalyzer.__new__(analyzer_module.RealtimeAnalyzer)
    obj.scfg = {"enabled": True}
    obj.secfg = {"enabled": True, "min_score": 5.0, "historical_priority": {}}
    obj.cfg = {"auto_demo": {"immediate_max_second": 3, "feed_stale_seconds": 5.0}}
    obj.mcfg = {"enabled": False}
    obj.asset = "76"
    obj.stake = 10.0
    obj.paused = False
    obj.block_until_current_from = 0
    obj._last_strategy_order_ts = None
    obj._last_score_eval_key = None
    obj._last_score_candle = None
    obj.last_score = None
    obj.graph_signals = []
    obj.live_state = lambda: (current_from, current_from + 60, current_from + second)
    obj.broker_clock_snapshot = lambda: (float(second), 100.0)
    obj.feed_is_fresh = lambda max_age_seconds=5.0: True
    obj._resolve_score_signals = lambda by, current: None
    obj._record_score_signal = lambda *args: None
    obj._record_score_ranking = lambda *args: None
    obj._score_calibration = lambda name, total, current: (total, 0)
    obj._setup_roi_today = lambda setup: 0.0
    obj.adaptive = _AdaptiveAllow()
    return obj


class DidiImmediateTimingR11Tests(unittest.TestCase):
    def test_didi_uses_closed_candles_only_at_open(self):
        current = 1_800_000_000
        rows = _rows(current)
        selected = analyzer_module.didi_immediate_rows(rows, current, current + 1, 3)
        self.assertEqual(selected[-1][0], current - 60)
        self.assertNotIn(current, [row[0] for row in selected])
        self.assertIsNone(analyzer_module.didi_immediate_rows(rows, current, current + 57, 3))

    def test_full_didi_chain_dispatches_at_second_one(self):
        current = 1_800_000_000
        obj = _analyzer(current, 1)
        obj.db = sqlite3.connect(":memory:")
        self.addCleanup(obj.db.close)
        obj.db.executescript("""
            CREATE TABLE decisions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
                asset_id TEXT, cycle_start INTEGER, c1_dir TEXT, similarity REAL,
                matched_cycle INTEGER, prediction TEXT, status TEXT, decision_mode TEXT,
                UNIQUE(asset_id,cycle_start)
            );
            CREATE TABLE demo_orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER, leg TEXT,
                status TEXT, error TEXT
            );
            CREATE TABLE score_ranking_log(
                asset_id TEXT, signal_ts INTEGER, locked INTEGER DEFAULT 0, updated_at TEXT
            );
        """)
        obj.executor = _ExecutorExecuted(obj.db)
        output = io.StringIO()
        with patch.object(analyzer_module, "double_pullback_legacy", return_value=0), \
             patch.object(analyzer_module, "double_pullback_new", return_value=0), \
             patch.object(analyzer_module, "didi_signal_legacy", return_value=1), \
             patch.object(analyzer_module, "didi_signal_new", return_value=0), \
             patch.object(analyzer_module, "calc", return_value={"score": 4.0, "parts": {"teste": 4.0}}), \
             redirect_stdout(output):
            obj.score_lab(_rows(current), current)
            obj.strategy_engine(_rows(current), current)
        log = output.getvalue()
        for marker in ("[SCORE LAB] DIDI", "[ADAPTIVE][AUTORIZADO] DIDI",
                       "[MOTOR][ENTRADA] DIDI", "[DEMO][SETUP_FAVORAVEL]",
                       "[DEMO][DIDI_IMEDIATO]", "[DEMO][SUBMITTED]", "[BROKER][EXECUTED]"):
            self.assertIn(marker, log)
        self.assertNotIn("SKIPPED_STALE", log)
        self.assertIn("[SCORE LAB] DIDI | sinal=G | base=3.0 | confirm=4.0 | TOTAL=7.0 | sec=1", log)
        self.assertGreaterEqual(log.count("segundo=1"), 6)
        self.assertEqual(obj.executor.last_execute["event_second"], 1)
        self.assertEqual(obj.executor.last_execute["event_observed_at"], 100.0)

    def test_didi_is_not_evaluated_or_dispatched_at_second_57(self):
        current = 1_800_000_000
        obj = _analyzer(current, 57)
        with patch.object(analyzer_module, "double_pullback_legacy", return_value=0), \
             patch.object(analyzer_module, "double_pullback_new", return_value=0), \
             patch.object(analyzer_module, "didi_signal_legacy") as legacy, \
             patch.object(analyzer_module, "didi_signal_new") as new:
            obj.score_lab(_rows(current), current)
        legacy.assert_not_called()
        new.assert_not_called()
        self.assertEqual(obj.last_score["setup"], "AGUARDANDO SETUP")

    def test_didi_new_uses_the_same_immediate_dispatch_chain(self):
        current = 1_800_000_000
        obj = _analyzer(current, 1)
        obj.db = sqlite3.connect(":memory:")
        self.addCleanup(obj.db.close)
        obj.db.executescript("""
            CREATE TABLE decisions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT,
                asset_id TEXT, cycle_start INTEGER, c1_dir TEXT, similarity REAL,
                matched_cycle INTEGER, prediction TEXT, status TEXT, decision_mode TEXT,
                UNIQUE(asset_id,cycle_start)
            );
            CREATE TABLE demo_orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER, leg TEXT,
                status TEXT, error TEXT
            );
            CREATE TABLE score_ranking_log(
                asset_id TEXT, signal_ts INTEGER, locked INTEGER DEFAULT 0, updated_at TEXT
            );
        """)
        obj.executor = _ExecutorExecuted(obj.db)
        output = io.StringIO()
        with patch.object(analyzer_module, "double_pullback_legacy", return_value=0), \
             patch.object(analyzer_module, "double_pullback_new", return_value=0), \
             patch.object(analyzer_module, "didi_signal_legacy", return_value=0), \
             patch.object(analyzer_module, "didi_signal_new", return_value=1), \
             patch.object(analyzer_module, "calc", return_value={"score": 4.0, "parts": {"teste": 4.0}}), \
             redirect_stdout(output):
            obj.score_lab(_rows(current), current)
            obj.strategy_engine(_rows(current), current)
        log = output.getvalue()
        self.assertIn("[SCORE LAB] DIDI NEW", log)
        self.assertIn("[MOTOR][ENTRADA] DIDI NEW", log)
        self.assertIn("[MOTOR][EXECUTED] DIDI NEW", log)
        self.assertNotIn("SKIPPED_STALE", log)


if __name__ == "__main__":
    unittest.main()


