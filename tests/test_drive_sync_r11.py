import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from drive_sync import DriveDBSync, db_info, restore_if_needed


def make_database(path, timestamp=100):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE candles(timestamp INTEGER PRIMARY KEY, payload BLOB);
        CREATE TABLE decisions(id INTEGER PRIMARY KEY);
        CREATE TABLE demo_orders(id INTEGER PRIMARY KEY);
        CREATE TABLE bullex_live_state(asset_id TEXT PRIMARY KEY);
    """)
    db.execute("INSERT INTO candles(timestamp,payload) VALUES(?,zeroblob(150000))", (timestamp,))
    db.commit()
    db.close()


class DriveSyncR11Tests(unittest.TestCase):
    def test_lightweight_validation_and_local_restore_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "candles.db"
            make_database(database, 123)
            info = db_info(database)
            self.assertEqual(info["latest"], 123)
            self.assertEqual(restore_if_needed(database, Path(folder)), (False, "LOCAL_OK"))

    def test_start_does_not_copy_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "source.db"
            backup = root / "backup"
            make_database(database)
            sync = DriveDBSync(database, backup, interval=300)
            sync.start()
            time.sleep(0.05)
            self.assertFalse((backup / "candles_latest.db").exists())
            sync.stop()

    def test_explicit_snapshot_creates_valid_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "source.db"
            backup = root / "backup"
            make_database(database, 456)
            sync = DriveDBSync(database, backup, interval=300)
            self.assertTrue(sync.snapshot(), sync.last_status)
            self.assertEqual(db_info(backup / "candles_latest.db")["latest"], 456)


if __name__ == "__main__":
    unittest.main()


