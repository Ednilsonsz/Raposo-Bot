import json, re, subprocess, sys, time, os
from pathlib import Path
from paths_r11 import APP_DIR, ROBO_ROOT, CONFIG_PATH, LOG_DIR, UPDATES_DIR

BASE = APP_DIR
DRIVE_ROOT = ROBO_ROOT
DRIVE_DIAG = LOG_DIR/'update_deploy.log'
DRIVE_UPDATES = UPDATES_DIR
DRIVE_APP_NEW = DRIVE_ROOT/'Raposo'
DRIVE_APP_OLD = DRIVE_ROOT/'backup'

def diag(msg):
    line=time.strftime('%Y-%m-%d %H:%M:%S')+' | '+str(msg)+'\n'
    for p in (LOG_DIR/'update_deploy.log', DRIVE_DIAG):
        try:
            p.parent.mkdir(parents=True,exist_ok=True)
            with p.open('a',encoding='utf-8') as f: f.write(line)
        except Exception: pass

def ver_tuple(s):
    nums = re.findall(r'\d+', str(s))
    return tuple(int(x) for x in nums[:4]) or (0,)

def current_version():
    try:
        v=(BASE/'VERSION').read_text(encoding='utf-8').strip()
        if v: return v
    except Exception: pass
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8')).get('version','0')
    except Exception:
        return '0'

def _candidate_roots(folder=None):
    roots=[]; seen=set()
    def add(p):
        if not p: return
        try:
            p=Path(p)
            for r in (p/'Updates', p, p.parent/'Updates', p.parent, p.parent.parent/'Updates'):
                k=str(r).lower()
                if k not in seen:
                    seen.add(k); roots.append(r)
        except Exception: pass
    add(folder)
    try:
        cfg=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        add((cfg.get('drive_sync') or {}).get('folder'))
    except Exception: pass
    if os.name=='nt':
        add(DRIVE_UPDATES); add(DRIVE_ROOT/'Dados'); add(DRIVE_ROOT)
    return roots

def _stable_zip(p):
    try:
        s1=p.stat().st_size; time.sleep(.08); s2=p.stat().st_size
        return s1 >= 1024 and s1 == s2
    except Exception:
        return False

def find_update(folder=None):
    diag('CHECK current='+current_version())
    try: (LOG_DIR/'updater_heartbeat.log').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' | current='+current_version(),encoding='utf-8')
    except Exception: pass
    cand=[]
    for root in _candidate_roots(folder):
        try:
            if not root.exists(): continue
            for p in root.glob('bullex_update_v*.zip'):
                if p.name.endswith(('.part','.tmp')) or not _stable_zip(p): continue
                mm=re.search(r'v([0-9][0-9._-]*)',p.name,re.I)
                if mm: cand.append((ver_tuple(mm.group(1)),p))
        except Exception: pass
    if not cand: return None
    cand.sort(reverse=True,key=lambda x:x[0])
    upd=cand[0][1] if cand[0][0] > ver_tuple(current_version()) else None
    if upd:
        diag('FOUND '+str(upd))
        try: (LOG_DIR/'updater_found.log').write_text(str(upd),encoding='utf-8')
        except Exception: pass
    return upd

def bootstrap_update_check(folder=None):
    try:
        upd=find_update(folder)
        if not upd: return False
        stage_update(upd)
        return True
    except Exception as e:
        try: (LOG_DIR/'update_error.log').write_text('bootstrap: '+repr(e),encoding='utf-8')
        except Exception: pass
        return False

def stage_update(zip_path):
    diag('STAGE '+str(zip_path))
    helper=BASE/'apply_update.py'
    flags=0
    if os.name=='nt':
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'DETACHED_PROCESS',0)
    subprocess.Popen([sys.executable,str(helper),str(zip_path),str(BASE),str(os.getpid())],
                     close_fds=(os.name!='nt'), creationflags=flags)
