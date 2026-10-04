import candle_flow_observation_r12 as cf_observation
from session_exception_r12 import t4_exception_active, ALLOWED_SETUPS
from browser_capture import screenshot_png, screenshot_css_image
import threading, time, re, json, sqlite3
from datetime import datetime
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from database import connect
from asset_catalog import ensure_schema, resolve, lookup, is_asset_name
from voice_announcer_r11 import announce as voice_announce
from paths_r11 import RUNTIME_DIR

class DemoExecutor:
    def __init__(self, driver, config):
        self.d = driver
        self.cfg = config
        self.db = connect()
        ensure_schema(self.db)
        self.base = Path(__file__).resolve().parent
        self.lock = threading.Lock()
        self.armed = False
        self.feed_m1_ready = True
        self.session_orders = 0
        self._expiry_1m_confirmed_by_code = False
        self._amount_10_confirmed_by_code = False
        self._amount_read_thread = None
        self._amount_read_result = None
        self.exec_cfg_path = RUNTIME_DIR/'config_execucao.json'
        self.runtime_ready = False
        self._last_amount_set_attempt = 0.0
        self._last_expiry_set_attempt = 0.0
        self.document_token = None
        self._exec_cfg = self._load_exec_cfg()
        self.selected_asset_provider = None
        self._current_asset_name = None
        self._buttons_ready = False
        self._button_rects = {}
        self.loss_controller = None

    def set_selected_asset_provider(self, provider):
        self.selected_asset_provider = provider

    def set_loss_controller(self, controller):
        self.loss_controller = controller

    def _loss_gate(self):
        controller = getattr(self, 'loss_controller', None)
        if controller is None:
            return True, {'current': '—', 'reason': ''}
        return controller.allows_new_entries()

    def _selected_asset_is_safe(self, expected_asset):
        expected=str(expected_asset or '')
        selected=None
        try:
            if self.selected_asset_provider is not None:
                selected=self.selected_asset_provider(force=True) or {}
        except Exception:
            selected=None
        label=' '.join(str((selected or {}).get('label') or '').split()).upper()
        actual=str((selected or {}).get('asset_id') or '')
        verified=bool((selected or {}).get('verified'))
        if verified and actual and actual == expected:
            if label.startswith('ATIVO WS ') or label in ('', 'CARREGANDO', 'AGUARDANDO ATIVO',
                                                            'NAO IDENTIFICADO', 'NÃO IDENTIFICADO'):
                label='ATIVO NÃO VINCULADO'
            return True, label or f'ATIVO BULLEX {actual}'
        if self._has_fresh_m1_capture(expected):
            return True, 'ATIVO NÃO VINCULADO'
        return False, 'aguardando captura M1 recente para vincular o ativo'

    def _has_fresh_m1_capture(self, asset_id, max_age=15.0):
        """Use the signal's captured M1 stream while the chart label is loading."""
        if not asset_id:
            return False
        try:
            row=self.db.execute('SELECT last_received_at FROM bullex_live_state WHERE asset_id=?',
                                (str(asset_id),)).fetchone()
            if not row or not row[0]:
                return False
            observed=datetime.fromisoformat(str(row[0]))
            now=datetime.now(observed.tzinfo) if observed.tzinfo else datetime.now()
            age=(now-observed).total_seconds()
            return -5.0 <= age <= float(max_age)
        except Exception:
            return False

    def session_valid(self):
        try:
            token = self.d.execute_script("return location.href + '|' + performance.timeOrigin;")
            if not token or (self.document_token is not None and token != self.document_token):
                self.runtime_ready = False
                self._amount_10_confirmed_by_code = False
                self._expiry_1m_confirmed_by_code = False
                self._amount_read_result = None
                self.disarm()
                self.document_token = token
                self.navigation_changed = True
                return True
            self.document_token = token
            return True
        except Exception:
            self.runtime_ready = False
            self.disarm()
            return False

    def safety_ready(self):
        try:
            # Always refresh both execution controls. A different failed gate must
            # not prevent ACIMA/ABAIXO from leaving their transient blocked state.
            session=bool(self.session_valid())
            buttons=bool(self.refresh_order_buttons())
            demo_only=self.cfg.get('demo_only') is True
            orders_enabled=self.cfg.get('execute_orders') is True
            feed_m1=bool(getattr(self, 'feed_m1_ready', True))
            loss_allowed,_=self._loss_gate()
            expiry_ready=bool(self._confirm_expiry_1m())
            return bool(session and self.runtime_ready and feed_m1 and demo_only and orders_enabled
                        and float(self.cfg.get('demo_stake', 0)) == 10.0
                        and expiry_ready and buttons and loss_allowed)
        except Exception:
            self.disarm()
            return False

    def safety_status(self):
        """Diagnostico somente-leitura; nao relaxa nenhum requisito de seguranca."""
        try:
            session=bool(self.session_valid())
            runtime=bool(self.runtime_ready)
            stake=(float(self.cfg.get('demo_stake', 0)) == 10.0)
            controls=self._read_rendered_controls()
            expiry=controls.get('expiry')
            expiry_confirmed=bool(self._expiry_1m_confirmed_by_code and expiry == '1 MIN')
            amount=self._amount_10_confirmed_by_code
            acima=bool(self._probe_button_no_click('ACIMA'))
            abaixo=bool(self._probe_button_no_click('ABAIXO'))
            fresh=0 <= time.monotonic()-controls.get('captured_at',float('-inf')) <= 4
            demo_only=self.cfg.get('demo_only') is True
            orders_enabled=self.cfg.get('execute_orders') is True
            feed_m1=bool(getattr(self, 'feed_m1_ready', True))
            loss_allowed,loss_state=self._loss_gate()
            ok=session and runtime and feed_m1 and demo_only and orders_enabled and stake and expiry_confirmed and amount and acima and abaixo and fresh and loss_allowed
            return ok, f"demo_only={demo_only} execute_orders={orders_enabled} session={session} runtime={runtime} feed_m1={feed_m1} stake10={stake} expiry={expiry or 'NAO_LIDA'} expiry1m_confirmada={expiry_confirmed} amount10={amount} acima={acima} abaixo={abaixo} stop_loss={loss_allowed}:{loss_state.get('current')}:{loss_state.get('reason','')} leitura={getattr(self, '_control_read_diagnostic', {})}"
        except Exception as e:
            return False, f"diagnostico={type(e).__name__}:{e}"

    def arm(self):
        self.armed = bool(self.safety_ready())
        if not self.armed:
            _, detail=self.safety_status()
            print('[SEGURANCA][BLOQUEADO]', detail, flush=True)
        return self.armed

    def disarm(self):
        self.armed = False

    def set_feed_m1_ready(self, ready):
        """Execution-only feed gate; submitted orders/reconciliation are untouched."""
        self.feed_m1_ready = bool(ready)
        if not self.feed_m1_ready:
            self.disarm()


    def on_asset_changed(self, asset_label=None):
        """Asset change does not invalidate the user-confirmed manual expiry.

        R6 validates 1 MIN once before enabling execution. A navigation/reload still
        invalidates runtime_ready in session_valid(), but a noisy asset label must not
        create false expiry blocks.
        """
        if asset_label:
            print(f"[DEMO][ATIVO] troca detectada: {asset_label} | expiracao manual preservada", flush=True)

    def _read_rendered_controls(self):
        # No configured values, overlay text, old confirmations or global AX matches.
        try:
            from bullex_controls_reader import read_controls
            if not hasattr(self, '_controls_pixel_cache'):
                self._controls_pixel_cache = {}
            result = read_controls(self.d, self._controls_pixel_cache)
            self._control_read_diagnostic = result
            if result.get('expiry') != '1 MIN':
                self._expiry_1m_confirmed_by_code = False
            if result.get('amount') != 10.0:
                self._amount_10_confirmed_by_code = False
            return result
        except Exception as exc:
            self._control_read_diagnostic = {'source': 'SCREEN_OCR',
                'error': type(exc).__name__ + ': ' + str(exc)}
            return {'amount': None, 'expiry': None}

    def read_expiry_label(self):
        return self._read_rendered_controls().get('expiry')

    def get_expiry_status(self):
        return self.read_expiry_label()

    def _find_canvas(self):
        best = None
        best_area = -1
        for c in self.d.find_elements(By.TAG_NAME, "canvas"):
            try:
                r = c.rect
                area = max(0, r.get("width",0)) * max(0, r.get("height",0))
                if c.is_displayed() and area > best_area:
                    best = c
                    best_area = area
            except Exception:
                pass
        return best

    def _scaled(self, canvas, key):
        p = self.cfg["canvas_profile"]
        r = canvas.rect
        ref_w = float(p["reference_width"])
        ref_h = float(p["reference_height"])
        x0, y0 = p[key]
        return (
            float(x0) / ref_w * r["width"],
            float(y0) / ref_h * r["height"]
        )

    def _click_canvas(self, canvas, x, y):
        if not self.session_valid():
            raise RuntimeError('sessao invalida; clique bloqueado')
        r = canvas.rect
        ox = x - r["width"] / 2
        oy = y - r["height"] / 2
        ActionChains(self.d).move_to_element_with_offset(canvas, ox, oy).click().perform()

    def _set_amount(self, canvas, stake):
        x, y = self._scaled(canvas, "invest_field_center")
        self._click_canvas(canvas, x, y)
        time.sleep(.18)
        ActionChains(self.d).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
        time.sleep(.08)
        ActionChains(self.d).send_keys(str(int(stake) if float(stake).is_integer() else stake)).perform()
        time.sleep(.08)
        ActionChains(self.d).send_keys(Keys.ENTER).perform()
        time.sleep(.35)


    def _visible_text(self):
        try:
            return self.d.execute_script("return document.body ? document.body.innerText : ''") or ''
        except Exception:
            return ''

    def _find_visible_text_element(self, texts):
        texts=[str(x).strip().lower() for x in texts]
        try:
            els=self.d.find_elements(By.XPATH, "//*[self::button or self::div or self::span or self::input]")
        except Exception:
            return None
        best=None; best_area=10**18
        for e in els:
            try:
                if not e.is_displayed():
                    continue
                t=(e.text or e.get_attribute('value') or '').strip().lower()
                if t in texts:
                    r=e.rect; area=max(1,r.get('width',1))*max(1,r.get('height',1))
                    if area<best_area:
                        best=e; best_area=area
            except Exception:
                pass
        return best

    def _ensure_expiry_1m(self):
        return self._confirm_expiry_1m()

    def _expiry_ui_guard(self, enable):
        """Expiry adjustment must never toggle/resume the Raposo overlay."""
        try:
            if enable:
                return self.d.execute_script("""const r=document.getElementById('bullex-pro-overlay'); window.__RAPOSO_EXPIRY_PREV_PAUSE__=(window.__RAPOSO_PAUSE_REQUEST__===true); if(r){window.__RAPOSO_EXPIRY_PREV_POINTER__=r.style.pointerEvents;r.style.pointerEvents='none';} return window.__RAPOSO_EXPIRY_PREV_PAUSE__;""")
            self.d.execute_script("""const r=document.getElementById('bullex-pro-overlay'); if(r)r.style.pointerEvents=window.__RAPOSO_EXPIRY_PREV_POINTER__||'auto'; if(window.__RAPOSO_EXPIRY_PREV_PAUSE__===true)window.__RAPOSO_PAUSE_REQUEST__=true; delete window.__RAPOSO_EXPIRY_PREV_POINTER__; delete window.__RAPOSO_EXPIRY_PREV_PAUSE__;""")
        except Exception:
            return None

    def ensure_expiry_1m_startup(self, timeout=10):
        deadline=time.monotonic()+max(.5,float(timeout))
        while time.monotonic()<deadline:
            if self._confirm_expiry_1m():
                return True
            time.sleep(.1)
        return False
    def read_amount_value(self):
        return self._read_rendered_controls().get('amount')

    def _confirm_amount_10(self):
        # LOGON define $10; uma leitura positiva valida o valor para esta página.
        # OCR roda fora do laço principal: mesmo se o Windows OCR demorar, os
        # comandos PAUSE/RESUME continuam sendo confirmados em tempo curto.
        worker = self._amount_read_thread
        if worker is not None and not worker.is_alive():
            self._amount_read_thread = None
            completed = self._amount_read_result
            self._amount_read_result = None
            if completed:
                token, value, diagnostic = completed
                self._control_read_diagnostic = diagnostic
                fresh = 0 <= time.monotonic()-float((diagnostic or {}).get('captured_at',float('-inf'))) <= 4
                self._expiry_1m_confirmed_by_code = bool(token == self.document_token and fresh and
                    str((diagnostic or {}).get('expiry') or '').upper() == '1 MIN')
                self._amount_10_confirmed_by_code = bool(token == self.document_token and fresh and
                    value is not None and abs(float(value)-10.0) < 0.001)
        if (self._amount_read_thread is None and time.monotonic()-self._last_amount_set_attempt >= 2.0
                and time.monotonic()-float((getattr(self,'_control_read_diagnostic',{}) or {}).get('captured_at',0)) >= 1.5):
            self._last_amount_set_attempt = time.monotonic()
            token = self.document_token
            def read_in_background():
                value = self.read_amount_value()
                diagnostic = dict(getattr(self, '_control_read_diagnostic', {}) or {})
                self._amount_read_result = (token, value, diagnostic)
            self._amount_read_thread = threading.Thread(target=read_in_background,
                                                        name='raposo-control-reader', daemon=True)
            self._amount_read_thread.start()
        diagnostic = getattr(self, '_control_read_diagnostic', {}) or {}
        fresh = 0 <= time.monotonic()-float(diagnostic.get('captured_at',float('-inf'))) <= 4
        return bool(fresh and self._amount_10_confirmed_by_code)

    def _confirm_expiry_1m(self):
        # A expiração é confirmada na mesma captura visual assíncrona do cartão.
        self._confirm_amount_10()
        diagnostic = getattr(self, '_control_read_diagnostic', {}) or {}
        fresh = 0 <= time.monotonic()-float(diagnostic.get('captured_at',float('-inf'))) <= 4
        return bool(fresh and self._expiry_1m_confirmed_by_code and diagnostic.get('expiry') == '1 MIN')

    def ensure_amount_10_startup(self, timeout=6):
        """Define $10 no card Invest da Bullex; nunca libera sem reler $10."""
        if self._confirm_amount_10(): return True
        now=time.monotonic()
        if now-self._last_amount_set_attempt < 2.0: return False
        self._last_amount_set_attempt=now
        try:
            # Primeiro tenta input/contenteditable real associado ao card Invest.
            field=self.d.execute_script(r"""
              const vis=e=>{const r=e.getBoundingClientRect(),c=getComputedStyle(e);return r.width>5&&r.height>5&&r.bottom>0&&r.right>0&&c.display!=='none'&&c.visibility!=='hidden'};
              const norm=s=>(s||'').replace(/\s+/g,' ').trim(); const vw=innerWidth;
              let all=[...document.querySelectorAll('body *')].filter(e=>vis(e)&&!e.closest('#bullex-pro-overlay')&&e.getBoundingClientRect().left>vw*.72);
              let lab=all.find(e=>/^invest$/i.test(norm(e.innerText||e.textContent))) || all.find(e=>/^invest\b/i.test(norm(e.innerText||e.textContent)));
              let box=lab; for(let i=0;i<5&&box;i++,box=box.parentElement){let t=norm(box.innerText||box.textContent); if(/invest/i.test(t)&&/\$\s*\d+/.test(t)) break;}
              if(box){let f=box.querySelector('input,[contenteditable="true"]'); if(f&&vis(f))return f; let vals=[...box.querySelectorAll('*')].filter(e=>vis(e)&&/^\$?\s*\d+(?:[.,]\d+)?$/.test(norm(e.innerText||e.textContent))); if(vals.length)return vals[0];}
              return all.find(e=>/^\$\s*1(?:[.,]0+)?$/.test(norm(e.innerText||e.textContent)))||null;
            """)
            if field:
                ActionChains(self.d).move_to_element(field).click().perform(); time.sleep(.12)
                ActionChains(self.d).key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL).send_keys('10').send_keys(Keys.ENTER).perform()
                time.sleep(.55)
                if self._confirm_amount_10():
                    print('[DEMO][VALOR] $10 selecionado e confirmado no controle Bullex.',flush=True); return True
            print('[DEMO][VALOR][BLOQUEADO] controle Bullex nao confirmou $10.',flush=True)
        except Exception as e: print('[DEMO][VALOR][SET]',repr(e),flush=True)
        self.disarm(); return False


    def _load_exec_cfg(self):
        try:
            if self.exec_cfg_path.exists():
                obj=json.loads(self.exec_cfg_path.read_text(encoding='utf-8'))
                return obj if isinstance(obj,dict) else {}
        except Exception as e:
            print(f"[V3.86][BOTOES][CFG] leitura falhou: {e!r}", flush=True)
        return {}

    def _save_exec_cfg(self):
        try:
            self.exec_cfg_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.exec_cfg_path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(self._exec_cfg,ensure_ascii=False,indent=2),encoding='utf-8')
            tmp.replace(self.exec_cfg_path)
            return True
        except Exception as e:
            print(f"[V3.86][BOTOES][CFG] gravacao falhou: {e!r}", flush=True)
            return False

    def _persist_button_rect(self, side, rect, source='AUTO'):
        try:
            vw=float(self.d.execute_script("return window.innerWidth") or 0)
            vh=float(self.d.execute_script("return window.innerHeight") or 0)
            if vw<=0 or vh<=0: return
            x=float(rect.get('x',0)); y=float(rect.get('y',0)); w=float(rect.get('width',0)); h=float(rect.get('height',0))
            if w<=0 or h<=0: return
            buttons=self._exec_cfg.setdefault('order_buttons',{})
            buttons[side]={'x':x/vw,'y':y/vh,'w':w/vw,'h':h/vh,'source':source,'updated_at':datetime.now().isoformat(timespec='seconds')}
            self._exec_cfg['schema']=1
            self._save_exec_cfg()
            print(f"[V3.86][BOTOES][PERSISTIDO] {side}=OK fonte={source}", flush=True)
        except Exception as e:
            print(f"[V3.86][BOTOES][CFG] persistencia {side}: {e!r}", flush=True)

    def _cached_button_rect(self, side):
        try:
            rec=(self._exec_cfg.get('order_buttons') or {}).get(side) or {}
            vw=float(self.d.execute_script("return window.innerWidth") or 0); vh=float(self.d.execute_script("return window.innerHeight") or 0)
            if vw<=0 or vh<=0: return None
            x=float(rec['x'])*vw; y=float(rec['y'])*vh; w=float(rec['w'])*vw; h=float(rec['h'])*vh
            if w<40 or h<20 or x<vw*.45 or x+w>vw*1.02 or y<0 or y+h>vh*1.02: return None
            return {'x':x,'y':y,'width':w,'height':h,'source':'CACHE'}
        except Exception:
            return None

    def _color_matches_side(self, side, rect):
        try:
            import io
            from PIL import Image
            im=screenshot_css_image(self.d)
            W,H=im.size; pix=im.load()
            cx=int(max(0,min(W-1,rect['x']+rect['width']/2))); cy=int(max(0,min(H-1,rect['y']+rect['height']/2)))
            radx=max(4,int(rect['width']*.25)); rady=max(4,int(rect['height']*.25)); hit=tot=0
            for yy in range(max(0,cy-rady),min(H,cy+rady+1),4):
                for xx in range(max(0,cx-radx),min(W,cx+radx+1),4):
                    R,G,B=pix[xx,yy]; tot+=1
                    ok=(G>95 and G>R*1.18 and G>B*1.10) if side=='ACIMA' else (R>110 and R>G*1.18 and R>B*1.10)
                    if ok: hit+=1
            # A real order button has a solid colored centre; sparse profit text is not a button.
            return tot>0 and hit/tot>=0.55
        except Exception:
            return False

    def _find_pixel_button_bbox(self, direction):
        import io
        from PIL import Image
        im=screenshot_css_image(self.d)
        w,h=im.size; pix=im.load(); step=3
        x0,x9=int(w*.82),int(w*.997); y0,y9=int(h*.12),int(h*.82)
        hits=set()
        for y in range(y0,y9,step):
            for x in range(x0,x9,step):
                R,G,B=pix[x,y]
                hit=(G>105 and G>R*1.28 and G>B*1.18) if direction=='G' else (R>125 and R>G*1.28 and R>B*1.18)
                if hit: hits.add((x,y))
        comps=[]
        while hits:
            seed=hits.pop(); stack=[seed]; comp=[seed]
            while stack:
                x,y=stack.pop()
                for dx,dy in ((step,0),(-step,0),(0,step),(0,-step)):
                    q=(x+dx,y+dy)
                    if q in hits: hits.remove(q); stack.append(q); comp.append(q)
            comps.append(comp)
        cand=[]
        for c in comps:
            xs=[q[0] for q in c]; ys=[q[1] for q in c]
            x1,x2=min(xs),max(xs); y1,y2=min(ys),max(ys); bw,bh=x2-x1,y2-y1
            if bw>=65 and bh>=20: cand.append((bw*bh,len(c),x1,y1,x2,y2,c))
        if not cand: return None
        cand.sort(key=lambda item:item[:2],reverse=True); _,_,x1,y1,x2,y2,points=cand[0]
        # Centre of the solid button rectangle, excluding labels/arrows.
        import statistics
        return {'x':x1,'y':y1,'width':x2-x1,'height':y2-y1,'source':'PIXEL',
                'center_x':statistics.median(pt[0] for pt in points),
                'center_y':statistics.median(pt[1] for pt in points)}

    def _probe_button_no_click(self, side):
        direction='G' if side=='ACIMA' else 'R'
        labels=("ACIMA","UP","CALL") if side=='ACIMA' else ("ABAIXO","DOWN","PUT")
        cached=self._cached_button_rect(side)
        if cached and self._color_matches_side(side,cached): return cached
        try:
            r=self.d.execute_script(r"""
                const wanted=arguments[0].map(x=>x.toLowerCase()); const norm=s=>(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/\s+/g,' ').trim().toLowerCase();
                const vw=innerWidth||document.documentElement.clientWidth,vh=innerHeight||document.documentElement.clientHeight;
                const vis=e=>{if(!e||!e.getBoundingClientRect)return false;const r=e.getBoundingClientRect(),c=getComputedStyle(e);return r.width>35&&r.height>24&&r.bottom>0&&r.right>0&&r.top<vh&&r.left<vw&&c.display!=='none'&&c.visibility!=='hidden'&&Number(c.opacity||1)>0};
                let cand=[]; for(const e of document.querySelectorAll('button,[role=button],a,div,span')){if(!vis(e))continue;const b=norm([e.innerText,e.textContent,e.getAttribute('aria-label'),e.getAttribute('title'),e.className].filter(Boolean).join(' '));if(!wanted.some(w=>b===w||b.includes(w)))continue;const rr=e.getBoundingClientRect();if(rr.left<vw*.50)continue;cand.push({x:rr.left,y:rr.top,width:rr.width,height:rr.height,area:rr.width*rr.height});}
                if(!cand.length)return null;cand.sort((a,b)=>b.area-a.area);return cand[0];
            """, list(labels))
            if r:
                r['source']='DOM'
                if self._color_matches_side(side,r):
                    return r
        except Exception: pass
        try:
            r=self._find_pixel_button_bbox(direction)
            if r and self._color_matches_side(side,r):
                return r
            return None
        except Exception: return None

    def refresh_order_buttons(self, persist=True):
        """Refresh the hard button gate and publish loss/recovery transitions."""
        previous=bool(getattr(self, '_buttons_ready', False))
        rects={side:self._probe_button_no_click(side) for side in ('ACIMA','ABAIXO')}
        ready=all(rects.values())
        self._button_rects=rects
        self._buttons_ready=ready
        if ready:
            if persist and (not previous or any(r.get('source') != 'CACHE' for r in rects.values())):
                for side, rect in rects.items():
                    self._persist_button_rect(side, rect, rect.get('source','AUTO'))
            if not previous:
                print('[V3.86][BOTOES][RECUPERADO] ACIMA=OK | ABAIXO=OK | liberando rearmamento', flush=True)
        elif previous:
            self.disarm()
            print(f"[V3.86][BOTOES][PERDIDO] ACIMA={'OK' if rects['ACIMA'] else 'FALHOU'} | ABAIXO={'OK' if rects['ABAIXO'] else 'FALHOU'} | ORDENS BLOQUEADAS", flush=True)
        return ready

    def validate_order_buttons_startup(self, timeout=4):
        end=time.monotonic()+max(.5,float(timeout)); last={}
        while time.monotonic()<end:
            ok=self.refresh_order_buttons(persist=True)
            last=dict(getattr(self, '_button_rects', {}))
            if ok:
                print('[V3.86][BOTOES] ACIMA=OK | ABAIXO=OK | config persistente=Raposo_Data/runtime/config_execucao.json', flush=True)
                return True
            time.sleep(.35)
        print(f"[V3.86][BOTOES][BLOQUEADO] ACIMA={'OK' if last.get('ACIMA') else 'FALHOU'} | ABAIXO={'OK' if last.get('ABAIXO') else 'FALHOU'}", flush=True)
        return False

    def _assert_entry_deadline(self):
        deadline=getattr(self, '_entry_click_deadline', None)
        if deadline is None or time.monotonic() >= deadline:
            raise RuntimeError('JANELA M1 EXPIRADA: clique descartado apos 1 segundo')
        row=self.db.execute('SELECT broker_at,last_received_at FROM bullex_live_state WHERE asset_id=?',(self._entry_asset_id,)).fetchone()
        if not row or row[0] is None or not row[1]:
            raise RuntimeError('JANELA M1: relogio broker nao confirmado')
        received=datetime.fromisoformat(str(row[1])).timestamp()
        age=time.time()-received
        broker_now=float(row[0])+age
        elapsed=broker_now-self._entry_target
        if age<0 or age>5.0 or not 0 <= elapsed < 1.0:
            raise RuntimeError(f'JANELA M1: relogio broker fora de 0-1s; atraso={elapsed:.3f}s; idade={age:.3f}s')
        allowed,state=self._loss_gate()
        if not allowed: raise RuntimeError(state.get('reason','RISCO BLOQUEADO'))
        if not self.armed: raise RuntimeError('EXECUTOR PAUSADO')
        if time.monotonic() >= deadline:
            raise RuntimeError('JANELA M1 EXPIRADA durante verificacao de risco')

    def _click_direction(self, canvas, direction):
        # No second asynchronous OCR/arming round at the opening boundary.
        # A NEW screenshot below still proves exact 1 MIN and $10 before sending.
        if not self.armed or not self.runtime_ready or not getattr(self,'feed_m1_ready',True):
            self.disarm()
            print('[DEMO][BLOQUEADO] executor sem armamento/runtime/feed no instante do envio',flush=True)
            return False
        # A fresh screenshot must match the broker card confirmed by OCR.
        # Changed/unreadable controls cannot inherit an earlier 1 MIN approval.
        try:
            from bullex_controls_reader import verify_controls_snapshot
            controls=verify_controls_snapshot(self.d, getattr(self, '_controls_pixel_cache', {}))
        except Exception:
            controls=None
        if not controls or controls.get('expiry') != '1 MIN' or controls.get('amount') != 10.0:
            self._expiry_1m_confirmed_by_code=False
            self._amount_10_confirmed_by_code=False
            self.disarm()
            print('[DEMO][BLOQUEADO] cartão de expiração/valor mudou; 1 MIN exige nova confirmação visual antes do clique',flush=True)
            return False
        self._control_read_diagnostic=controls
        """TESTE20: localizador ampliado dos botoes reais ACIMA/ABAIXO.

        Mantem clique somente em elemento visivel/clicavel do painel direito;
        amplia a faixa de busca e aceita texto contido, shadow DOM e candidatos
        com classes/aria-label. Nao registra EXECUTED sem clique confirmado.
        """
        if direction == "G":
            labels = ("ACIMA", "UP", "CALL")
            side = "ACIMA"
        elif direction == "R":
            labels = ("ABAIXO", "DOWN", "PUT")
            side = "ABAIXO"
        else:
            return False

        print(f"[TESTE20][BOTAO] procurando {side}...", flush=True)

        cached=(getattr(self, '_button_rects', {}) or {}).get(side)
        if cached and self._color_matches_side(side,cached):
            x=cached.get('center_x',cached['x']+cached['width']/2); y=cached.get('center_y',cached['y']+cached['height']/2)
            try:
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mouseMoved','x':x,'y':y})
                self._assert_entry_deadline()
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mousePressed','x':x,'y':y,'button':'left','buttons':1,'clickCount':1})
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mouseReleased','x':x,'y':y,'button':'left','buttons':0,'clickCount':1})
                print(f"[TESTE20][CACHE] {side} x={x:.0f} y={y:.0f} | clique CDP enviado", flush=True)
                return True
            except Exception as e:
                print(f"[TESTE20][CACHE][ERRO] {side}: {e!r}", flush=True)

        # 1) Busca DOM profunda. A versao antiga exigia >72% da tela; a Bullex
        # pode deslocar o painel conforme zoom/largura. Aqui aceitamos >52%.
        try:
            el = self.d.execute_script(r"""
                const wanted = arguments[0].map(x => x.toLowerCase());
                const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
                    .replace(/\s+/g,' ').trim().toLowerCase();
                const vw=innerWidth||document.documentElement.clientWidth;
                const vh=innerHeight||document.documentElement.clientHeight;
                const vis=e=>{
                    if(!e || !e.getBoundingClientRect) return false;
                    const r=e.getBoundingClientRect(), cs=getComputedStyle(e);
                    return r.width>35 && r.height>28 && r.bottom>0 && r.right>0 &&
                           r.top<vh && r.left<vw && cs.display!=='none' &&
                           cs.visibility!=='hidden' && Number(cs.opacity||1)>0;
                };
                const roots=[document];
                const seen=new Set();
                for(let i=0;i<roots.length;i++){
                    const root=roots[i];
                    let nodes=[];
                    try{nodes=[...root.querySelectorAll('*')]}catch(e){}
                    for(const n of nodes){
                        if(n.shadowRoot && !seen.has(n.shadowRoot)){seen.add(n.shadowRoot); roots.push(n.shadowRoot);}
                    }
                }
                let cand=[];
                for(const root of roots){
                    let all=[];
                    try{all=[...root.querySelectorAll('button,[role=button],a,div,span')]}catch(e){}
                    for(const e of all){
                        if(!vis(e)) continue;
                        const blob=norm([e.innerText,e.textContent,e.getAttribute('aria-label'),e.getAttribute('title'),e.className].filter(Boolean).join(' '));
                        if(!wanted.some(w=>blob===w || blob.includes(w))) continue;
                        let c=e.closest ? (e.closest('button,[role=button],a') || e) : e;
                        if(!vis(c)) c=e;
                        const r=c.getBoundingClientRect();
                        if(r.left < vw*0.52) continue;
                        const area=r.width*r.height;
                        const score=area + (r.left/vw)*5000 + (blob.length<40?2000:0);
                        cand.push({e:c,score,txt:blob,r:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]});
                    }
                }
                if(!cand.length) return null;
                cand.sort((a,b)=>b.score-a.score);
                console.log('RAPOSO_BUTTON_CANDIDATES',cand.slice(0,8).map(x=>({txt:x.txt,r:x.r,score:x.score})));
                return cand[0].e;
            """, list(labels))
            if el is not None and el.is_displayed():
                r=el.rect
                print(f"[TESTE20][BOTAO] {side} DOM x={r.get('x')} y={r.get('y')} w={r.get('width')} h={r.get('height')}", flush=True)
                try:
                    self.d.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", el)
                except Exception:
                    pass
                try:
                    self._assert_entry_deadline()
                    el.click()
                except Exception:
                    self._assert_entry_deadline()
                    self.d.execute_script("arguments[0].click();", el)
                self._persist_button_rect(side,r,"DOM")
                print(f"[DEMO][CLIQUE] botao DOM {side}", flush=True)
                return True
        except Exception as e:
            print(f"[TESTE20][BOTAO][DOM] {side}: {e!r}", flush=True)

        # 2) Selenium por texto visivel, sem limitar aos 72% antigos.
        try:
            el = self._find_visible_text_element(labels)
            if el is not None:
                r=el.rect
                vw=float(self.d.execute_script("return window.innerWidth") or 0)
                if (not vw) or float(r.get('x',0)) > vw*0.52:
                    try:
                        self._assert_entry_deadline()
                        el.click()
                    except Exception:
                        self._assert_entry_deadline()
                        self.d.execute_script("arguments[0].click();", el)
                    self._persist_button_rect(side,r,"TXT")
                    print(f"[DEMO][CLIQUE] texto visivel {side}", flush=True)
                    return True
        except Exception as e:
            print(f"[TESTE20][BOTAO][TXT] {side}: {e!r}", flush=True)

        # 3) Diagnostico: imprime os maiores controles visiveis da metade direita.
        try:
            rows=self.d.execute_script(r"""
                const vw=innerWidth||document.documentElement.clientWidth, vh=innerHeight||document.documentElement.clientHeight;
                const out=[];
                for(const e of document.querySelectorAll('button,[role=button],a,div')){
                  const r=e.getBoundingClientRect(),cs=getComputedStyle(e);
                  if(r.left<vw*.52||r.width<70||r.height<35||r.top<0||r.bottom>vh||cs.display==='none'||cs.visibility==='hidden') continue;
                  const t=(e.innerText||e.textContent||e.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().slice(0,80);
                  if(!t) continue;
                  out.push({t,x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),bg:cs.backgroundColor});
                }
                out.sort((a,b)=>(b.w*b.h)-(a.w*a.h)); return out.slice(0,12);
            """) or []
            for row in rows:
                print(f"[TESTE20][CANDIDATO] {row}", flush=True)
        except Exception as e:
            print(f"[TESTE20][CANDIDATOS][ERRO] {e!r}", flush=True)

        # 4) TESTE20 V2: detector visual restrito ao painel de ordens + clique CDP real.
        try:
            import io
            from PIL import Image
            im=Image.open(io.BytesIO(screenshot_png(self.d))).convert('RGB')
            w,h=im.size; pix=im.load(); step=3
            # No layout atual os botoes ficam no extremo direito. Evita juntar
            # candles/indicadores verdes do grafico ao componente do botao.
            x0,x9=int(w*.885),int(w*.995); y0,y9=int(h*.20),int(h*.72)
            hits=set()
            for y in range(y0,y9,step):
                for x in range(x0,x9,step):
                    R,G,B=pix[x,y]
                    hit=(G>105 and G>R*1.28 and G>B*1.18) if direction=='G' else (R>125 and R>G*1.28 and R>B*1.18)
                    if hit: hits.add((x,y))
            # componentes conexos na grade amostrada
            comps=[]
            while hits:
                seed=hits.pop(); stack=[seed]; comp=[seed]
                while stack:
                    x,y=stack.pop()
                    for dx,dy in ((step,0),(-step,0),(0,step),(0,-step)):
                        q=(x+dx,y+dy)
                        if q in hits: hits.remove(q); stack.append(q); comp.append(q)
                comps.append(comp)
            cand=[]
            for c in comps:
                xs=[q[0] for q in c]; ys=[q[1] for q in c]
                x1,x2=min(xs),max(xs); y1,y2=min(ys),max(ys)
                bw,bh=x2-x1,y2-y1
                if bw>=70 and bh>=22:
                    cand.append((bw*bh,len(c),x1,y1,x2,y2))
            if cand:
                cand.sort(reverse=True)
                _,_,x1,y1,x2,y2=cand[0]
                x=(x1+x2)/2; y=(y1+y2)/2
                print(f"[TESTE20][PIXEL-V2] {side} bbox=({x1},{y1})-({x2},{y2}) centro=({x:.0f},{y:.0f})", flush=True)
                # Chrome DevTools Input gera entrada de mouse no viewport, em vez
                # de MouseEvent sintetico via JavaScript (que a pagina pode ignorar).
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mouseMoved','x':x,'y':y})
                self._assert_entry_deadline()
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mousePressed','x':x,'y':y,'button':'left','buttons':1,'clickCount':1})
                self.d.execute_cdp_cmd('Input.dispatchMouseEvent', {'type':'mouseReleased','x':x,'y':y,'button':'left','buttons':0,'clickCount':1})
                self._persist_button_rect(side,{"x":x1,"y":y1,"width":x2-x1,"height":y2-y1},"PIXEL")
                print(f"[TESTE20][CLIQUE-ENVIADO] {side} via CDP; aguardando confirmacao visual da Bullex", flush=True)
                return True
            print(f"[TESTE20][PIXEL-V2] nenhum botao {side} isolado no painel direito", flush=True)
        except Exception as e:
            print(f"[TESTE20][PIXEL-V2][ERRO] {side}: {e!r}", flush=True)

        print(f"[DEMO][BLOQUEADO] Botao real {side} nao localizado; ordem NAO registrada como executada.", flush=True)
        return False

    def _record(self, decision_id, asset_id, cycle_start, leg, direction, stake,
                status, error=None):
        now = datetime.now().isoformat(timespec="seconds")
        candle_exec=(int(cycle_start)%300)//60+1 if cycle_start is not None else None
        build_revision=str(getattr(self,'cfg',{}).get('build_revision') or '').strip() or None
        if build_revision:
            import re
            version=re.search(r'V\d+(?:\.\d+)*',build_revision,re.I)
            release=re.search(r'\bR\d+\b',build_revision,re.I)
            if version and release: build_revision=version.group(0)+' - '+release.group(0)
        label=self._current_asset_name
        if not is_asset_name(label):
            label=None
        if not label:
            saved=lookup(self.db,asset_id)
            if saved:
                label=saved['asset_name']
        if not label and self.selected_asset_provider is not None:
            try:
                selected=self.selected_asset_provider(force=True) or {}
                if str(selected.get('asset_id') or '') == str(asset_id) and selected.get('label'):
                    label=' '.join(str(selected['label']).split())
            except Exception:
                pass
        label=label or 'ATIVO NÃO VINCULADO'
        catalog=resolve(self.db,label,asset_id) if label != 'ATIVO NÃO VINCULADO' else None
        our_asset_id=catalog['our_asset_id'] if catalog else None
        asset_name=catalog['asset_name'] if catalog else label
        # SQLite pode ficar ocupado por poucos instantes enquanto captura/resultados
        # persistem frames. Um lock transitório não pode perder o vínculo da ordem.
        # Retry é SOMENTE de persistência: nunca repete clique nem decisão.
        terminal = str(status or '').upper()
        last_db_error = None
        for attempt in range(4):
            try:
                self.db.execute("""
                    INSERT OR REPLACE INTO demo_orders
                    (id,decision_id,asset_id,our_asset_id,asset_name,bullex_active_id,
                     cycle_start,candle_exec,build_revision,leg,direction,stake,requested_at,executed_at,status,error,
                     direction_original,direction_executed,candle_flow_inverted)
                    VALUES(
                      COALESCE((SELECT id FROM demo_orders WHERE decision_id=? AND leg=?),NULL),
                      ?,?,?,?,?,?,
                      COALESCE((SELECT candle_exec FROM demo_orders WHERE decision_id=? AND leg=?),?),
                      COALESCE((SELECT build_revision FROM demo_orders WHERE decision_id=? AND leg=?),?),
                      ?,?,?,?,?,?,?,?,?,?
                    )
                """, (
                    decision_id, leg,
                    decision_id, asset_id, our_asset_id, asset_name, str(asset_id or ''), cycle_start,
                    decision_id,leg,candle_exec,decision_id,leg,build_revision,
                    leg, direction, stake,
                    now, now if status == "EXECUTED" else None, status, error,
                    getattr(self,'_active_direction_original',None) or direction,
                    getattr(self,'_active_direction_executed',None) or direction,
                    1 if getattr(self,'_active_candle_flow_inverted',False) else 0
                ))
                if terminal in ('EXECUTED','BLOCKED','ERROR','SKIPPED'):
                    self.db.execute("UPDATE decisions SET status=?,updated_at=? WHERE id=?",
                                    (terminal, now, decision_id))
                self.db.commit()
                last_db_error = None
                break
            except sqlite3.OperationalError as exc:
                try: self.db.rollback()
                except Exception: pass
                if 'locked' not in str(exc).lower() or attempt == 3:
                    raise
                last_db_error = exc
                wait = 0.08 * (2 ** attempt)
                print(f"[DB][LOCK][RETRY] decision_id={decision_id} tentativa={attempt+1}/4 espera={wait:.2f}s", flush=True)
                time.sleep(wait)
        if last_db_error is not None:
            raise last_db_error
        print(
            f"[ATIVO][CATALOGO] decision_id={decision_id} "
            f"our_asset_id={our_asset_id or '—'} asset_name={asset_name} "
            f"bullex_active_id={asset_id or '—'} status={status}",
            flush=True,
        )

    def _read_bullex_clock(self):
        """Return Bullex footer time; fall back to latest broker-feed time when footer DOM is inaccessible."""
        try:
            val=self.d.execute_script(r"""
                // A Bullex inclui a data entre o rótulo e a hora: "HORA ATUAL: 27 SETEMBRO, 21:42:56".
                const re=/HORA\s+ATUAL\s*:\s*(?:\d{1,2}\s+[A-ZÀ-Ú]+\s*,\s*)?(\d{1,2}):(\d{2}):(\d{2})/i;
                const clone=document.body ? document.body.cloneNode(true) : null;
                if(clone){ const ov=clone.querySelector('#bullex-pro-overlay'); if(ov) ov.remove(); }
                const body=String(clone && (clone.innerText||clone.textContent) || ''); // pagina Bullex sem overlay Raposo
                let m=body.match(re); if(m) return {h:+m[1],m:+m[2],s:+m[3],src:'footer-body'};
                let best=null;
                for(const e of [...document.querySelectorAll('body *')]){
                  const t=String(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim();
                  if(!t||t.length>180) continue; const mm=t.match(re); if(!mm) continue;
                  const r=e.getBoundingClientRect(); const score=(r.top>window.innerHeight*.72?100:0)+r.top/1000;
                  if(!best||score>best.score) best={h:+mm[1],m:+mm[2],s:+mm[3],score,src:'footer-node'};
                }
                return best;
            """)
            if val and 0 <= int(val.get('s',-1)) <= 59:
                return int(val['s']), int(val.get('m',0)), int(val.get('h',0)), str(val.get('src') or 'footer')
        except Exception:
            pass
        try:
            row=self.db.execute('SELECT broker_at FROM bullex_live_state ORDER BY last_received_at DESC LIMIT 1').fetchone()
            if row and row[0] is not None:
                t=time.localtime(float(row[0])); return int(t.tm_sec),int(t.tm_min),int(t.tm_hour),'broker'
        except Exception:
            pass
        t=time.localtime(); return t.tm_sec,t.tm_min,t.tm_hour,'system'

    def _wait_entry_window(self, asset_id, cycle_start=None):
        """Wait for the opening of the target M1 candle (chart countdown 00:59)."""
        if cycle_start is None:
            return False, None
        target=(int(cycle_start)//60)*60
        auto=self.cfg.get('auto_demo',{})
        send_second=float(auto.get('entry_send_second',0))
        open_tolerance=max(0.0,float(auto.get('entry_open_tolerance_seconds',1.0)))
        timeout=float(self.cfg.get('auto_demo',{}).get('entry_transition_timeout_seconds',4.0))
        # O sinal continua sendo pré-armado antes da virada; só o instante do
        # clique muda para os primeiros instantes do candle alvo.
        deadline=time.monotonic()+max(16.0,min(timeout,20.0))
        last=None
        while time.monotonic() < deadline:
            if not self.armed:
                return False, last
            clk=self._read_bullex_clock()
            row=self.db.execute("SELECT current_from,broker_at,last_received_at FROM bullex_live_state WHERE asset_id=?",(str(asset_id),)).fetchone()
            visible_sec=int(clk[0]) if clk else None
            # Usa a hora atual do rodapé da Bullex quando disponível. O feed do
            # broker é fallback: chega em pacotes espaçados e pode saltar a janela
            # do primeiro segundo. O contador M1 e current_from não são alterados.
            if clk and len(clk)>3 and str(clk[3]).startswith('footer'):
                target_local=time.localtime(target)
                target_minute=target_local.tm_hour*60+target_local.tm_min
                visible_minute=int(clk[2])*60+int(clk[1])
                if visible_minute==target_minute:
                    if send_second <= visible_sec < send_second+open_tolerance:
                        self._entry_click_deadline=time.monotonic()+max(0.0,1.0-visible_sec)
                        return True,visible_sec
                    if visible_sec is not None and visible_sec>=send_second+open_tolerance:
                        return False,visible_sec
                elif (visible_minute-target_minute)%1440 < 720:
                    return False,visible_sec
                time.sleep(.010)
                continue
            # Fallback para installations em que o DOM do rodapé não está acessível.
            broker_now=None
            if row and row[1] is not None:
                try:
                    recv=datetime.fromisoformat(str(row[2])).timestamp() if row[2] else time.time()
                    age=max(0.0,min(time.time()-recv,15.0))
                    broker_now=float(row[1])+age
                except Exception:
                    broker_now=None
            if broker_now is not None:
                candle_clock=int(broker_now//60)*60
                second_exact=float(broker_now%60)
                sec=int(second_exact)
            elif row and row[1] is not None:
                candle_clock=int(float(row[1])//60)*60
                second_exact=float(row[1])%60
                sec=int(second_exact)
            else:
                candle_clock=None
                sec=visible_sec
                second_exact=float(sec) if sec is not None else None
            last=sec
            # 00:59 restante no gráfico equivale ao segundo 00 do relógio M1.
            if (candle_clock == target and second_exact is not None
                    and send_second <= second_exact <= send_second+open_tolerance):
                self._entry_click_deadline=time.monotonic()+max(0.0,1.0-second_exact)
                return True, sec
            if candle_clock is not None and (candle_clock > target or
                    (candle_clock == target and second_exact is not None
                     and second_exact > send_second+open_tolerance)):
                return False, sec
            time.sleep(.010)
        return False, last

    def record_favorable_setup(self, decision_id, asset_id, cycle_start, leg, direction, stake, setup, score=None,
                               event_second=None):
        """Persist a favorable setup before any execution gate is evaluated."""
        score_text = '—' if score is None else f'{float(score):.1f}'
        reason = f'SETUP FAVORÁVEL | {setup} | score={score_text} | aguardando execução'
        try:
            self._record(decision_id, asset_id, cycle_start, leg, direction, stake, 'FAVORABLE', reason)
            second_text='' if event_second is None else f' | segundo={int(event_second)%60}'
            print(f'[DEMO][SETUP_FAVORAVEL] decision_id={decision_id} | {setup} | score={score_text}{second_text}', flush=True)
        except Exception as exc:
            # The attempt will continue; this diagnostic must not block the order.
            print(f'[DEMO][SETUP_FAVORAVEL][REGISTRO_PENDENTE] {type(exc).__name__}: {exc}', flush=True)

    def execute(self, decision_id, asset_id, cycle_start, leg, direction, stake, immediate=False,
                direction_original=None, candle_flow_inverted=False, event_second=None,
                event_observed_at=None):
        with self.lock:
            self._entry_asset_id=str(asset_id)
            self._entry_target=(int(cycle_start)//60)*60
            self._entry_click_deadline=None
            if not cf_observation.ENTRY_ENABLED and not self.cfg.get('risk_return',{}).get('replace_legacy_gates'):
                setup_row=self.db.execute("SELECT c1_dir FROM decisions WHERE id=?",(decision_id,)).fetchone()
                if setup_row and setup_row[0] == 'CANDLE_FLOW':
                    self._record(decision_id,asset_id,cycle_start,leg,direction,stake,"BLOCKED","CANDLE_FLOW em observacao; entradas suspensas")
                    return False
            if t4_exception_active() and not self.cfg.get('risk_return',{}).get('replace_legacy_gates'):
                row=self.db.execute("SELECT c1_dir FROM decisions WHERE id=?",(decision_id,)).fetchone()
                if not row or row[0] not in ALLOWED_SETUPS:
                    self._record(decision_id, asset_id, cycle_start, leg, direction, stake, "BLOCKED", "T4 30/09 exclusivo DIDI/SR_NIVEL")
                    return False
            self._active_direction_original = direction_original or direction
            self._active_direction_executed = direction
            self._active_candle_flow_inverted = bool(candle_flow_inverted)
            if not self.cfg.get("execute_orders", False):
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", "execute_orders=false")
                return False

            loss_allowed, loss_state = self._loss_gate()
            if not loss_allowed:
                reason = loss_state.get('reason') or 'STOP LOSS DO TURNO'
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", reason)
                print(f"[R12][STOP_LOSS][BLOQUEADO] {reason}; somente nova entrada bloqueada.", flush=True)
                voice_announce("blocked", reason)
                return False

            from risk_return_r12 import entry_gate
            risk_setup = self.db.execute('SELECT c1_dir FROM decisions WHERE id=?', (decision_id,)).fetchone()
            risk_ok, risk_reason = entry_gate(self.db, self.cfg, risk_setup[0] if risk_setup else '',asset_id=asset_id,target_ts=cycle_start)
            if not risk_ok:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake, 'BLOCKED', risk_reason)
                print('[R12][RISCO][BLOQUEADO] '+risk_reason, flush=True)
                return False
            from daily_setup_control import setup_gate
            setup_row = self.db.execute('SELECT c1_dir FROM decisions WHERE id=?', (decision_id,)).fetchone()
            setup_allowed, setup_reason = setup_gate(self.db, self.cfg, setup_row[0] if setup_row else '')
            if not setup_allowed:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake, 'BLOCKED', setup_reason)
                print(f'[DEMO][SETUP_DIA][BLOQUEADO] {setup_reason}', flush=True)
                return False
            if not self.armed or not self.safety_ready():
                _, detail=self.safety_status()
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", "sessao DEMO nao armada | "+detail)
                print("[DEMO][BLOQUEADO] Sessão não armada | "+detail, flush=True)
                voice_announce("blocked", "sessão não armada")
                return False

            auto = self.cfg.get("auto_demo", {})
            max_orders = int(auto.get("max_orders_per_session", 0))
            if max_orders > 0 and self.session_orders >= max_orders:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", "limite de ordens da sessao atingido")
                print("[DEMO][BLOQUEADO] Limite de ordens da sessão atingido.")
                return False

            min_stake = float(auto.get("require_min_stake", 10.0))
            if float(stake) < min_stake:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", f"stake abaixo do minimo configurado {min_stake}")
                return False

            if direction not in ("G", "R"):
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "SKIPPED", "direcao nao executavel")
                return False

            asset_safe, asset_detail=self._selected_asset_is_safe(asset_id)
            self._current_asset_name = asset_detail if asset_safe else None
            if not asset_safe:
                self.disarm()
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "BLOCKED", asset_detail)
                print('[DEMO][BLOQUEADO] '+asset_detail, flush=True)
                return False

            # Duplicate protection (V3.74 hotfix): alem de decision_id, bloqueia
            # qualquer segunda ordem do MESMO ativo/mesmo minuto/mesma perna. Isso
            # evita repeticao do clique quando o feed repete o sinal alguns segundos
            # depois ou quando o timestamp chega com pequena variacao.
            minute_start=(int(cycle_start)//60)*60
            row = self.db.execute("""
                SELECT status FROM demo_orders
                WHERE decision_id=? AND leg=?
            """, (decision_id, leg)).fetchone()
            if row and row[0] in ("EXECUTED","SUBMITTED"):
                print(f"[DEMO][IGNORADO] {leg} já executado para decision_id={decision_id}.")
                return False
            dup = self.db.execute("""
                SELECT id FROM demo_orders
                WHERE asset_id=? AND leg=? AND status='EXECUTED'
                  AND cycle_start>=? AND cycle_start<?
                ORDER BY id DESC LIMIT 1
            """, (str(asset_id), leg, minute_start, minute_start+60)).fetchone()
            if dup and self.cfg.get('demo_only') and self.cfg.get('multi_setup_demo',{}).get('enabled'):
                rows=self.db.execute("SELECT o.decision_id,d.c1_dir FROM demo_orders o LEFT JOIN decisions d ON d.id=o.decision_id WHERE o.asset_id=? AND o.leg=? AND o.status IN ('EXECUTED','SUBMITTED') AND o.cycle_start>=? AND o.cycle_start<?",(str(asset_id),leg,minute_start,minute_start+60)).fetchall()
                if len(rows)<2 and all(r[0]!=decision_id and r[1]!=(risk_setup[0] if risk_setup else '') for r in rows):dup=None
            if dup:
                print(f"[DEMO][IGNORADO] ordem duplicada bloqueada | ativo={asset_id} | minuto={minute_start} | {leg}", flush=True)
                return False

            canvas = self._find_canvas()
            if not canvas:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "ERROR", "canvas nao encontrado")
                print("[DEMO][ERRO] Canvas não encontrado.")
                return False

            try:
                if not self._ensure_expiry_1m():
                    # R6 manual: nunca altera a expiração automaticamente. Apenas
                    # confirma visualmente 1 MIN e bloqueia a ordem se não estiver certo.
                    self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                                 "BLOCKED", "expiracao 1 min nao confirmada (ajuste manual)")
                    print("[DEMO][BLOQUEADO] Expiração não está em 1 min; ajuste manual necessário.", flush=True)
                    return False

                if float(stake) != 10.0:
                    self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                                 "BLOCKED", "stake diferente de R$10")
                    return False
                # R6 manual: apenas confirma $10 imediatamente antes da ordem;
                # não altera o campo de investimento automaticamente.

                # DIDI Agulhada e gatilho de momento: score aprovado => clique agora.
                # Nao carregar o DIDI como SIGNAL_PENDING para outro instante.
                if immediate:
                    # The analyzer owns the fresh broker snapshot that produced the
                    # signal. Re-reading the footer here used a later/inconsistent
                    # clock and turned valid DIDI signals into SKIPPED_STALE.
                    max_late=int(self.cfg.get('auto_demo',{}).get('immediate_max_second',3))
                    dispatch_age=(time.monotonic()-float(event_observed_at)
                                  if event_observed_at is not None else 0.0)
                    max_dispatch_age=float(self.cfg.get('auto_demo',{}).get('immediate_dispatch_max_age_seconds',2.0))
                    signal_sec=(int(event_second)%60) if event_second is not None else None
                    if signal_sec is None or signal_sec >= max_late or dispatch_age > max_dispatch_age:
                        detail=(f"segundo {signal_sec}" if signal_sec is not None else "segundo ausente")
                        if dispatch_age > max_dispatch_age:
                            detail+=f", despacho {dispatch_age:.3f}s depois"
                        self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                                     "SKIPPED", f"sinal imediato tardio M1: {detail}")
                        print(f"[DEMO][SKIPPED_STALE] DIDI fora da janela M1 | {detail} | limite={max_late}", flush=True)
                        return False
                    entry_second = signal_sec
                    print(f"[DEMO][DIDI_IMEDIATO] decision_id={decision_id} | direcao={direction} | segundo={signal_sec} | latencia={dispatch_age:.3f}s", flush=True)
                else:
                    timing_ok, entry_second = self._wait_entry_window(asset_id, cycle_start)
                    if not timing_ok:
                        self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                                     "BLOCKED", f"janela de envio nao observada no candle de preparacao: segundo {entry_second}")
                        print(f"[DEMO][BLOQUEADO] Janela de abertura do candle alvo {cycle_start} não observada; segundo={entry_second}.", flush=True)
                        return False

                if immediate:
                    self._entry_click_deadline = time.monotonic()+max(0.0,1.0-float(entry_second or 0)-dispatch_age)
                voice_announce("entry", "acima" if direction == "G" else "abaixo")
                if not self._click_direction(canvas, direction):
                    raise RuntimeError("botao real de ordem nao localizado/clicado")

                # R3: _click_direction() somente retorna True depois que um clique real
                # foi disparado no controle ACIMA/ABAIXO (DOM, texto visivel ou CDP).
                # Esse e o evento operacional que precisa alimentar sessao, dedupe e
                # resolucao de WIN/LOSS. Antes a ordem ficava eternamente CLICK_SENT,
                # mesmo quando a corretora de fato abria a posicao.
                # O clique já foi enviado à Bullex. Falha posterior no SQLite
                # não pode converter uma ordem acionada em falha nem provocar novo
                # clique duplicado no mesmo ciclo.
                record_error=None
                try:
                    self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                                 "SUBMITTED", "aguardando vinculo broker/order_id")
                except Exception as exc:
                    record_error=f"{type(exc).__name__}: {exc}"
                    print(f"[DEMO][SUBMETIDA][REGISTRO_PENDENTE] clique confirmado; falha ao gravar SQLite: {record_error}", flush=True)
                self.session_orders += 1
                side = "ACIMA" if direction == "G" else "ABAIXO"
                print(f"[DEMO][SUBMETIDA] {leg} | {side} | R${stake:.2f} | ciclo={cycle_start} | aguardando broker/order_id | ordem_sessao={self.session_orders} | segundo={entry_second}", flush=True)
                voice_announce("sent", side.lower())
                return True

            except Exception as e:
                self._record(decision_id, asset_id, cycle_start, leg, direction, stake,
                             "ERROR", str(e))
                print("[DEMO][ERRO]", e)
                return False
