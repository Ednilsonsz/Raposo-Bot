"""Full process restart after the previous R12 exits; existing SQLite is untouched."""
import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from paths_r11 import APP_DIR, LOG_DIR, RUNTIME_DIR

BASE = APP_DIR
STATE_NAME = 'resume_state_r12.json'


def _state_path(base=BASE):
    base=Path(base)
    return (RUNTIME_DIR/STATE_NAME) if base.resolve()==BASE.resolve() else (base/STATE_NAME)


def save_restart_state(config, base=BASE):
    def clean(value):
        if isinstance(value, dict):
            return {k:clean(v) for k,v in value.items()
                    if not k.startswith('_') and not any(part in k.lower()
                    for part in ('password','credential','secret','token','ssid','username'))}
        if isinstance(value, list): return [clean(v) for v in value]
        return value
    state={'version':1,'saved_at':datetime.now().isoformat(),
           'paused':True,'config':clean(config)}
    target=_state_path(base)
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(state,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)
    return target


def load_restart_state(defaults, base=BASE):
    path=_state_path(base)
    if not path.exists(): return dict(defaults)
    state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('version')!=1 or not isinstance(state.get('config'),dict):
        raise ValueError('invalid R12 restart state')
    result=dict(defaults);result.update(state['config'])
    # Risk edits saved on disk must not be replaced by the previous process's snapshot.
    if defaults.get('risk_return',{}).get('enabled'):
        for key in ('risk_return','loss_control','auto_demo','score_engine','experimental_modules','estrategy_tg','multi_setup_demo'):
            if key in defaults: result[key]=defaults[key]
    from turn_loss_control_r12 import normalize_shift_config
    normalize_shift_config(result)
    result['demo_stake']=10.0;result['expiry_minutes']=1
    return result


def wait_for_exit(pid, timeout=90):
    if pid<=0 or pid==os.getpid(): raise ValueError('invalid parent PID')
    # O Raposo roda no Windows, mas manter o helper testável em Linux evita que
    # uma validação local falhe antes de chegar ao desktop. No Windows usamos a
    # espera de processo nativa; em POSIX fazemos polling sem alterar o banco.
    if not hasattr(ctypes, 'WinDLL'):
        deadline=time.monotonic()+float(timeout)
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                # Um filho encerrado pode permanecer como zombie até o pai fazer
                # wait(); para fins de reinício ele já não é uma instância ativa.
                try:
                    state=Path(f'/proc/{pid}/stat').read_text().split()[2]
                    if state == 'Z':
                        return
                except (FileNotFoundError, OSError, IndexError):
                    return
            except ProcessLookupError:
                return
            except PermissionError:
                return
            time.sleep(0.05)
        raise TimeoutError('Previous R12 still active; refusing a second engine')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.WaitForSingleObject.restype=ctypes.c_ulong
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:
        error=ctypes.get_last_error()
        if error==87: return  # The previous instance already exited.
        raise OSError(error,'Cannot verify previous R12 process exit')
    try:
        if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:
            raise TimeoutError('Previous R12 still active; refusing a second engine')
    finally:
        kernel.CloseHandle(handle)


def launch_after_exit(parent_pid, base=BASE):
    return subprocess.Popen([sys.executable,str(Path(base)/'r11_restart.py'),
                             '--wait-pid',str(parent_pid)],cwd=str(base),
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--wait-pid',type=int,required=True)
    args=parser.parse_args()
    try:
        wait_for_exit(args.wait_pid)
        subprocess.Popen([sys.executable,str(BASE/'main_v386_r11.py'),'--resume'],
                         cwd=str(BASE),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except Exception as exc:
        LOG_DIR.mkdir(parents=True,exist_ok=True)
        with (LOG_DIR/'restart_error_r12.log').open('a',encoding='utf-8') as stream:
            stream.write(datetime.now().isoformat()+' '+repr(exc)+'\n')
        raise


if __name__=='__main__': main()
