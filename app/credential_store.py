import base64, ctypes, json, os
from ctypes import wintypes
from pathlib import Path
from paths_r11 import CREDENTIALS_PATH

BASE = Path(__file__).resolve().parent
STORE = CREDENTIALS_PATH

class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

def _protect(text: str) -> str:
    if not text:
        return ''
    raw = text.encode('utf-8')
    if os.name != 'nt':
        return base64.b64encode(raw).decode('ascii')
    inp, _ = _blob(raw)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), 'Bullex Robot', None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        enc = ctypes.string_at(out.pbData, out.cbData)
        return base64.b64encode(enc).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)

def _unprotect(token: str) -> str:
    if not token:
        return ''
    data = base64.b64decode(token.encode('ascii'))
    if os.name != 'nt':
        return data.decode('utf-8')
    inp, _ = _blob(data)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        return ''
    try:
        raw = ctypes.string_at(out.pbData, out.cbData)
        return raw.decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)

def load_credentials():
    try:
        obj = json.loads(STORE.read_text(encoding='utf-8'))
        return obj.get('username',''), _unprotect(obj.get('password',''))
    except Exception:
        return '', ''

def save_credentials(username: str, password: str):
    obj = {'username': username or '', 'password': _protect(password or '')}
    STORE.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
