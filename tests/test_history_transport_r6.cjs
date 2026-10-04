const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../broker_history_transport_r11.js'),'utf8');
function harness(){
 let now=Date.parse('2026-09-16T18:00:00Z'),tick; const sent=[];
 class Clock extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
 class Socket{static OPEN=1;constructor(url){this.url=url;this.readyState=1;this.listeners={};}addEventListener(n,f){(this.listeners[n]??=[]).push(f);}emit(n,obj){for(const f of this.listeners[n]||[])f(n==='message'?{data:JSON.stringify(obj)}:{});}send(data){if(data==='throw')throw Error('native');sent.push(JSON.parse(data));return 'original';}}
 const original=Socket.prototype.send;
 const context={window:{WebSocket:Socket},URL,Intl,Date:Clock,setInterval:f=>tick=f};vm.runInNewContext(source,context);
 return {Socket,original,sent,context,step(ms=1000){now+=ms;tick();},connect(url='wss://ws.trade.bull-ex.com'){return new context.window.WebSocket(url);}};
}
{
 const h=harness(),ws=h.connect();assert.ok(ws instanceof h.Socket);assert.equal(ws.send,h.original);assert.throws(()=>ws.send('throw'),/native/);
 h.step();assert.equal(h.sent.length,0);ws.emit('message',{name:'profile',msg:{balances:[{id:11,type:1}]}});h.step();assert.equal(h.sent.length,0);
 ws.emit('message',{name:'profile',msg:{balances:[{id:11,type:1},{id:22,type:4}]}});h.step();assert.equal(h.sent.length,1);
 const req=h.sent[0];assert.equal(req.msg.name,'portfolio.get-history-positions');assert.equal(req.msg.body.user_balance_id,22);assert.equal(req.msg.body.start,Date.parse('2026-09-16T03:00:00Z')/1000);assert.equal(req.msg.body.offset,0);
 assert.deepEqual(Array.from(req.msg.body.instrument_types),['binary-option','turbo-option','blitz-option']);
 h.step();assert.equal(h.sent.length,1);ws.emit('message',{name:'history-positions',request_id:req.request_id,msg:{positions:Array(100).fill({})}});h.step();assert.equal(h.sent[1].msg.body.offset,100);
 ws.emit('message',{name:'history-positions',request_id:h.sent[1].request_id,msg:{positions:[]}});h.step();assert.equal(h.sent.length,2);assert.equal(h.context.window.__raposoHistory.status,'HISTORY_FETCHED');h.step(120000);assert.equal(h.sent.length,3);
 ws.emit('close');h.step(120000);assert.equal(h.sent.length,3);
 assert.ok(h.sent.every(r=>r.msg.name==='portfolio.get-history-positions'));
}
{
 const h=harness(),ws=h.connect('wss://attacker.example');ws.emit('message',{name:'profile',msg:{balances:[{id:22,type:4}]}});h.step();assert.equal(h.sent.length,0);
}
{
 const h=harness(),ws=h.connect();ws.emit('message',{name:'profile',msg:{balances:[{id:22,type:4}]}});h.step();h.step(16000);assert.equal(h.sent.length,1);assert.equal(h.context.window.__raposoHistory.status,'HISTORY_TIMEOUT');h.step(10000);assert.equal(h.sent.length,2);
}
console.log('History transport: native behavior, demo-only, origin, day bounds, pagination, throttle, reconnect and timeout OK');
