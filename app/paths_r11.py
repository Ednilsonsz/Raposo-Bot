"""Canonical filesystem layout for the Raposo R11 installation."""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RAPOSO_DIR = APP_DIR.parent
ROBO_ROOT = RAPOSO_DIR.parent
DATA_DIR = Path(os.environ.get("RAPOSO_DATA_ROOT", str(ROBO_ROOT / "Raposo_Data"))).resolve()
CONFIG_DIR = RAPOSO_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
RELEASE_CONFIG_PATH = CONFIG_DIR / "config_v386_release.json"
ASSET_LABELS_PATH = CONFIG_DIR / "asset_labels.json"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.dat"
DATABASE_DIR = DATA_DIR / "database"
DB_PATH = DATABASE_DIR / "candles.db"
LATEST_DB_PATH = DATABASE_DIR / "candles_latest.db"
LOG_DIR = DATA_DIR / "logs"
RUNTIME_DIR = DATA_DIR / "runtime"
REPORTS_DIR = DATA_DIR / "reports"
HISTORY_DIR = DATA_DIR / "history"
BACKUPS_DIR = DATA_DIR / "backups"
UPDATES_DIR = ROBO_ROOT / "Updates"

