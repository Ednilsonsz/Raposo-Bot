"""Catalogo persistente do dashboard: nosso ID, nome e active_id da Bullex."""
from datetime import datetime


def ensure_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS asset_catalog (
      our_asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
      asset_name TEXT NOT NULL UNIQUE,
      asset_key TEXT NOT NULL UNIQUE,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS asset_bullex_ids (
      our_asset_id INTEGER NOT NULL,
      bullex_active_id TEXT NOT NULL UNIQUE,
      confirmed_at TEXT NOT NULL,
      confirmed_by TEXT NOT NULL DEFAULT 'runtime',
      PRIMARY KEY (our_asset_id, bullex_active_id),
      FOREIGN KEY (our_asset_id) REFERENCES asset_catalog(our_asset_id)
    );
    CREATE VIEW IF NOT EXISTS ativos AS
      SELECT c.our_asset_id AS My_ID, b.bullex_active_id AS ID_BULLEX,
             c.asset_name AS NOME
      FROM asset_catalog c LEFT JOIN asset_bullex_ids b
        ON b.our_asset_id=c.our_asset_id;
    """)
    cols = {row[1] for row in db.execute("PRAGMA table_info(demo_orders)")}
    if cols:
        for name, definition in (
            # Migrações aditivas para bases R6 antigas. cycle_start é usado
            # pelo executor, pelo reconciliador e pelo Dashboard.
            ("cycle_start", "INTEGER"),
            ("our_asset_id", "INTEGER"),
            ("asset_name", "TEXT"),
            ("bullex_active_id", "TEXT"),
        ):
            if name not in cols:
                db.execute(f"ALTER TABLE demo_orders ADD COLUMN {name} {definition}")
    db.commit()


def _key(name):
    return ''.join(ch.lower() if ch.isalnum() else '_' for ch in str(name)).strip('_')


def is_asset_name(name):
    label = str(name or '').strip().upper()
    return bool(label) and not label.startswith(('ATIVO ', 'HISTÓRICO ', 'HISTORICO ')) and label not in {'NAO IDENTIFICADO', 'NÃO IDENTIFICADO', 'CARREGANDO'}


def resolve(db, name, bullex_active_id, *, confirmed_by='runtime'):
    """Resolve or create our stable ID; never reuses another name for an ID."""
    label=' '.join(str(name or '').replace('(', ' (').split())
    if not is_asset_name(label):
        return None
    now=datetime.now().isoformat(timespec='seconds')
    key=_key(label)
    existing = db.execute('''SELECT c.our_asset_id,c.asset_name FROM asset_bullex_ids b
        JOIN asset_catalog c ON c.our_asset_id=b.our_asset_id WHERE b.bullex_active_id=?''',
        (str(bullex_active_id or ''),)).fetchone()
    if existing and not is_asset_name(existing[1]):
        named = db.execute('SELECT our_asset_id FROM asset_catalog WHERE asset_key=?', (key,)).fetchone()
        if named:
            db.execute('UPDATE asset_bullex_ids SET our_asset_id=? WHERE bullex_active_id=?', (named[0], str(bullex_active_id)))
        else:
            db.execute('UPDATE asset_catalog SET asset_name=?,asset_key=? WHERE our_asset_id=?', (label, key, existing[0]))
    row=db.execute('SELECT our_asset_id FROM asset_catalog WHERE asset_key=?',(key,)).fetchone()
    if row:
        our_id=int(row[0])
    else:
        db.execute('INSERT INTO asset_catalog(asset_name,asset_key,created_at,updated_at) VALUES(?,?,?,?)',(label,key,now,now))
        our_id=int(db.execute('SELECT our_asset_id FROM asset_catalog WHERE asset_key=?',(key,)).fetchone()[0])
    if bullex_active_id not in (None,'','None'):
        aid=str(bullex_active_id)
        conflict=db.execute('SELECT our_asset_id FROM asset_bullex_ids WHERE bullex_active_id=?',(aid,)).fetchone()
        if not conflict or int(conflict[0])==our_id:
            db.execute('INSERT OR IGNORE INTO asset_bullex_ids(our_asset_id,bullex_active_id,confirmed_at,confirmed_by) VALUES(?,?,?,?)',(our_id,aid,now,confirmed_by))
    db.execute('UPDATE asset_catalog SET updated_at=? WHERE our_asset_id=?',(now,our_id))
    db.commit()
    return {'our_asset_id':our_id,'asset_name':label,'bullex_active_id':str(bullex_active_id or '')}


def lookup(db, bullex_active_id):
    if bullex_active_id in (None,'','None'):
        return None
    row=db.execute('''SELECT c.our_asset_id,c.asset_name,b.bullex_active_id
      FROM asset_bullex_ids b JOIN asset_catalog c ON c.our_asset_id=b.our_asset_id
      WHERE b.bullex_active_id=?''',(str(bullex_active_id),)).fetchone()
    return {'our_asset_id':int(row[0]),'asset_name':row[1],'bullex_active_id':row[2]} if row and is_asset_name(row[1]) else None
