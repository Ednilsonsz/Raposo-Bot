import os, shutil, sqlite3, threading, time
from pathlib import Path
from datetime import datetime

REQUIRED_TABLES={'candles','decisions','demo_orders','bullex_live_state'}


def db_info(path):
    """Valida o banco sem fazer uma varredura integral de varios gigabytes."""
    path=Path(path)
    if not path.exists() or path.stat().st_size < 100_000:
        return None
    con=None
    try:
        # mode=ro evita criar WAL/SHM ou disputar uma transacao de escrita apenas
        # para decidir se o arquivo local pode ser usado no startup.
        uri='file:'+path.resolve().as_posix()+'?mode=ro'
        con=sqlite3.connect(uri,uri=True,timeout=2)
        con.execute('PRAGMA query_only=ON')
        con.execute('PRAGMA busy_timeout=2000')
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES.issubset(tables):
            return None
        if con.execute('SELECT 1 FROM candles LIMIT 1').fetchone() is None:
            return None
        # timestamp participa da chave/indice de candles e MAX e barato; COUNT(*)
        # e integrity_check percorriam todo o banco de 3,7 GB a cada abertura.
        latest=con.execute('SELECT MAX(timestamp) FROM candles').fetchone()[0]
        return {'size':path.stat().st_size,'latest':int(latest or 0),'tables':tables}
    except Exception:
        return None
    finally:
        try:
            if con: con.close()
        except Exception: pass


def restore_if_needed(source_db, folder):
    """Só restaura quando o banco canônico local é inválido. Nunca substitui um banco local válido."""
    source=Path(source_db); folder=Path(folder) if folder else None
    if db_info(source):
        return False, 'LOCAL_OK'
    if not folder:
        return False, 'SEM_DRIVE'
    candidates=[folder/'candles.db', folder/'candles_latest.db']
    valid=[]
    for p in candidates:
        info=db_info(p)
        if info: valid.append((info['latest'],info['size'],p))
    if not valid:
        return False, 'SEM_BACKUP_VALIDO'
    _,_,best=max(valid)
    source.parent.mkdir(parents=True,exist_ok=True)
    tmp=source.with_suffix('.restore.tmp')
    shutil.copy2(best,tmp)
    if not db_info(tmp):
        try: tmp.unlink()
        except: pass
        return False, 'BACKUP_INVALIDO'
    os.replace(tmp,source)
    return True, f'RESTAURADO:{best.name}'


class DriveDBSync:
    def __init__(self, source_db, folder, interval=300):
        self.source=Path(source_db); self.folder=Path(folder) if folder else None
        self.interval=max(300,int(interval)); self.stop_evt=threading.Event(); self.last_status='AGUARDANDO'; self.last_file=''
        self._snapshot_lock=threading.Lock(); self._thread=None
    def snapshot(self):
        if not self.folder: return False
        if not self._snapshot_lock.acquire(blocking=False):
            self.last_status='IGNORADO: snapshot ja em andamento'
            return False
        self.folder.mkdir(parents=True,exist_ok=True)
        latest=self.folder/'candles_latest.db'; tmp=self.folder/'candles_latest.tmp.db'
        src=None; dst=None
        try:
            srcinfo=db_info(self.source)
            if not srcinfo:
                self.last_status='IGNORADO: banco local invalido/vazio'
                return False
            if tmp.exists(): tmp.unlink()
            src=sqlite3.connect(str(self.source),timeout=20)
            dst=sqlite3.connect(str(tmp),timeout=20)
            src.execute('PRAGMA busy_timeout=20000')

            def progress(status, remaining, total):
                if self.stop_evt.is_set():
                    raise RuntimeError('snapshot cancelado no encerramento')
                # Copia em blocos e cede tempo ao capturador/analisador.
                time.sleep(.003)

            src.backup(dst,pages=2048,progress=progress,sleep=.10)
            dst.close(); dst=None
            src.close(); src=None
            tmpinfo=db_info(tmp)
            if not tmpinfo:
                raise RuntimeError('snapshot temporario invalido')
            oldinfo=db_info(latest)
            # Nunca publique um snapshot com menos histórico que o último backup válido.
            if oldinfo and tmpinfo['latest'] < oldinfo['latest']:
                self.last_status=f"IGNORADO: historico menor ({tmpinfo['latest']}<{oldinfo['latest']})"
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp,latest)
            self.last_status='OK '+datetime.now().strftime('%H:%M:%S'); self.last_file=str(latest)
            return True
        except Exception as e:
            self.last_status='ERRO: '+str(e)[:100]
            try: tmp.unlink(missing_ok=True)
            except: pass
            return False
        finally:
            try:
                if dst: dst.close()
            except Exception: pass
            try:
                if src: src.close()
            except Exception: pass
            self._snapshot_lock.release()
    def run(self):
        # Nao copie 3,7 GB no exato momento em que Chrome, captura e analisador
        # estao subindo. O primeiro snapshot ocorre somente apos o intervalo.
        while not self.stop_evt.wait(self.interval):
            self.snapshot()
    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._thread=threading.Thread(target=self.run,daemon=True,name='DriveDBSync')
        self._thread.start()
    def stop(self):
        self.stop_evt.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
