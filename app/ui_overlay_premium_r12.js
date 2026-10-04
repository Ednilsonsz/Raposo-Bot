const p=arguments[0],root=document.getElementById('bullex-pro-overlay');
if(!root)return;
const set=(id,value)=>{const e=document.getElementById(id);if(e)e.textContent=String(value??'—')};
if(!root.dataset.premiumReady){
  root.dataset.premiumReady='true';
  // Normalize the base updater's label in the same microtask, before repaint.
  const assetText=document.getElementById('bxasset');
  const normalizeAsset=()=>{const text=assetText.textContent.replace(/^ATIVO:\s*/i,'');if(assetText.textContent!==text)assetText.textContent=text};
  const assetObserver=new MutationObserver(normalizeAsset);
  assetObserver.observe(assetText,{childList:true,characterData:true,subtree:true});
  normalizeAsset();
  const style=document.createElement('style');style.id='raposo-premium-style';
  style.textContent=`
  #bullex-pro-overlay *{font-size:12px!important;font-weight:inherit!important;letter-spacing:normal!important;text-transform:none!important}
  #bullex-pro-overlay{--accent:#00ef78;--rgb:0,239,120;background:radial-gradient(ellipse at top left,#003124 0,transparent 45%),#030d10!important;border:0!important;border-right:1px solid #00d76e!important;border-radius:12px!important;box-shadow:inset 0 0 1px 1px #00d76e,0 0 15px #00e97a44!important}
  #bullex-pro-overlay #bxh{zoom:.95!important;height:auto!important;padding:12px 10px 9px!important;background:transparent!important;border-bottom:0!important}
  #bullex-pro-overlay .bxidentity{display:grid!important;grid-template-columns:48px 1fr auto!important;gap:8px!important;align-items:center!important}
  #bullex-pro-overlay .bxidentity strong{font-size:19px!important;font-weight:700!important;letter-spacing:.2px!important;color:#f4f8ff!important;white-space:nowrap!important}
  #bullex-pro-overlay #bxver{font-size:10px!important;color:#39b9ff!important;display:block!important}
  #bullex-pro-overlay .rpclock{text-align:right!important;font-size:10px!important;color:#75f9d4!important}
  #bullex-pro-overlay #rpdate{font-size:10px!important;white-space:nowrap!important}
  #bullex-pro-overlay #rpclock{display:block!important;font-size:16px!important;color:white!important;font-weight:700!important;white-space:nowrap!important}
  #bullex-pro-overlay #bxstate{display:block!important;width:fit-content!important;max-width:100%!important;margin:0!important;padding:2px 5px!important;border:1px solid #00a653!important;border-radius:5px!important;background:#063c24!important;font-size:9px!important}
  #bullex-pro-overlay .rpheading{display:flex!important;align-items:center!important;justify-content:space-between!important;gap:6px!important;margin-bottom:7px!important}
  #bullex-pro-overlay .rpheading>.rptitle{margin:0!important;white-space:nowrap!important}
  #bullex-pro-overlay #bxmodes{gap:4px!important;margin-top:9px!important}
  #bullex-pro-overlay #bxmodes button{height:26px!important;font-size:10px!important;border-color:#1773a4!important;background:#041c28!important;color:#8ee9ef!important}
  #bullex-pro-overlay .bxtiming>#bxexpiry{display:none!important}
  #bullex-pro-overlay .bxtiming>span:first-child{font-size:10px!important}
  #bullex-pro-overlay #bxmodes button.on{background:linear-gradient(#095a29,#042611)!important;border-color:#16ff74!important;color:#eaffec!important;box-shadow:0 0 8px #00ee7a66!important}
  #bullex-pro-overlay #bxclassic{zoom:.95!important;padding:0 8px 8px!important;height:calc(100vh - 130px)!important;overflow-y:auto!important;scrollbar-width:thin!important;scrollbar-color:#12603d #031110!important}
  #bullex-pro-overlay .rpcard,#bullex-pro-overlay .bxsignal,#bullex-pro-overlay #bxlosscontrol{margin:0 0 6px!important;padding:7px!important;border:1px solid #087e45!important;border-radius:8px!important;background:linear-gradient(135deg,#03201899,#020a0fe8)!important;box-shadow:inset 0 0 12px #00d2670c!important}
  #bullex-pro-overlay .rptitle{display:block!important;margin:0 0 7px!important;font-size:11px!important;letter-spacing:.5px!important;color:#74f0de!important;font-weight:700!important}
  #bullex-pro-overlay .rpstatuses{display:grid!important;grid-template-columns:1fr auto!important;gap:5px!important;font-size:11px!important}
  #bullex-pro-overlay .rpstatuses>span{font-size:11px!important;font-weight:400!important;margin:0!important;padding:0!important}
  #bullex-pro-overlay .rpstatuses b{font-size:10px!important;color:#abbbc8;font-weight:600!important;text-align:right}
  #bullex-pro-overlay .rpstatuses b.good{color:#00f080!important}
  #bullex-pro-overlay .rpstatuses b.bad{color:#ff6573!important}
  #bullex-pro-overlay #bxasset{display:block!important;font-size:12px!important;font-weight:700!important;color:#f2fcff!important;line-height:1.4!important;text-align:right!important;overflow-wrap:anywhere!important}
  #bullex-pro-overlay .bxtiming{margin:0!important;padding:0!important;border-top:0!important;font-size:10px!important;display:flex!important;gap:5px!important;align-items:center!important}
  #bullex-pro-overlay .bxtiming>span:first-child{white-space:nowrap!important}
  #bullex-pro-overlay .rpheading:has(#bxasset){gap:5px!important}
  #bullex-pro-overlay .rpheading:has(#bxasset)>.rptitle{font-size:9px!important}
  #bullex-pro-overlay #bxasset{font-size:10px!important}
  #bullex-pro-overlay .bxactions{display:grid!important;grid-template-columns:repeat(3,1fr)!important;gap:5px!important;margin:0!important}
  #bullex-pro-overlay .bxactions button{height:32px!important;font-size:10px!important}
  #bullex-pro-overlay #bxpause{background:#c19703!important;color:#fff!important;border-color:#ffcf00!important}
  #bullex-pro-overlay #bxpause.resume{background:#059b3c!important;border-color:#0ee264!important}
  #bullex-pro-overlay #bxstop{background:#c62334!important;color:white!important;border-color:#ff4457!important}
  #bullex-pro-overlay #bxrestart{background:#073842!important;border-color:#338b9a!important}
  #bullex-pro-overlay #bxvoice{grid-column:1/-1!important;height:22px!important;color:#a2bdc4!important}
  #bullex-pro-overlay .bxresults{display:grid!important;grid-template-columns:repeat(4,1fr)!important;gap:5px!important;margin:0!important}
  #bullex-pro-overlay .bxresults>div{padding:6px!important;min-height:50px!important;background:#021015!important;border:1px solid #135038!important;border-radius:6px!important}
  #bullex-pro-overlay .bxresults span{font-size:9px!important;white-space:nowrap!important}
  #bullex-pro-overlay .bxresults b{font-size:23px!important}
  #bullex-pro-overlay #bxwins{color:#00ed75!important}#bullex-pro-overlay #bxlosses{color:#ff495a!important}
  #bullex-pro-overlay .bxprofit{margin:9px 0 0!important;padding:6px 0 0!important;border-top:1px solid #154732!important}
  #bullex-pro-overlay #bxpl{font-size:20px!important;white-space:nowrap!important}
  #bullex-pro-overlay #bxlosscontrol>strong{font-size:11px!important;color:#79eedd!important;display:block!important;margin-bottom:7px!important}
  #bullex-pro-overlay #bxshiftrows{display:grid!important;grid-template-columns:1fr 1fr!important;gap:5px!important}
  #bullex-pro-overlay #bxshiftrows>div{font-size:10px!important;min-height:26px!important;padding:4px!important;border:1px solid #174338!important;border-radius:5px!important;line-height:1.4!important}
  #bullex-pro-overlay #bxshiftrows .current{background:#064725!important;border-color:#00dd6d!important}
  #bullex-pro-overlay #bxshiftrows .stop{color:#ff596d!important;border-color:#ac3542!important;background:#361018!important}
  #bullex-pro-overlay .bxsignal>div{margin-bottom:4px!important}
  #bullex-pro-overlay .bxsignal small{font-size:10px!important;color:#9aaeba!important}
  #bullex-pro-overlay .rplastrow{display:flex!important;justify-content:space-between!important;gap:5px!important;margin:4px 0!important;font-size:10px!important}
  #bullex-pro-overlay .rplastrow>*{font-size:10px!important;margin:0!important;padding:0!important}
  #bullex-pro-overlay #rplastasset{font-size:12px!important;font-weight:700!important;color:#f0f5ff!important}
  #bullex-pro-overlay .rpfooter{display:flex!important;justify-content:space-between!important;font-size:9px!important;color:#43d5c8!important;margin:8px 0!important}
  #bullex-pro-overlay #rpobservation{font-size:10px!important;color:#ffcc55!important}
  `;
  document.head.appendChild(style);
  const identity=root.querySelector('.bxidentity'),title=identity.querySelector('strong'),version=document.getElementById('bxver');
  title.textContent='RAPOSO BOT';
  const branding=document.createElement('div');branding.append(title,version);
  const logo=document.createElement('div');logo.innerHTML=`<svg viewBox="0 0 64 64" width="48" height="48" aria-label="Raposo"><path d="M8 5 25 17 39 17 56 5 52 35 32 60 12 35Z" fill="#e98116" stroke="#ffc744" stroke-width="2"/><path d="m8 5 8 23 10-10M56 5 48 28 38 18" fill="#a94411"/><path d="m13 30 15 5 4 17 4-17 15-5-6 16-13 14-13-14Z" fill="#eaf7ff"/><path d="m18 28 9 3-7 4Zm28 0-9 3 7 4Z" fill="#052531"/><path d="m27 42 10 0-5 7Z" fill="#092333"/></svg>`;
  const clock=document.createElement('div');clock.className='rpclock';clock.innerHTML='<span id="rpdate"></span><b id="rpclock"></b>';
  identity.replaceChildren(logo,branding,clock);root.querySelector('#bxh').insertBefore(document.getElementById('bxstate'),document.getElementById('bxmodes'));
  const main=document.getElementById('bxclassic');
  const card=(heading,...nodes)=>{const e=document.createElement('section');e.className='rpcard';const h=document.createElement('strong');h.className='rptitle';h.textContent=heading;e.append(h,...nodes);return e};
  const asset=card('ATIVO ATUAL',document.getElementById('bxasset'),main.querySelector('.bxtiming'));
  const control=card('CONTROLE',root.querySelector('.bxactions'));
  for(const [section,node] of [[asset,asset.querySelector('#bxasset')],[control,document.getElementById('bxstate')]]){
    const heading=document.createElement('div');heading.className='rpheading';heading.append(section.querySelector('.rptitle'),node);section.prepend(heading);
  }
  const statuses=document.createElement('div');statuses.className='rpstatuses';statuses.innerHTML='<span>Captura M1</span><b id="rpcapture"></b><span>Motor de decisão</span><b id="rpmotor"></b><span>Executor</span><b id="rpexecutor"></b><span>Expiração lida</span><b id="rpexpiry"></b><span>Drive Sync</span><b id="rpsync"></b>';
  const system=card('STATUS DO SISTEMA',statuses);
  const results=main.querySelector('.bxresults'),draw=document.createElement('div');draw.innerHTML='<span>DRAW</span><b id="rpdraw">—</b>';results.insertBefore(draw,results.children[2]);
  const score=card('PLACAR DO DIA',results,main.querySelector('.bxprofit'));
  const last=document.createElement('div');last.innerHTML='<div class="rplastrow"><span id="rplasttime"></span><b id="rplastresult"></b></div><div id="rplastasset"></div><div class="rplastrow"><b id="rplastside"></b><span id="rplaststake"></span><b id="rplastpl"></b></div>';
  const lastCard=card('ÚLTIMA OPERAÇÃO FECHADA',last);
  const signal=main.querySelector('.bxsignal'),signalTitle=document.createElement('strong');signalTitle.className='rptitle';signalTitle.textContent='ESTRATÉGIA / SINAL';signal.prepend(signalTitle);const observation=document.createElement('div');observation.id='rpobservation';signal.append(observation);
  const meta=main.querySelector('.bxmeta'),stop=document.getElementById('bxlosscontrol');
  const footer=document.createElement('div');footer.className='rpfooter';footer.textContent='Disciplina gera consistência';
  main.replaceChildren(asset,control,system,signal,score,stop,meta,footer);
  asset.querySelector('.rpheading').append(asset.querySelector('.bxtiming'));
  asset.querySelector('.rpheading').style.setProperty('margin-bottom','0','important');
}
document.getElementById('bxclassic').style.setProperty('height',`${Math.max(0,(innerHeight-root.querySelector('#bxh').getBoundingClientRect().height)/.95)}px`,'important');
set('rpdate',p.panel_date);set('rpclock',p.panel_clock);set('bxexpiry',p.actual_expiry);set('rpexpiry',p.actual_expiry);
root.querySelector('.bxtiming>span:first-child').textContent='Gráfico M1';
set('bxasset',String(p.asset_label||'CARREGANDO').replace(/^ATIVO:\s*/i,''));
set('rpcapture',p.capture_state);set('rpmotor',p.engine_state);set('rpexecutor',p.executor_state);set('rpsync',p.sync_state);set('rpdraw',p.draws);
for(const [id,good,bad] of [['rpcapture',p.capture_state==='RECEBENDO',p.capture_state==='CONGELADA'],['rpmotor',p.engine_state==='ANALISANDO',false],['rpexecutor',p.armed,!p.armed],['rpexpiry',p.actual_expiry==='1 MIN',p.actual_expiry!=='1 MIN']]){
  const el=document.getElementById(id);el.classList.toggle('good',!!good);el.classList.toggle('bad',!!bad);
}
document.getElementById('bxpause').classList.toggle('resume',!!p.paused);
set('rpobservation',p.setup==='CANDLE_FLOW'?'CANDLE_FLOW: SOMENTE OBSERVAÇÃO':'');
