import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
"""Validação somente leitura dos módulos V3.85 contra um candles.db existente."""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from score_engine_v386_r11 import didi_signal_new, double_pullback_new
from strategy_modules_v386_r11 import candle_flow, revz, sr_nivel


def main(path):
    database = Path(path).resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(candles)")}
    required = {"timestamp", "asset_id", "timeframe", "open", "high", "low", "close"}
    if not required.issubset(columns):
        raise RuntimeError(f"schema candles incompatível; faltam {sorted(required - columns)}")
    assets = [str(row[0]) for row in connection.execute(
        "SELECT DISTINCT asset_id FROM candles WHERE timeframe=60 ORDER BY asset_id"
    ).fetchall()]
    counts = Counter()
    processed = 0
    for asset in assets:
        rows = connection.execute("""
            SELECT timestamp,open,high,low,close FROM candles
             WHERE asset_id=? AND timeframe=60 ORDER BY timestamp DESC LIMIT 5000
        """, (asset,)).fetchall()[::-1]
        for index in range(121, len(rows)):
            window = rows[max(0, index - 180):index + 1]
            checks = {
                "DIDI_NEW": didi_signal_new(window),
                "DP_NEW": double_pullback_new(window),
                "CANDLE_FLOW": candle_flow(window, {"min_confirmations": 4}, 0.88)["signal"],
                "REVZ": revz(window, {"lookback": 120, "z_threshold": 2.0})["signal"],
                "SR_NIVEL": sr_nivel(window, {"lookback": 60, "atr_tolerance": 0.25, "min_touches": 2})["signal"],
            }
            for name, signal in checks.items():
                if signal:
                    counts[name] += 1
            processed += 1
    print(json.dumps({"database": str(database), "assets": len(assets), "windows": processed,
                      "signals": dict(sorted(counts.items()))}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("uso: py -3 tests/offline_validate_v385.py CAMINHO_CANDLES_DB")
    main(sys.argv[1])


