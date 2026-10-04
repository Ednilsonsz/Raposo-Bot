import sqlite3
from pathlib import Path
from paths_r11 import DB_PATH as DB, DATABASE_DIR

DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_runtime_schema(c):
    """Migrações aditivas executadas após qualquer restore do banco."""
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'demo_orders' not in tables:
        return
    cols = {row[1] for row in c.execute("PRAGMA table_info(demo_orders)")}
    if 'cycle_start' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN cycle_start INTEGER")
        if 'decisions' in tables:
            c.execute("""UPDATE demo_orders
                         SET cycle_start=(SELECT d.cycle_start FROM decisions d
                                          WHERE d.id=demo_orders.decision_id)
                         WHERE cycle_start IS NULL""")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.cycle_start criada.', flush=True)
    if 'candle_exec' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN candle_exec INTEGER")
        c.execute("""UPDATE demo_orders
                     SET candle_exec=((cycle_start % 300) / 60) + 1
                     WHERE candle_exec IS NULL AND cycle_start IS NOT NULL""")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.candle_exec criada.', flush=True)
    if 'build_revision' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN build_revision TEXT")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.build_revision criada.', flush=True)
    if 'direction_original' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN direction_original TEXT")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.direction_original criada.', flush=True)
    if 'direction_executed' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN direction_executed TEXT")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.direction_executed criada.', flush=True)
    if 'candle_flow_inverted' not in cols:
        c.execute("ALTER TABLE demo_orders ADD COLUMN candle_flow_inverted INTEGER NOT NULL DEFAULT 0")
        c.commit()
        print('[DB] Migracao aditiva: demo_orders.candle_flow_inverted criada.', flush=True)


def connect():
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.execute("PRAGMA busy_timeout=30000")
    # Reaplicar journal_mode=WAL em toda nova conexao pede um lock exclusivo e
    # pode disputar com captura, analisador e backup. So altere se necessario.
    mode=c.execute("PRAGMA journal_mode").fetchone()
    if not mode or str(mode[0]).lower() != 'wal':
        c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA wal_autocheckpoint=2000")
    # O catalogo e as colunas de snapshot sao migracoes aditivas: nao removem
    # nem reescrevem historico existente.
    try:
        from asset_catalog import ensure_schema
        ensure_schema(c)
    except Exception:
        # O banco base pode ainda estar sendo criado por outro modulo; a
        # inicializacao posterior do executor/broker repete a migracao.
        pass
    try:
        _ensure_runtime_schema(c)
    except Exception as e:
        print('[DB] Falha na migracao runtime:', repr(e), flush=True)
    return c
