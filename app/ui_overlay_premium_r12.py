"""Optional appearance only; inherits the production panel's controls and engine."""
from datetime import datetime
from pathlib import Path
import time
from broker_day_r11 import SAO_PAULO,today as broker_today
from ui_overlay_massa_v386_r11 import BullexOverlay

JS_PATH=Path(__file__).with_name('ui_overlay_premium_r12.js')
JS=JS_PATH.read_text(encoding='utf-8')

class PremiumOverlay(BullexOverlay):
    def _snapshot(self):
        data=super()._snapshot()
        now=datetime.now(SAO_PAULO)
        data['panel_date']=now.strftime('%d/%m/%y')
        data['panel_clock']=now.strftime('%H:%M:%S')
        executor=getattr(self.analyzer,'executor',None) if self.analyzer else getattr(self,'executor',None)
        controls=getattr(executor,'_control_read_diagnostic',{}) or {}
        data['actual_expiry']=controls.get('expiry') or 'NÃO CONFIRMADA'
        data['capture_state']='CONGELADA' if data['feed_m1_frozen'] else ('RECEBENDO' if self.analyzer and data['price'] is not None and data['remaining'] is not None else 'AGUARDANDO')
        data['engine_state']='PAUSADO' if data['paused'] else ('ANALISANDO' if self.analyzer else 'AGUARDANDO')
        data['executor_state']='PRONTO' if data['armed'] else 'BLOQUEADO'
        data['sync_state']='CONFIGURADO' if (self.cfg.get('drive_sync') or {}).get('enabled') else 'DESATIVADO'
        if time.monotonic()-getattr(self,'_premium_financial_at',0)>3:
            draws=self.db.execute("SELECT COUNT(*) FROM broker_confirmed_results WHERE result='DRAW' AND substr(closed_at,1,10)=?",(broker_today(),)).fetchone()[0]
            last=self.db.execute("""SELECT o.asset_name,o.direction_executed,o.direction,r.closed_at,r.result,r.pnl_minor,r.currency,r.stake_minor
                FROM broker_confirmed_results r JOIN demo_orders o ON o.id=r.demo_order_id
                WHERE o.status='EXECUTED' AND substr(r.closed_at,1,10)=?
                ORDER BY r.closed_at DESC,o.id DESC LIMIT 1""",(broker_today(),)).fetchone()
            self._premium_financial={'draws':int(draws),'last_order':None}
            if last:
                asset,executed,direction,closed,result,pnl,currency,stake=last
                self._premium_financial['last_order']={'asset':asset or 'ATIVO NÃO VINCULADO',
                    'side':'CALL' if (executed or direction)=='G' else 'PUT' if (executed or direction)=='R' else '—',
                    'at':datetime.fromisoformat(closed.replace('Z','+00:00')).astimezone(SAO_PAULO).strftime('%d/%m/%y · %H:%M:%S'),'result':result,
                    'pnl':f'{currency} {pnl/100:+.2f}' if pnl is not None else '—',
                    'stake':f'{currency} {stake/100:.2f}' if stake is not None else '—'}
            self._premium_financial_at=time.monotonic()
        data.update(self._premium_financial)
        self._premium_payload=data
        return data

    def update(self,force=False):
        if not force and time.time()-self.last_push<0.45:return
        stamp=JS_PATH.stat().st_mtime_ns
        if stamp!=getattr(self,'_premium_script_stamp',None):
            script=JS_PATH.read_text(encoding='utf-8')
            if hasattr(self,'_premium_script_stamp'):
                self.driver.execute_script("document.getElementById('bullex-pro-overlay')?.remove();document.getElementById('raposo-premium-style')?.remove();document.getElementById('raposo-v386-style')?.remove();")
            self._premium_script=script
            self._premium_script_stamp=stamp
        self._premium_payload=None
        super().update(force=force)
        if getattr(self,'_premium_disabled',False) or self._premium_payload is None:return
        try:
            self.driver.execute_script(self._premium_script,self._premium_payload)
        except Exception as exc:
            self._premium_disabled=True
            print('[R12][TELA NOVA] Falha visual; retornando ao painel atual:',repr(exc),flush=True)
            try:
                self.driver.execute_script("document.getElementById('bullex-pro-overlay')?.remove();document.getElementById('raposo-premium-style')?.remove();document.getElementById('raposo-v386-style')?.remove();")
            except Exception:pass
