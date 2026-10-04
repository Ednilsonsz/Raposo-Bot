from broker_day_r11 import today as broker_today
import json, threading, time, sys, os
from pathlib import Path
from datetime import datetime
from browser import start_bullex
from bullex_live_capture import BullexLiveCapture
from realtime_analyzer_v386_r11 import RealtimeAnalyzer
from demo_executor_v386_r11 import DemoExecutor
from broker_results_r11 import BrokerResults, scoreboard
from r11_restart import save_restart_state, load_restart_state, launch_after_exit
from database import connect as result_db_connect
from ui_overlay_massa_v386_r11 import BullexOverlay
if os.environ.get('RAPOSO_UI_VARIANT') == 'premium':
    try:
        from ui_overlay_premium_r12 import PremiumOverlay as BullexOverlay
    except Exception as exc:
        print('[R12][TELA NOVA] Falha ao carregar; usando tela atual:',repr(exc),flush=True)
from launcher_ui import LauncherUI
from drive_sync import DriveDBSync, restore_if_needed
from database import DB as DB_PATH
from login_helper import auto_fill_login
from credential_store import load_credentials
from asset_catalog import resolve as resolve_asset_catalog
from updater import bootstrap_update_check, find_update, stage_update
from mobile_server import MobileControlServer
from pause_control_r11 import select_command, synchronize_pause
from voice_announcer_r11 import configure as configure_voice, announce as voice_announce
from feed_watchdog_m1_r11 import M1FeedWatchdog
from turn_loss_control_r12 import TurnLossController
from paths_r11 import (APP_DIR, ROBO_ROOT, DATA_DIR, CONFIG_PATH,
                       RELEASE_CONFIG_PATH, ASSET_LABELS_PATH, LOG_DIR,
                       RUNTIME_DIR, DATABASE_DIR, UPDATES_DIR)

BASE=APP_DIR
DRIVE_ROOT=ROBO_ROOT
DRIVE_DATA=DATA_DIR
DRIVE_UPDATES=UPDATES_DIR
DRIVE_APP_NEW=DRIVE_ROOT/'Raposo_R12'
DRIVE_APP_OLD=DRIVE_ROOT/'backup'
RUNTIME_LOG=LOG_DIR/'bullex_r12_runtime.log'
ERROR_LOG=LOG_DIR/'bullex_r12_error.log'
LOCAL_RUNTIME_LOG=LOG_DIR/'bullex_r12_runtime_local.log'
LOCAL_ERROR_LOG=LOG_DIR/'bullex_r12_error_local.log'
_MUTEX_HANDLE=None
APP_VERSION='3.86'
BUILD_REVISION='R12'
BUILD_LABEL=f'V{APP_VERSION} · {BUILD_REVISION}'

class _Tee:
    def __init__(self,*streams): self.streams=streams
    def write(self,data):
        for x in self.streams:
            try: x.write(data); x.flush()
            except Exception: pass
        return len(data)
    def flush(self):
        for x in self.streams:
            try: x.flush()
            except Exception: pass

def _start_persistent_log():
    streams=[sys.__stdout__]
    handles=[]
    stamp=f'\n=== RAPOSO BOT {BUILD_LABEL} START '+datetime.now().isoformat(timespec='seconds')+' ===\n'

    # Sempre grava uma copia local, mesmo se o Google Drive estiver indisponivel.
    try:
        lf=open(LOCAL_RUNTIME_LOG,'a',encoding='utf-8',buffering=1)
        lf.write(stamp)
        streams.append(lf); handles.append(lf)
    except Exception:
        pass

    # Em paralelo, grava diretamente no caminho sincronizado pelo Google Drive Desktop.
    try:
        LOG_DIR.mkdir(parents=True,exist_ok=True)
        df=open(RUNTIME_LOG,'a',encoding='utf-8',buffering=1)
        df.write(stamp)
        streams.append(df); handles.append(df)
    except Exception as e:
        try:
            if len(streams)>1:
                streams[-1].write('[LOG] Drive indisponivel no startup: '+repr(e)+'\n')
        except Exception:
            pass

    sys.stdout=_Tee(*streams)
    sys.stderr=_Tee(*streams)
    return handles

def _start_log_heartbeat():
    def _beat():
        while True:
            time.sleep(60)
            try:
                print('[HEARTBEAT V3.86] vivo '+datetime.now().isoformat(timespec='seconds'), flush=True)
            except Exception:
                pass
    t=threading.Thread(target=_beat,name='bullex-log-heartbeat',daemon=True)
    t.start()
    return t

def _fatal_log(label, exc_text):
    text=f'\n[{datetime.now().isoformat(timespec="seconds")}] {label}\n{exc_text}\n'
    for target in (ERROR_LOG, LOCAL_ERROR_LOG, LOG_DIR/'startup_error.log'):
        try:
            target.parent.mkdir(parents=True,exist_ok=True)
            with open(target,'a',encoding='utf-8') as f: f.write(text)
        except Exception:
            pass

def _remember_asset_label(asset_id, label):
    """Persist only a verified human label for the read-only DEV scanner."""
    label=' '.join(str(label or '').replace('ATIVO:','').replace('ATIVO ','').split()).strip()
    if not label or label.upper() in ('COM/NS','NAO IDENTIFICADO','CARREGANDO'):
        return False
    if '/' not in label and 'OTC' not in label.upper():
        return False
    try:
        db=result_db_connect()
        resolve_asset_catalog(db,label,str(asset_id),confirmed_by='graph+websocket')
        db.close()
    except Exception as exc:
        print('[V3.86][ATIVO][CATALOGO]',repr(exc),flush=True)
    target=ASSET_LABELS_PATH
    try:
        from asset_label_store import remember_label
        return remember_label(target, asset_id, label)
    except Exception as exc:
        print('[V3.86][ATIVO][NOME]',repr(exc),flush=True)
        return False

def acquire_single_instance():
    global _MUTEX_HANDLE
    try:
        import ctypes
        kernel32=ctypes.windll.kernel32
        _MUTEX_HANDLE=kernel32.CreateMutexW(None, False, 'Global\\BullexDevRobotSingleInstance')
        if kernel32.GetLastError()==183:  # ERROR_ALREADY_EXISTS
            print('[V3.86] Outra instancia do BULLEX DEV ja esta aberta. Encerrando esta copia.', flush=True)
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'O Raposo ja esta aberto ou ainda esta iniciando.\n\nAguarde; nao clique novamente no arquivo BAT.',
                    'Raposo V3.86 R12',
                    0x30,
                )
            except Exception: pass
            return False
    except Exception as e:
        print('[V3.86] Aviso mutex:', repr(e), flush=True)
    return True

def hide_console():
    try:
        import ctypes
        hwnd=ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd: ctypes.windll.user32.ShowWindow(hwnd,0)
    except Exception: pass

def _bounded_shutdown(label, func, timeout=3.0):
    """Run cleanup without letting a slow Drive/browser call freeze STOP/RESTART."""
    started=time.monotonic()
    print(f'[R12][STOP][INICIO] {label}',flush=True)
    result={'error':None}
    def runner():
        try: func()
        except Exception as exc: result['error']=exc
    worker=threading.Thread(target=runner,daemon=True,name='R6Shutdown-'+label)
    worker.start(); worker.join(timeout)
    print(f'[R12][STOP][TEMPO] {label}={time.monotonic()-started:.3f}s',flush=True)
    if worker.is_alive():
        print(f'[V3.86][STOP][TIMEOUT] {label} excedeu {timeout:.1f}s; continuando.',flush=True)
        return False
    if result['error'] is not None:
        print(f'[V3.86][STOP][{label}] {result["error"]!r}',flush=True)
        return False
    return True

def wait_for_bullex_workspace(driver, timeout=180.0):
    """Wait for the broker workspace without starting Raposo's heavy bootstrap."""
    deadline=time.monotonic()+float(timeout)
    stable=0
    while time.monotonic()<deadline:
        try:
            state=driver.execute_script(r"""
                const visible=e=>{if(!e||!e.getBoundingClientRect)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>20&&r.height>18&&r.bottom>0&&r.right>0&&s.display!=='none'&&s.visibility!=='hidden'};
                const login=[...document.querySelectorAll('input[type=password]')].some(visible);
                const text=String(document.body&&(document.body.innerText||document.body.textContent)||'').toUpperCase();
                const up=/\b(ACIMA|CALL|UP)\b/.test(text),down=/\b(ABAIXO|PUT|DOWN)\b/.test(text);
                const canvases=[...document.querySelectorAll('canvas')].filter(visible);
                return {ready:!login&&((up&&down)||canvases.length>0),login,up,down,canvas:canvases.length,url:String(location.href)};
            """) or {}
            if state.get('ready'):
                stable+=1
                if stable>=4:
                    print(f'[{BUILD_LABEL}][BOOTSTRAP] Bullex pronta; iniciando captura e motor.',flush=True)
                    return True
            else:
                stable=0
        except Exception:
            stable=0
        time.sleep(.25)
    print(f'[{BUILD_LABEL}][BOOTSTRAP][TIMEOUT] Bullex não confirmou a área operacional em {timeout:.0f}s.',flush=True)
    return False


def acknowledge_control(driver, command_id, command, paused, error=''):
    """Publish Python's real pause state as the command acknowledgement."""
    if not command_id:
        return
    try:
        driver.execute_script(r"""
            const root=document.getElementById('bullex-pro-overlay');
            const target=root?root.dataset:document.documentElement.dataset;
            target.raposoAckId=String(arguments[0]);
            target.raposoAckCommand=String(arguments[1]||'');
            target.raposoAckPaused=arguments[2]?'true':'false';
            target.raposoAckError=String(arguments[3]||'');
            document.documentElement.dataset.raposoAckId=target.raposoAckId;
            document.documentElement.dataset.raposoAckCommand=target.raposoAckCommand;
            document.documentElement.dataset.raposoAckPaused=target.raposoAckPaused;
            document.documentElement.dataset.raposoAckError=target.raposoAckError;
        """,str(command_id),str(command or ''),bool(paused),str(error or ''))
    except Exception as exc:
        print(f'[{BUILD_LABEL}][CONTROLE][ACK_ERRO] {exc!r}',flush=True)


def main():
    if not acquire_single_instance():
        return
    _log_handles=_start_persistent_log()
    _start_log_heartbeat()
    print('[V3.86] Log Drive:', str(RUNTIME_LOG), flush=True)
    print('[V3.86] Log local:', str(LOCAL_RUNTIME_LOG), flush=True)
    # V3.69 DIAGNOSTICO: CMD permanece visivel. Login/configuracao vem ANTES do Chrome.
    cfg_path=CONFIG_PATH if CONFIG_PATH.exists() else RELEASE_CONFIG_PATH
    cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['version']=APP_VERSION
    cfg['revision']=BUILD_REVISION
    cfg['build_revision']=BUILD_LABEL
    cfg['run_mode']='LOSS_SHIFTS'
    cfg.pop('target_mode',None)

    cfg['demo_stake']=10.0
    cfg['expiry_minutes']=1

    resume_flag=RUNTIME_DIR/'resume_after_update_r12.flag'
    resume_mode=('--resume' in sys.argv) or resume_flag.exists()
    if resume_mode:
        cfg=load_restart_state(cfg)
        try: resume_flag.unlink(missing_ok=True)
        except Exception: pass
        action='arm'
        operating={'stake':float(cfg.get('demo_stake',10.0)),'expiry_minutes':1}
        drive_folder=(cfg.get('drive_sync') or {}).get('folder','')
        run_mode='LOSS_SHIFTS'
        target=cfg.get('loss_control') or {}
        try: username,password=load_credentials()
        except Exception: username,password='',''
    else:
        # PRIMEIRO: interface de login/configuracao. Nenhum Chrome/motor existe ainda.
        action, drive_folder, run_mode, target, operating, username, password=LauncherUI(cfg).run()
        if action!='arm':
            return
        cfg['run_mode']=run_mode
        cfg['loss_control']=target or cfg.get('loss_control') or {}
        if operating and 'daily_net_loss_minor' in operating:
            cfg.setdefault('risk_return',{})['daily_net_loss_minor']=operating['daily_net_loss_minor']
        cfg.pop('target_mode',None)
        cfg['demo_stake']=10.0
        cfg['expiry_minutes']=1
        if drive_folder:
            cfg.setdefault('drive_sync',{})['folder']=drive_folder
        try: CONFIG_PATH.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception: pass

    # Banco ativo e snapshots ficam juntos na raiz Raposo_Data/database.
    drive_folder=str(DATABASE_DIR)
    cfg.setdefault('drive_sync',{})['folder']=drive_folder
    try: CONFIG_PATH.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception: pass

    # SEGUNDO: restaura o banco ANTES de qualquer componente abrir conexao SQLite.
    # Na V3.69 a captura era criada primeiro e o restore podia substituir candles.db
    # depois, deixando a captura presa ao arquivo antigo (inode antigo).
    if drive_folder:
        try:
            restore_if_needed(DB_PATH,drive_folder)
            print('[V3.86] Banco validado/restaurado antes da captura.', flush=True)
        except Exception as e:
            print('[V3.86] Restore DB:', repr(e), flush=True)

    # TERCEIRO: abre a Bullex sem captura, banco, motor ou overlay concorrendo
    # com a montagem da tela após o login.
    print(f'[{BUILD_LABEL}] Login confirmado. Abrindo Chrome...', flush=True)
    driver=start_bullex(cfg.get('broker_url','https://trade.bull-ex.com/traderoom'),
        startup_script=(BASE/'broker_history_transport_r11.js').read_text(encoding='utf-8-sig'))
    try: auto_fill_login(driver,username,password,timeout=30)
    except Exception as e: print(f'[{BUILD_LABEL}] Login helper:', repr(e), flush=True)
    if not wait_for_bullex_workspace(driver,timeout=180):
        raise RuntimeError('Bullex não concluiu o carregamento da área operacional')
    print(f'[{BUILD_LABEL}] Bullex carregada. Conectando captura...', flush=True)

    try:
        configure_voice(cfg)
        capture=BullexLiveCapture(driver)
        executor=DemoExecutor(driver,cfg)
        loss_controller=TurnLossController(executor.db,cfg)
        executor.set_loss_controller(loss_controller)
        executor.set_selected_asset_provider(capture.selected_asset_snapshot)
        results=BrokerResults(result_db_connect(),cfg)
        results.observe_executor(executor)
        capture.results=results
    except Exception as e:
        print('[V3.86] Falha captura/executor:', repr(e), flush=True)
        capture=None
        executor=None

    for _ in range(80):
        if BullexOverlay.inject_shell(driver,BUILD_LABEL): break
        time.sleep(.1)
    BullexOverlay.set_diag(driver,'Bullex pronta; iniciando Raposo…','INICIALIZANDO')
    overlay=None
    # A interface nasce somente depois da área operacional da Bullex estar pronta.
    try:
        overlay=BullexOverlay(driver,cfg,None)
        overlay.executor=executor
        overlay.update(force=True)
    except Exception as e:
        overlay=None
        print('[V3.86] Overlay pre-login:', repr(e), flush=True)

    # V3.72: a expiraÃ§Ã£o serÃ¡ configurada/confirmada SOMENTE quando o feed M1
    # estiver vivo. O motor nÃ£o arma antes dessa confirmaÃ§Ã£o.
    expiry_ready=False
    buttons_ready=False
    # Marco da sessão para diagnóstico; o STOP LOSS é sempre reconstruído do banco.
    session_started=datetime.now().isoformat(timespec='seconds')
    cfg['_session_started_at']=session_started
    BullexOverlay.set_diag(driver,'login/sessÃ£o prontos; carregando bancoâ€¦','INICIALIZANDO')

    try:
        if overlay is None:
            overlay=BullexOverlay(driver,cfg,None)
        else:
            overlay.cfg=cfg
        overlay.executor=executor
        overlay.update(force=True)
    except Exception as e:
        overlay=None
        BullexOverlay.set_diag(driver,'painel sem vÃ­nculo: '+type(e).__name__,'PAINEL ATIVO')
        print('[V3.86] Overlay:', repr(e), flush=True)

    # Serve read-only Dashboard/LOG before waiting for the M1 feed.
    mobile=None
    try:
        mcfg=cfg.setdefault('mobile_control',{})
        mobile_enabled=bool(mcfg.get('enabled',False))
        desktop_port=8765
        try:
            from urllib.parse import urlparse
            access=(RUNTIME_DIR/'ACESSO_CELULAR.txt').read_text(encoding='utf-8')
            previous_url=next(line[5:].strip() for line in access.splitlines() if line.startswith('URL: '))
            desktop_port=urlparse(previous_url).port or desktop_port
        except Exception:
            pass
        mobile=MobileControlServer(cfg,executor,None,
                                   host='0.0.0.0' if mobile_enabled else '127.0.0.1',
                                   port=int(mcfg.get('port',8765)) if mobile_enabled else desktop_port)
        murl,mpin=mobile.start()
        cfg['_control_url']=f'http://127.0.0.1:{mobile.port}/api/control'
        cfg['_control_pin']=mpin
        if int(mobile.port) <= 0:
            raise RuntimeError('Servidor local retornou uma porta inválida para o Dashboard')
        cfg['_dashboard_url']=f'http://127.0.0.1:{mobile.port}/dashboard.html?pin={mpin}'
        cfg['_log_url']=f'http://127.0.0.1:{mobile.port}/log.html?pin={mpin}'
        if mobile_enabled:
            cfg['_mobile_url']=murl; cfg['_mobile_pin']=mpin
            try: CONFIG_PATH.write_text(json.dumps({k:v for k,v in cfg.items() if not str(k).startswith('_')},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception: pass
            print(f'[V3.86][MOBILE] {murl} | PIN {mpin}',flush=True)
        print(f'[V3.86][CONTROLE] canal local pronto em 127.0.0.1:{mobile.port}',flush=True)
        try:
            if overlay: overlay.update(force=True)
        except Exception: pass
    except Exception as e:
        print('[V3.86][MOBILE] Falha:',repr(e),flush=True)
        mobile=None

    live_ok=False
    try:
        if capture is not None:
            deadline=time.time()+25
            while time.time()<deadline:
                capture.poll()
                initial_selected=capture.selected_asset_snapshot(force=True)
                initial_id=str(initial_selected.get('asset_id') or '')
                st0=capture.db.execute('SELECT current_from FROM bullex_live_state WHERE asset_id=?',(initial_id,)).fetchone() if initial_id else None
                # O feed M1 identifica o ativo operacional. O cabecalho visual serve
                # para validar/exibir o nome, mas a ordem e feita por clique na tela.
                if st0:
                    cfg['asset_id']=initial_id
                    cfg['_asset_label']=initial_selected.get('label') or ''
                    live_ok=True
                    break
                time.sleep(.1)
        BullexOverlay.set_diag(driver,'feed M1 conectado' if live_ok else 'aguardando feed M1â€¦','PAUSADO')
    except Exception as e:
        BullexOverlay.set_diag(driver,'captura inicial: '+repr(e)[:120],'ERRO CAPTURA')
        print('[V3.86] Captura inicial:', repr(e), flush=True)

    sync=None
    if cfg.get('drive_sync',{}).get('enabled') and drive_folder:
        sync=DriveDBSync(DB_PATH,drive_folder,cfg.get('drive_sync',{}).get('interval_seconds',300)); sync.start()

    analyzer=None
    if executor is not None:
        try:
            analyzer=RealtimeAnalyzer(cfg,executor)
            if overlay is None:
                overlay=BullexOverlay(driver,cfg,analyzer)
            else:
                selected_now=capture.selected_asset_snapshot(force=True) if capture is not None else {}
                overlay.cfg=cfg; overlay.analyzer=analyzer; overlay.set_asset(str(selected_now.get('asset_id') or ''), selected_now.get('label'))
            overlay.update(force=True)
            BullexOverlay.set_diag(driver,'dados vinculados; motor aguardando mercado','DEMO ARMADO')
        except Exception as e:
            BullexOverlay.set_diag(driver,'painel ativo; falha do motor: '+repr(e)[:100],'PAINEL ATIVO')
            print('[V3.86] Motor:', repr(e), flush=True)
    # V3.86 DESKTOP: mobile is optional and disabled by default; never changes desktop capture/execution.
    print('[V3.86][DESKTOP] Execucao principal: DESKTOP | Chrome/Selenium | M1', flush=True)
    # Manual INICIAR DEMO means active analysis; restart recovery remains fail-safe paused.
    user_paused=bool(resume_mode)
    cfg['_user_paused']=user_paused
    print('[V3.86][START]', 'reinício recuperado; PAUSADO' if user_paused else
          'INICIAR DEMO manual; análise ativa; ordens aguardam todas as validações de segurança', flush=True)
    feed_frozen=False
    cfg['_feed_m1_frozen']=False
    asset_verified=False

    def fast_control(command, command_id):
        """Apply pause state without waiting for Selenium/capture/dashboard I/O."""
        nonlocal user_paused
        command=str(command or '').upper()
        if command=='PAUSE':
            user_paused=True; cfg['_user_paused']=True
            if analyzer: analyzer.paused=True
            if executor: executor.disarm()
            return {'paused':True,'error':''}
        if command=='RESUME':
            if analyzer is None or executor is None:
                return {'paused':True,'error':'MOTOR_INDISPONIVEL'}
            try:
                row=capture.db.execute('SELECT current_from FROM bullex_live_state WHERE asset_id=?',(str(capture.asset_id),)).fetchone() if capture else None
                current=int(row[0]) if row else 0
            except Exception:
                current=0
            analyzer.block_until_current_from=(current//60)*60+60 if current else 0
            # O canal rápido não pode executar OCR, Selenium pesado ou consultar o
            # dashboard. Apenas publica o estado lógico; o loop principal faz a
            # validação visual/rearmamento no ciclo normal, sem bloquear o comando.
            user_paused=False; cfg['_user_paused']=False; analyzer.paused=False
            if executor: executor.disarm()
            return {'paused':False,'error':''}
        return {'paused':bool(user_paused),'error':'COMMAND'}

    if mobile is not None:
        mobile.executor=executor
        mobile.analyzer=analyzer
        mobile.set_control_handler(fast_control)

    restart_requested=False
    restart_pending=False
    restart_wait_started=None
    analyzer_started=False
    # session_started foi definido antes da criacao do overlay.
    next_update_check=time.monotonic()+10
    last_asset_label=None
    feed_watchdog=M1FeedWatchdog(cfg)
    arm_retry_at=0.0
    try:
        driver.execute_script('window.__RAPOSO_PAUSE_REQUEST__=arguments[0];',bool(user_paused))
    except Exception:
        pass
    if analyzer is not None:
        analyzer.paused=bool(user_paused)
    if executor is not None:
        executor.disarm()

    try:
        last_overlay_command_id = ''
        while True:
            # Canal rápido e explícito. O ID é globalmente único e não reinicia
            # quando o overlay é recriado; PAUSE/RESUME não dependem do Dashboard.
            remote_cmd=mobile.pop_command() if mobile else None
            overlay_cmd = None
            overlay_command_id = ''
            try:
                cmd_state = driver.execute_script("""
                    const r=document.getElementById('bullex-pro-overlay');
                    const command=(r&&r.dataset.raposoCommand)||document.documentElement.dataset.raposoCommand||
                        (window.__RAPOSO_RESTART_REQUEST__===true?'RESTART':'');
                    const id=(r&&r.dataset.raposoCommandId)||document.documentElement.dataset.raposoCommandId||'';
                    return command&&id?[command,id]:null;
                """)
                if cmd_state and len(cmd_state) >= 2:
                    command_id=str(cmd_state[1] or '')
                    if command_id and command_id != last_overlay_command_id:
                        last_overlay_command_id=command_id
                        overlay_command_id=command_id
                        overlay_cmd=str(cmd_state[0] or '').upper()
            except Exception:
                overlay_cmd=None
            overlay_cmd=select_command(overlay_cmd,remote_cmd)
            if overlay_cmd=='RESTART':
                try:
                    try: driver.execute_script("window.__RAPOSO_RESTART_REQUEST__=false; window.__BULLEX_ROBOT_STOP_REQUEST__=false; delete document.documentElement.dataset.raposoCommand; delete document.documentElement.dataset.raposoStopRequest;")
                    except Exception: pass
                    save_restart_state(cfg)
                    restart_pending=True;user_paused=True
                    restart_wait_started=time.monotonic()
                    if analyzer: analyzer.paused=True
                    if executor: executor.disarm()
                    print('[R12][RESTART] Estado salvo; aguardando executor ficar livre.',flush=True)
                except Exception as exc:
                    print('[R12][RESTART][ERRO]',repr(exc),flush=True)
                    try: driver.execute_script("const b=document.getElementById('bxrestart');if(b){b.disabled=false;b.textContent='TENTAR REINICIAR';}")
                    except Exception: pass
            if restart_pending:
                lock=getattr(executor,'lock',None) if executor is not None else None
                idle=executor is None or lock is None or lock.acquire(blocking=False)
                if idle:
                    if lock: lock.release()
                    print('[R12][RESTART] Executor livre; iniciando reinicio.',flush=True)
                    restart_requested=True
                    break
                if restart_wait_started is not None and time.monotonic()-restart_wait_started>=8.0:
                    print('[R12][RESTART][TIMEOUT] Executor nao liberou em 8s; reinicio seguro forcado com executor desarmado.',flush=True)
                    restart_requested=True
                    break
            try: request_stop=bool(overlay_cmd=='STOP' or driver.execute_script("return window.__BULLEX_ROBOT_STOP_REQUEST__ === true || document.documentElement.dataset.raposoStopRequest === 'true';"))
            except: request_stop=(overlay_cmd=='STOP')
            if request_stop:
                try: driver.execute_script("window.__BULLEX_ROBOT_STOP_REQUEST__=false; delete document.documentElement.dataset.raposoStopRequest;")
                except: pass
                print('[V3.86][STOP] Encerramento solicitado pela interface.', flush=True)
                break
            # One pause state for desktop/mobile, even when Selenium fails.
            control_error=''
            if overlay_cmd in ('VOICE_ON','VOICE_OFF'):
                cfg.setdefault('voice_alerts',{})['enabled']=(overlay_cmd=='VOICE_ON')
                try: CONFIG_PATH.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception: pass
                configure_voice(cfg)
                print('[V3.86][VOZ]', 'ATIVADA' if cfg['voice_alerts']['enabled'] else 'DESATIVADA', flush=True)
                overlay_cmd=None
            req_pause=synchronize_pause(driver,user_paused,overlay_cmd,analyzer,executor)
            if overlay_cmd=='RESUME' and analyzer is None:
                req_pause=True
                control_error='MOTOR_INDISPONIVEL'
            analyzer_state_mismatch=bool(analyzer is not None and getattr(analyzer,'paused',user_paused) != req_pause)
            if req_pause != user_paused or analyzer_state_mismatch:
                user_paused=req_pause
                if user_paused:
                    if analyzer: analyzer.paused=True
                    if executor: executor.disarm()
                    try: BullexOverlay.set_diag(driver,'pausado; nenhuma ordem sera enviada','PAUSADO')
                    except Exception: pass
                else:
                    # Ao retomar, nunca usa o candle parcial atual. A estrategia so
                    # fica elegivel a partir do proximo M1 completo observado.
                    try:
                        resume_st=capture.db.execute('SELECT current_from FROM bullex_live_state WHERE asset_id=?',(str(capture.asset_id),)).fetchone() if capture else None
                        cur=int(resume_st[0]) if resume_st else 0
                    except Exception:
                        cur=0
                    analyzer.block_until_current_from=(cur//60)*60+60 if cur else 0
                    analyzer.paused=False
                    ready=bool(executor and executor.safety_ready())
                    if ready: executor.arm()
                    elif executor: executor.disarm()
                    try: BullexOverlay.set_diag(driver,'retomado; aguardando proximo M1 completo' if ready else 'retomado; analise ativa, ordens bloqueadas ate seguranca/feed OK','DEMO ARMADO' if ready else 'ANALISE ATIVA')
                    except Exception: pass
                print('[V3.86][PAUSA]', 'PAUSADO' if user_paused else 'ATIVO', flush=True)
                voice_announce('paused' if user_paused else 'resumed')
            cfg['_user_paused']=bool(user_paused)
            if overlay_cmd in ('PAUSE','RESUME') and overlay_command_id:
                expected=(overlay_cmd=='PAUSE')
                if bool(user_paused)!=expected and not control_error:
                    control_error='ESTADO_NAO_CONFIRMADO'
                acknowledge_control(driver,overlay_command_id,overlay_cmd,user_paused,control_error)

            # Run before every early continue: validate session and recreate the panel.
            if executor is not None and not executor.session_valid():
                executor.disarm(); executor.runtime_ready=False
                user_paused=True; buttons_ready=False; expiry_ready=False
                if analyzer: analyzer.paused=True
                print('[SEGURANCA] sessao perdida/navegacao; PAUSADO. Recriando Chrome.', flush=True)
                try: driver.quit()
                except Exception: pass
                try:
                    driver=start_bullex(cfg.get('broker_url','https://trade.bull-ex.com/traderoom'),
        startup_script=(BASE/'broker_history_transport_r11.js').read_text(encoding='utf-8-sig'))
                    auto_fill_login(driver,username,password,timeout=30)
                    if not wait_for_bullex_workspace(driver,timeout=180):
                        raise RuntimeError('Bullex não concluiu a recuperação da área operacional')
                    try: capture.db.close()
                    except Exception: pass
                    capture=BullexLiveCapture(driver)
                    capture.results=results
                    executor.d=driver; executor.document_token=None
                    executor._expiry_1m_confirmed_by_code=False
                    executor._amount_10_confirmed_by_code=False
                    executor._amount_read_result=None
                    executor.set_selected_asset_provider(capture.selected_asset_snapshot)
                    feed_watchdog.reset(); feed_frozen=False; cfg['_feed_m1_frozen']=False
                    executor.set_feed_m1_ready(True)
                    last_asset_label=None; asset_verified=False
                    if overlay: overlay.driver=driver
                    for _ in range(80):
                        if BullexOverlay.inject_shell(driver,BUILD_LABEL): break
                        time.sleep(.1)
                    driver.execute_script('window.__RAPOSO_PAUSE_REQUEST__=true;')
                    print('[SEGURANCA] Chrome recuperado com novo login/captura; aguardando feed M1.', flush=True)
                except Exception as e:
                    print('[SEGURANCA] recuperacao falhou; encerrando desarmado:', repr(e), flush=True)
                    break
                continue
            if executor and getattr(executor, 'navigation_changed', False):
                executor.navigation_changed=False
                executor.disarm(); executor.runtime_ready=False
                buttons_ready=False; expiry_ready=False; user_paused=True
                if analyzer: analyzer.paused=True
                driver.execute_script('window.__RAPOSO_PAUSE_REQUEST__=true;')
            if overlay:
                try: overlay.update()
                except Exception as e: print('[OVERLAY]', type(e).__name__, flush=True)
            st=None
            live_asset=''
            live_label=None
            broker_at=None
            current_from=None
            if capture is not None:
                try:
                    capture.poll()
                    # Primeiro lê o gráfico; só depois consulta o feed desse ID.
                    # O valor do config (76) não pode ser fallback operacional.
                    selected=capture.selected_asset_snapshot(force=True)
                    live_asset=str(selected.get('asset_id') or '')
                    live_label=selected.get('label')
                    st=capture.db.execute('SELECT current_from,broker_at FROM bullex_live_state WHERE asset_id=?',(live_asset,)).fetchone() if live_asset else None
                    # Nao bloquear por falha de leitura do nome na interface. Exigimos
                    # somente um ID operacional com candle M1 recebido da propria Bullex.
                    asset_verified=bool(live_asset and st)
                    current_from=int(st[0]) if st and st[0] is not None else None
                    if live_label:
                        _remember_asset_label(live_asset,live_label)
                    now_feed=time.monotonic()
                    broker_at=float(st[1]) if st and len(st)>1 and st[1] is not None else None
                    if live_label and live_label != last_asset_label:
                        if last_asset_label is not None and executor is not None:
                            executor.on_asset_changed(live_label)
                        last_asset_label=live_label
                    if not asset_verified:
                        try: BullexOverlay.set_diag(driver,'ativo carregando; ID será conciliado pelas movimentações retornadas','ANALISE ATIVA')
                        except Exception: pass
                        if overlay: overlay.set_asset(live_asset,live_label or 'AGUARDANDO FEED M1')
                    elif analyzer is not None and live_asset != str(analyzer.asset):
                        analyzer.set_asset(live_asset)
                        cfg['asset_id']=live_asset
                        if live_label: cfg['_asset_label']=live_label
                        if overlay: overlay.set_asset(live_asset,live_label)
                    elif overlay and (live_asset != str(overlay.asset) or (live_label != getattr(overlay,'asset_label',None))):
                        if live_label: cfg['_asset_label']=live_label
                        overlay.set_asset(live_asset,live_label)
                except Exception as e:
                    BullexOverlay.set_diag(driver,'captura: '+type(e).__name__,'PAINEL ATIVO')
            feed_state=feed_watchdog.observe(live_asset,broker_at,current_from)
            feed_frozen=bool(feed_state['frozen'])
            cfg['_feed_m1_frozen']=feed_frozen
            if executor is not None:
                executor.set_feed_m1_ready(not feed_frozen)
            feed_event=feed_state.get('event')
            if feed_event and feed_event.get('kind')=='frozen':
                cadence=feed_state.get('cadence_seconds') or 0.0
                print(f"[V3.86][WATCHDOG][FEED_M1_CONGELADO] inicio={feed_event['started_at']} | detectado={feed_event['detected_at']} | ativo={feed_state['asset_id'] or 'NAO_IDENTIFICADO'} | broker_at={feed_state['broker_at']} | candle={feed_state['current_from']} | sem_avanco={feed_event['no_advance_seconds']:.2f}s | cadencia_observada={cadence:.3f}s | tolerancia={feed_state['tolerance_seconds']:.2f}s | novas_entradas=BLOQUEADAS | reconciliacao=ATIVA",flush=True)
                try: BullexOverlay.set_diag(driver,'broker_at sem avanço; somente novas entradas bloqueadas','FEED M1 CONGELADO')
                except Exception: pass
                if overlay:
                    try: overlay.update(force=True)
                    except Exception: pass
            elif feed_event and feed_event.get('kind')=='recovered':
                print(f"[V3.86][WATCHDOG][FEED_M1_RECUPERADO] inicio={feed_event['started_at']} | recuperado={feed_event['recovered_at']} | duracao={feed_event['duration_seconds']:.2f}s | avancos_consistentes={feed_event['advances']} | ativo={feed_state['asset_id']} | broker_at={feed_state['broker_at']} | novas_entradas={'BLOQUEADAS_PAUSA' if user_paused else 'LIBERADAS'}",flush=True)
                if overlay:
                    try: overlay.update(force=True)
                    except Exception: pass
            # OPERANDO is synchronized with the execution-only feed gate. Signals,
            # score, Adaptive/KNN, ROI and timing continue unchanged.
            # Recover resets outside the order/timing path, while execution is disarmed.
            if executor is not None and not user_paused and not feed_frozen:
                try:
                    from expiry_recovery_r12 import restore_step
                    restore_step(executor)
                except Exception as exc:
                    executor.disarm()
                    print('[R12][EXPIRACAO][RECUPERACAO][ERRO]',repr(exc),flush=True)

            now_feed=time.monotonic()
            # Refresh asynchronously even when there is no setup to execute.
            if executor is not None and executor.runtime_ready:
                if not executor._confirm_expiry_1m():
                    executor.disarm()
            if (executor and not user_paused and not feed_frozen and executor.runtime_ready
                    and not executor.armed and now_feed >= arm_retry_at):
                arm_retry_at=now_feed+0.5
                if executor.arm():
                    print('[V3.86][ARM] executor armado; OPERANDO sincronizado', flush=True)
                    try: BullexOverlay.set_diag(driver,'executor armado; aguardando entrada M1','DEMO ARMADO')
                    except Exception: pass
            if st and executor is not None and analyzer is not None and not analyzer_started:
                analyzer.paused=user_paused
                if not user_paused:
                    try:
                        current=int(st[0]) if st and st[0] else 0
                        analyzer.block_until_current_from=(current//60)*60+60 if current else 0
                    except Exception:
                        analyzer.block_until_current_from=0
                threading.Thread(target=analyzer.run,daemon=True).start(); analyzer_started=True
                print('[V3.86][ANALYZER] iniciado;', 'PAUSADO' if user_paused else
                      f'ANALISE ATIVA; elegível após M1 completo ({getattr(analyzer,"block_until_current_from",0)})', flush=True)

            # A liberação do executor depende dos controles da corretora, não do
            # rótulo/ID do ativo. A análise só começa quando houver M1 capturado.
            if executor is not None and analyzer is not None and not executor.runtime_ready:
                # Antes de armar, valida ACIMA/ABAIXO e grava a geometria em Raposo_Data/runtime.
                if not buttons_ready:
                    try:
                        buttons_ready=executor.validate_order_buttons_startup(timeout=3)
                    except Exception as e:
                        print('[V3.86][BOTOES] validacao pre-arm:',repr(e),flush=True)
                        buttons_ready=False
                    if not buttons_ready:
                        try: BullexOverlay.set_diag(driver,'ACIMA/ABAIXO nÃ£o validados; ordens bloqueadas','BOTÃ•ES')
                        except Exception: pass
                        time.sleep(.25)
                        continue
                if not expiry_ready:
                    try:
                        expiry_ready=bool(executor._confirm_expiry_1m())
                    except Exception as e:
                        print('[V3.86][EXPIRACAO][CHECK]',repr(e),flush=True)
                        expiry_ready=False
                    if not expiry_ready:
                        executor.disarm()
                        BullexOverlay.set_diag(driver,'aguardando confirmação visual da expiração 1 MIN','EXPIRACAO')
                        time.sleep(.25)
                        continue

                try:
                    amount_ready=bool(executor._confirm_amount_10())
                except Exception as e:
                    print('[V3.86][VALOR][CHECK]',repr(e),flush=True)
                    amount_ready=False
                if not amount_ready:
                    executor.disarm()
                    BullexOverlay.set_diag(driver,'ajuste manual: selecione $10; ordens bloqueadas','VALOR')
                    time.sleep(.25)
                    continue
                print('[V3.86][VALOR] R$10 confirmado.',flush=True)
                executor.runtime_ready=True
                executor.disarm()
                if analyzer_started: analyzer.paused=user_paused
                if not user_paused and not feed_frozen: executor.arm()
                try: BullexOverlay.set_diag(driver,'controles confirmados; aguardando sinal da captura M1' if not user_paused else 'sincronizado; aguardando RETOMAR','ANALISE ATIVA' if not user_paused else 'PAUSADO')
                except Exception: pass
                if overlay:
                    try: overlay.update(force=True)
                    except Exception as e:
                        try: BullexOverlay.set_diag(driver,'overlay inicial: '+type(e).__name__,'PAINEL ATIVO')
                        except Exception: pass
                        _fatal_log('OVERLAY NONFATAL', repr(e))
                # Update health handshake: only marks healthy after feed exists and analyzer is armed.
                try:
                    (RUNTIME_DIR/'update_healthy_r12.flag').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' | '+str(cfg.get('version','')),encoding='utf-8')
                except Exception:
                    pass

            if overlay:
                try: overlay.update()
                except Exception as e: BullexOverlay.set_diag(driver,'overlay: '+type(e).__name__,'PAINEL ATIVO')

            # Independent 10s updater. Do not depend on overlay/analyzer state to check.
            now=time.monotonic()
            if cfg.get('auto_update',{}).get('enabled') and now>=next_update_check:
                next_update_check=now+max(10,int(cfg.get('auto_update',{}).get('check_seconds',10)))
                try:
                    upd=find_update(drive_folder)
                    if upd:
                        print('[V3.86][UPDATER] pacote detectado:', str(upd), flush=True)
                        if executor: executor.disarm()
                        try:
                            if analyzer: analyzer.stop()
                        except Exception: pass
                        if sync:
                            try: sync.stop()
                            except Exception: pass
                        try: driver.quit()
                        except Exception: pass
                        print('[V3.86][UPDATER] aplicando pacote e reiniciando...', flush=True)
                        stage_update(upd)
                        return
                except Exception as e:
                    try: (LOG_DIR/'update_error.log').write_text(repr(e),encoding='utf-8')
                    except Exception: pass

            time.sleep(.08)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # V3.72: falha auxiliar nao deve derrubar o navegador/motor sem registro.
        import traceback
        err=traceback.format_exc()
        try: (LOG_DIR/'runtime_error.log').write_text(err,encoding='utf-8')
        except Exception: pass
        _fatal_log('RUNTIME FATAL', err)
        try: BullexOverlay.set_diag(driver,'falha isolada: '+type(e).__name__,'DEMO ARMADO')
        except Exception: pass
        # Mantem o browser aberto para inspecao; o guardian relanca o processo se necessario.
        time.sleep(2)
    finally:
        shutdown_started=time.monotonic()
        if analyzer: analyzer.paused=True
        if executor: executor.disarm()
        restart_helper=None
        if restart_requested:
            try:
                restart_helper=launch_after_exit(os.getpid())
                print('[R12][RESTART] Reinicio agendado antes da limpeza. helper='+str(restart_helper.pid),flush=True)
            except Exception as exc:
                print('[R12][RESTART][ERRO]',repr(exc),flush=True)
        if analyzer_started:
            _bounded_shutdown('ANALYZER',analyzer.stop,1.5)
        if sync:
            _bounded_shutdown('DRIVE_SYNC',sync.stop,3.0)
        if mobile:
            _bounded_shutdown('MOBILE',mobile.stop,2.0)
        if restart_requested and capture:
            _bounded_shutdown('CAPTURA_FINAL',capture.poll,1.5)
        print('[V3.86][STOP] Componentes parados; encerrando navegador.', flush=True)
        _bounded_shutdown('DRIVER',driver.quit,5.0)
        print(f'[V3.86][STOP] Encerramento concluido. limpeza={time.monotonic()-shutdown_started:.3f}s', flush=True)
        if restart_requested:
            try:
                if capture and capture.results:
                    _bounded_shutdown('INTENTS_FINAL',capture.results._flush_intents,1.0)
                print('[R12][RESTART] Reinicio completo; nova instancia iniciara PAUSADA.',flush=True)
            except Exception as exc:
                print('[R12][RESTART][ERRO]',repr(exc),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception:
        import traceback
        err=traceback.format_exc()
        print(err, flush=True)
        _fatal_log('STARTUP FATAL', err)
