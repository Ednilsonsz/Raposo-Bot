import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from daily_setup_control import setup_gate


class SetupGateTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE decisions(id INTEGER PRIMARY KEY,c1_dir TEXT);
            CREATE TABLE demo_orders(id INTEGER PRIMARY KEY,decision_id INTEGER,status TEXT);
            CREATE TABLE broker_confirmed_results(demo_order_id INTEGER,pnl_minor INTEGER,currency TEXT,closed_at TEXT,result TEXT);
        ''')
        self.cfg = {'score_engine': {'daily_setup_block': {'enabled': True, 'minimum_operations': 8, 'share_vs_others': .3}}}

    def add(self, setup, count, pnl=-1000, day='2026-09-29'):
        for _ in range(count):
            did = self.db.execute('INSERT INTO decisions(c1_dir) VALUES(?)', (setup,)).lastrowid
            self.db.execute('INSERT INTO demo_orders VALUES(?,?,?)', (did, did, 'EXECUTED'))
            self.db.execute('INSERT INTO broker_confirmed_results VALUES(?,?,?,?,?)', (did, pnl, 'USD', day+'T19:00:00', 'LOSS' if pnl < 0 else 'WIN'))
        self.db.commit()

    def test_minimum_share_and_negative_finance(self):
        self.add('FLOW', 7)
        self.add('OTHER', 26, 870)
        with patch('daily_setup_control.broker_today', return_value='2026-09-29'):
            self.assertTrue(setup_gate(self.db, self.cfg, 'FLOW')[0])
            self.add('FLOW', 1)
            self.assertFalse(setup_gate(self.db, self.cfg, 'FLOW')[0])
            self.assertTrue(setup_gate(self.db, self.cfg, 'OTHER')[0])

    def test_stays_blocked_until_next_day(self):
        self.add('FLOW', 8)
        with patch('daily_setup_control.broker_today', return_value='2026-09-29'):
            self.assertFalse(setup_gate(self.db, self.cfg, 'FLOW')[0])
            self.add('FLOW', 20, 870)
            self.assertFalse(setup_gate(self.db, self.cfg, 'FLOW')[0])
        with patch('daily_setup_control.broker_today', return_value='2026-09-30'):
            self.assertTrue(setup_gate(self.db, self.cfg, 'FLOW')[0])

    def test_under_thirty_percent_is_allowed(self):
        self.add('FLOW', 8)
        self.add('OTHER', 27, 870)
        with patch('daily_setup_control.broker_today', return_value='2026-09-29'):
            self.assertTrue(setup_gate(self.db, self.cfg, 'FLOW')[0])


if __name__ == '__main__':
    unittest.main()
