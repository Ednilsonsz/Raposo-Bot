import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from feed_watchdog_m1_r11 import M1FeedWatchdog
from demo_executor_v386_r11 import DemoExecutor


class M1FeedWatchdogTests(unittest.TestCase):
    def make_watchdog(self):
        return M1FeedWatchdog({'feed_watchdog': {
            'min_samples': 3,
            'min_timeout_seconds': 4,
            'max_timeout_seconds': 20,
            'cadence_multiplier': 5,
            'recovery_advances': 3,
        }})

    def test_learns_cadence_freezes_and_requires_consistent_recovery(self):
        watchdog = self.make_watchdog()
        wall = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
        for second in range(5):
            state = watchdog.observe('76', 1000 + second, 2000, now=float(second), wall_now=wall)
        self.assertTrue(state['learned'])
        self.assertEqual(state['tolerance_seconds'], 5.0)

        state = watchdog.observe('76', 1004, 2000, now=8.9, wall_now=wall)
        self.assertFalse(state['frozen'])
        state = watchdog.observe('76', 1004, 2000, now=9.1, wall_now=wall)
        self.assertTrue(state['frozen'])
        self.assertEqual(state['event']['kind'], 'frozen')

        self.assertTrue(watchdog.observe('76', 1005, 2000, now=10, wall_now=wall)['frozen'])
        self.assertTrue(watchdog.observe('76', 1006, 2000, now=11, wall_now=wall)['frozen'])
        recovered = watchdog.observe('76', 1007, 2000, now=12, wall_now=wall)
        self.assertFalse(recovered['frozen'])
        self.assertEqual(recovered['event']['kind'], 'recovered')
        self.assertEqual(recovered['event']['advances'], 3)

    def test_small_jitter_does_not_freeze(self):
        watchdog = self.make_watchdog()
        wall = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
        for second in (0, 1, 2, 3, 4, 7.5, 8.5):
            state = watchdog.observe('76', 1000 + second, 2000, now=second, wall_now=wall)
            self.assertFalse(state['frozen'])

    def test_slower_normal_cadence_is_learned_without_false_positive(self):
        watchdog = self.make_watchdog()
        wall = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
        for second in (0, 6, 12, 18, 24, 30):
            state = watchdog.observe('76', 1000 + second, 2000, now=second, wall_now=wall)
        self.assertTrue(state['learned'])
        self.assertEqual(state['cadence_seconds'], 6.0)
        self.assertEqual(state['tolerance_seconds'], 20.0)
        self.assertFalse(watchdog.observe('76', 1030, 2000, now=49.9, wall_now=wall)['frozen'])
        self.assertTrue(watchdog.observe('76', 1030, 2000, now=50.1, wall_now=wall)['frozen'])

    def test_executor_gate_disarms_only_new_entries(self):
        executor = DemoExecutor.__new__(DemoExecutor)
        executor.armed = True
        executor.set_feed_m1_ready(False)
        self.assertFalse(executor.feed_m1_ready)
        self.assertFalse(executor.armed)
        executor.set_feed_m1_ready(True)
        self.assertTrue(executor.feed_m1_ready)
        self.assertFalse(executor.armed)


if __name__ == '__main__':
    unittest.main()
