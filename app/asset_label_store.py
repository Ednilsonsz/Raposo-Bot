"""Atomic human-label cache; corrupt cache is recoverable from the catalog."""
import json
import os
import threading
from pathlib import Path
_LOCK = threading.Lock()

def remember_label(path, asset_id, label, catalog_rows=()):
    path = Path(path)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError('label cache must be an object')
        except (ValueError, UnicodeError):
            if path.exists():
                damaged = path.with_suffix('.corrupt.json')
                if not damaged.exists():
                    damaged.write_bytes(path.read_bytes())
            data = {str(key): value for key, value in catalog_rows}
        data[str(asset_id)] = label
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
        return True
