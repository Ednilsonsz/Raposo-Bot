const fs=require('fs'),vm=require('vm'),assert=require('assert');
let now=Date.parse('2026-10-04T01:00:00-03:00'),tick;
class Clock extends Date{constructor(...args){super(...(args.length?args:[now]))}static now(){return now}}
class Socket{constructor(url){this.url=url;this.readyState=1;this.listeners={};this.sent=[]}addEventListener(k,fn){this.listeners[k]=fn}send(p){this.sent.push(JSON.parse(p))}emit(obj){this.listeners.message({data:JSON.stringify(obj)})}}Socket.OPEN=1;
const window={WebSocket:Socket};vm.runInNewContext(fs.readFileSync('app/broker_history_transport_r11.js','utf8'),{window,URL,Date:Clock,Intl,setInterval:fn=>{tick=fn}});
const socket=new window.WebSocket('wss://ws.bull-ex.com/');
socket.emit({name:'profile',msg:{balances:[{id:42,type:4}]}});tick();
assert.equal(socket.sent.length,1);const first=socket.sent[0];
socket.emit({name:'result',request_id:first.request_id,msg:{success:true}});tick();
assert.equal(socket.sent.length,1);assert.equal(window.__raposoHistory.status,'FETCHING');
socket.emit({name:'history-positions',request_id:first.request_id,msg:{positions:[]}});
assert.equal(window.__raposoHistory.status,'HISTORY_FETCHED');assert.equal(window.__raposoHistory.pages,1);
now+=121000;tick();assert.equal(socket.sent.length,2);assert(socket.sent[1].msg.body.end>first.msg.body.end);
console.log('History ACK, final page and fresh cutoff: OK');
