import sqlite3
import sys
import threading
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

from broker_day_r11 import SAO_PAULO
from demo_executor_v386_r11 import DemoExecutor
from turn_loss_control_r12 import TurnLossController, shift_window


def at(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=SAO_PAULO)


class TurnLossControlR12Tests(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_dir.cleanup)
        self.state_path = Path(self.state_dir.name) / 'baselines.json'
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE broker_confirmed_results(result TEXT, closed_at TEXT)')
        self.cfg = {'loss_control': {'shifts': {
            'T1': {'mode': 'FREE', 'limit': None},
            'T2': {'mode': 'LIMIT', 'limit': 2},
            'T3': {'mode': 'LIMIT', 'limit': 2},
            'T4': {'mode': 'LIMIT', 'limit': 2},
        }}}

    def add(self, result, moment):
        self.db.execute('INSERT INTO broker_confirmed_results VALUES(?,?)', (result, moment.isoformat()))
        self.db.commit()

    def controller(self, cfg=None):
        return TurnLossController(self.db, cfg or self.cfg, cache_seconds=0, state_path=self.state_path)

    def test_noon_boundary_keeps_1200_in_t2_and_1201_in_t3(self):
        self.assertEqual(shift_window(at(2026, 9, 30, 12, 0))[0], 'T2')
        self.assertEqual(shift_window(at(2026, 9, 30, 12, 1))[0], 'T3')
        self.assertEqual(shift_window(at(2026, 9, 30, 13, 0))[0], 'T3')

    def test_requested_shift_boundaries(self):
        self.assertEqual(shift_window(at(2026, 9, 29, 0, 0))[0], 'T4')
        self.assertEqual(shift_window(at(2026, 9, 29, 0, 1))[0], 'T1')
        self.assertEqual(shift_window(at(2026, 9, 29, 6, 0, 59))[0], 'T1')
        self.assertEqual(shift_window(at(2026, 9, 29, 6, 1))[0], 'T2')
        self.assertEqual(shift_window(at(2026, 9, 29, 12, 1))[0], 'T3')
        self.assertEqual(shift_window(at(2026, 9, 29, 18, 1))[0], 'T4')

    def test_only_confirmed_losses_count_and_wins_never_stop(self):
        controller = self.controller()
        controller.snapshot(at(2026, 9, 29, 7, 0))
        self.add('WIN', at(2026, 9, 29, 8, 0))
        self.add('WIN_GALE', at(2026, 9, 29, 8, 1))
        self.add('DRAW', at(2026, 9, 29, 8, 2))
        state = controller.snapshot(at(2026, 9, 29, 9, 0))
        self.assertTrue(state['allowed'])
        self.assertEqual(state['losses'], 0)

    def test_limit_blocks_only_after_reaching_loss_count(self):
        controller = self.controller()
        controller.snapshot(at(2026, 9, 29, 7, 0))
        self.add('LOSS', at(2026, 9, 29, 8, 0))
        first = controller.snapshot(at(2026, 9, 29, 9, 0))
        self.assertTrue(first['allowed'])
        self.assertEqual(first['losses'], 1)
        self.add('LOSS_GALE', at(2026, 9, 29, 9, 1))
        stopped = controller.snapshot(at(2026, 9, 29, 9, 2))
        self.assertFalse(stopped['allowed'])
        self.assertTrue(stopped['stopped'])
        self.assertEqual(stopped['losses'], 2)
        self.assertEqual(stopped['shifts'][1]['label'], 'T2 — STOP LOSS ATINGIDO 2/2')

    def test_restart_in_same_shift_rebuilds_from_database(self):
        self.controller().snapshot(at(2026, 9, 29, 7, 0))
        self.add('LOSS', at(2026, 9, 29, 8, 0))
        before = self.controller().snapshot(at(2026, 9, 29, 9, 0))
        after = self.controller().snapshot(at(2026, 9, 29, 9, 5))
        self.assertEqual(before['losses'], 1)
        self.assertEqual(after['losses'], 1)

    def test_existing_losses_become_baseline_when_control_starts(self):
        for minute in range(10, 19):
            self.add('LOSS', at(2026, 9, 29, 7, minute))
        controller = self.controller()
        initial = controller.snapshot(at(2026, 9, 29, 8, 0))
        self.assertEqual(initial['losses'], 0)
        self.add('LOSS', at(2026, 9, 29, 8, 1))
        self.assertEqual(controller.snapshot(at(2026, 9, 29, 8, 2))['losses'], 1)

    def test_controller_never_writes_or_resets_history(self):
        self.add('LOSS', at(2026, 9, 29, 8, 0))
        changes = self.db.total_changes
        self.controller().snapshot(at(2026, 9, 29, 9, 0))
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM broker_confirmed_results').fetchone()[0], 1)

    def test_new_shift_uses_its_own_counter(self):
        controller = self.controller()
        controller.snapshot(at(2026, 9, 29, 7, 0))
        self.add('LOSS', at(2026, 9, 29, 8, 0))
        self.add('LOSS', at(2026, 9, 29, 9, 0))
        t2 = controller.snapshot(at(2026, 9, 29, 11, 0))
        t3 = controller.snapshot(at(2026, 9, 29, 12, 1))
        self.assertFalse(t2['allowed'])
        self.assertTrue(t3['allowed'])
        self.assertEqual(t3['losses'], 0)

    def test_free_shift_and_unconfigured_shift(self):
        controller = self.controller()
        controller.snapshot(at(2026, 9, 29, 0, 1))
        for minute in range(2, 8):
            self.add('LOSS', at(2026, 9, 29, 1, minute))
        self.assertTrue(controller.snapshot(at(2026, 9, 29, 2, 0))['allowed'])
        cfg = {'loss_control': {'shifts': {'T1': {'mode': 'FREE', 'limit': None}}}}
        state = self.controller(cfg).snapshot(at(2026, 9, 29, 8, 0))
        self.assertFalse(state['allowed'])
        self.assertTrue(state['unconfigured'])

    def test_executor_blocks_before_any_order_path_when_limit_is_reached(self):
        class Gate:
            def allows_new_entries(self):
                return False, {'current':'T2', 'reason':'STOP LOSS ATINGIDO T2 2/2'}
        executor = DemoExecutor.__new__(DemoExecutor)
        executor.lock = threading.Lock()
        executor.cfg = {'execute_orders': True}
        executor.loss_controller = Gate()
        recorded = []
        executor._record = lambda *args: recorded.append(args)
        result = executor.execute(10, '86', 1234567800, 'SIG', 'G', 10)
        self.assertFalse(result)
        self.assertEqual(recorded[0][-2:], ('BLOCKED', 'STOP LOSS ATINGIDO T2 2/2'))


if __name__ == '__main__':
    unittest.main()
