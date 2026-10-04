/* Read-only history transport. Original WebSocket send and trade messages are untouched. */
(() => {
  if (window.__raposoHistory) return;
  const Native = window.WebSocket;
  const state = {status:'WAITING_DEMO_ACCOUNT', pages:0, day:null, account:null, balances:[]};
  window.__raposoHistory = state;
  let socket=null, account=null, pending=null, offset=0, retryAt=0, end=0, start=0;
  let failures=0, generation=0;
  const day = () => new Intl.DateTimeFormat('en-CA', {timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  function observe(ws, text) {
    let envelope; try { envelope=JSON.parse(text); } catch { return; }
    const message=envelope.msg;
    if (envelope.name==='profile' || envelope.name==='balances') {
      const balances=Array.isArray(message)?message:message?.balances;
      if (Array.isArray(balances)) {
        state.balances=balances.filter(b=>b && b.id!=null).map(b=>({
          id:String(b.id), type:Number(b.type),
          currency:String(b.currency ?? ''),
          selected:Boolean(b.selected ?? b.is_selected ?? b.isSelected ??
            b.current ?? b.is_current ?? b.isCurrent ?? b.active ?? b.is_active ?? b.isActive)
        }));
        const demo=balances.filter(b=>Number(b.type)===4 && b.id!=null);
        if(demo.length===1 && account!==demo[0].id) {
          account=demo[0].id;socket=ws;pending=null;offset=0;retryAt=0;state.account=String(account);
        }
      }
    }
    if (!pending || String(envelope.request_id)!==pending.id) return;
    // The initial result ACK is not the history page; keep the request pending.
    if(envelope.name==='result' && !Array.isArray(message?.positions))return;
    pending=null;
    const items=message?.positions;
    if (!Array.isArray(items)) { state.status='HISTORY_SCHEMA_UNSUPPORTED';end=0;retryAt=Date.now()+120000;return; }
    state.pages++; failures=0;
    if(items.length<100) { state.status='HISTORY_FETCHED';retryAt=Date.now()+120000;offset=0;end=0; }
    else if(offset>=9900) {state.status='HISTORY_PAGE_LIMIT';retryAt=Date.now()+120000;offset=0;end=0;}
    else {offset+=items.length;state.status='FETCHING';retryAt=Date.now()+1000;}
  }
  window.WebSocket = new Proxy(Native, {construct(target,args,newTarget) {
    const ws=Reflect.construct(target,args,newTarget);
    try {
      const url=new URL(ws.url);
      if(url.protocol==='wss:' && (url.hostname==='bull-ex.com' || url.hostname.endsWith('.bull-ex.com'))) {
        ws.addEventListener('message',e=>{try{observe(ws,e.data);}catch{state.status='HISTORY_READ_ERROR';}});
        ws.addEventListener('close',()=>{if(socket===ws){socket=null;account=null;pending=null;state.status='WAITING_DEMO_ACCOUNT';}});
      }
    } catch {}
    return ws;
  }});
  setInterval(()=>{
    if(!socket || socket.readyState!==Native.OPEN || account==null)return;
    const current=day();
    if(current!==state.day){state.day=current;offset=0;pending=null;end=0;retryAt=0;generation++;}
    if(pending){if(Date.now()-pending.at<15000)return;pending=null;end=0;failures++;state.status='HISTORY_TIMEOUT';retryAt=Date.now()+Math.min(120000,10000*failures);return;}
    if(Date.now()<retryAt)return;
    if(!end){start=Date.parse(current+'T00:00:00-03:00')/1000;end=Math.floor(Date.now()/1000);}
    const id='raposo-history:'+generation+':'+Date.now()+':'+offset;
    const request={name:'sendMessage',request_id:id,msg:{name:'portfolio.get-history-positions',body:{instrument_types:['binary-option','turbo-option','blitz-option'],user_balance_id:account,limit:100,offset,start,end}}};
    try{Native.prototype.send.call(socket,JSON.stringify(request));pending={id,at:Date.now()};state.status='FETCHING';}
    catch{state.status='HISTORY_SEND_FAILED';retryAt=Date.now()+30000;}
  },1000);
})();
