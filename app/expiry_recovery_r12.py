"""Restore a reset broker expiry; never submits an order or assumes success."""
TARGET_JS = r"""
const phase=arguments[0];
const norm=s=>String(s||'').trim().toLowerCase().replace(/\s+/g,' ');
const expected=phase==='open'?/^(5\s*(s|seg|segundos?))$/:/^(1\s*(min|minuto|minutos))$/;
const nodes=[...document.querySelectorAll('button,[role="button"],div,span')].filter(e=>{
 if(e.closest('#bullex-pro-overlay'))return false;
 const r=e.getBoundingClientRect(),s=getComputedStyle(e);
 return r.width>12&&r.height>8&&r.width<260&&r.height<85&&r.left>innerWidth*.70&&r.top>60&&r.bottom<(phase==='open'?Math.min(innerHeight,400):innerHeight-35)&&s.visibility!=='hidden'&&s.display!=='none'&&expected.test(norm(e.innerText));
});
// Nested wrappers for the same option are one target; unrelated options are ambiguous.
const leaves=nodes.filter(e=>!nodes.some(other=>other!==e&&e.contains(other)));
if(leaves.length!==1)return null;
const e=leaves[0],r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
const hit=document.elementFromPoint(x,y);
if(!hit||!(e.contains(hit)||hit.contains(e)))return null;
return {x,y,label:norm(e.innerText)};
"""


def visual_target(driver, phase):
    """OCR exact duration in broker-only crop; never infer a duration from an icon."""
    import re,time
    from bullex_controls_reader import _capture_control_cards,ocr_image,normalize
    image,state,started=_capture_control_cards(driver,crop_height=None if phase=='select' else 400)
    words=ocr_image(image)
    number='5' if phase=='open' else '1'
    units=r's|seg|segundos?' if phase=='open' else r'min|minuto|minutos'
    matches=[]
    for w in words:
        text=normalize(w['text'])
        if re.fullmatch(number+r'\s*(?:'+units+r')',text):matches.append([w])
        elif text==number:
            for u in words:
                if re.fullmatch(units,normalize(u['text'])) and 0<=u['x']-(w['x']+w['width'])<24 and abs((u['y']+u['height']/2)-(w['y']+w['height']/2))<5:matches.append([w,u])
    boxes=[]
    for pair in matches:
        x=min(w['x'] for w in pair);y=min(w['y'] for w in pair)
        right=max(w['x']+w['width'] for w in pair);bottom=max(w['y']+w['height'] for w in pair)
        if y<70 or bottom>(image.height-35 if phase=='select' else 350):continue
        box=(int(state['width']*.75)+x,y,right-x,bottom-y)
        if box not in boxes:boxes.append(box)
    if len(boxes)!=1 or driver.execute_script(__import__('bullex_controls_reader')._CONTROL_STATE_JS)!=state:return None
    x,y,w,h=boxes[0]
    fresh,fresh_state,_=_capture_control_cards(driver,crop_height=None if phase=='select' else 400)
    local_x=x-int(state['width']*.75)
    box=(max(0,int(local_x)-3),max(0,int(y)-3),int(local_x+w)+3,int(y+h)+3)
    if fresh_state!=state or fresh.crop(box).tobytes()!=image.crop(box).tobytes():return None
    if driver.execute_script('return window.__RAPOSO_PAUSE_REQUEST__===true;'):return None
    return {'x':x+w/2,'y':y+h/2,'label':number+' '+('SEG' if phase=='open' else 'MIN'),'source':'SCREEN_OCR'}

def restore_step(executor):
    import time
    now=time.monotonic()
    phase=getattr(executor,'_expiry_recovery_phase',None)
    if phase=='select' and now-getattr(executor,'_expiry_recovery_at',0)>20:
        executor._expiry_recovery_phase=None
        print('[R12][EXPIRACAO][RECUPERACAO] menu expirou; permanece bloqueado',flush=True)
        return False
    if not phase:
        diag=getattr(executor,'_control_read_diagnostic',{}) or {}
        worker=getattr(executor,'_amount_read_thread',None)
        allowed,_=executor._loss_gate()
        if (diag.get('expiry') not in (None,'5 SEG')
            or now-getattr(executor,'_expiry_recovery_at',0)<15 or not allowed
            or (worker is not None and worker.is_alive())
            or executor.cfg.get('demo_only') is not True or not executor.session_valid()):return False
        phase='open'
    executor.disarm()
    executor._expiry_1m_confirmed_by_code=False
    target=executor.d.execute_script(TARGET_JS,phase)
    if not target:target=visual_target(executor.d,phase)
    if not target:
        if phase=='open':
            executor._expiry_recovery_at=time.monotonic()
            print('[R12][EXPIRACAO][RECUPERACAO] seletor sem alvo unico; permanece bloqueado',flush=True)
        return False
    for kind,buttons in [('mousePressed',1),('mouseReleased',0)]:
        executor.d.execute_cdp_cmd('Input.dispatchMouseEvent',{'type':kind,'x':target['x'],'y':target['y'],'button':'left','buttons':buttons,'clickCount':1})
    if phase=='open':
        executor._expiry_recovery_phase='select';executor._expiry_recovery_at=time.monotonic()
        print('[R12][EXPIRACAO][RECUPERACAO] 5 SEG detectado; abrindo seletor',flush=True)
    else:
        executor._expiry_recovery_phase=None;executor._expiry_recovery_at=time.monotonic()
        executor._controls_pixel_cache={};executor._control_read_diagnostic={}
        executor._amount_read_result=None
        print('[R12][EXPIRACAO][RECUPERACAO] 1 MIN selecionado; aguardando confirmacao visual independente',flush=True)
    return True
