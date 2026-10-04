import json, time, uuid
from urllib.parse import urlsplit
from asset_catalog import lookup as lookup_asset_catalog, resolve as resolve_asset_catalog

# R6: nomes amigaveis por active_id confirmado pelo WebSocket.
# A identificacao operacional continua sendo pelo ID; esta tabela e apenas de exibicao.
BULLEX_ASSET_NAMES = {
    "1": 'EURUSD',
    # A Bullex identifica EUR/USD OTC no WebSocket como active_id 76.
    # Sem este mapeamento, a assinatura M1 e o cabeÃƒÂ§alho visÃƒÂ­vel divergiam e
    # o painel bloqueava o ativo apesar de o par estar confirmado na pÃƒÂ¡gina.
    "76": 'EURUSD-OTC',
    "2": 'EURGBP',
    "3": 'GBPUSD',
    "4": 'USDJPY',
    "5": 'AUDUSD',
    "7": 'USDCAD',
    "8": 'USDCHF',
    "10": 'NZDUSD',
    # Bullex active_id observado ao selecionar NZD/USD(OTC).
    "80": 'NZDUSD-OTC',
    # Bullex active_id observado ao selecionar EUR/GBP(OTC).
    "77": 'EURGBP-OTC',
    # Bullex active_id observado ao selecionar EUR/JPY(OTC).
    "79": 'EURJPY-OTC',
    # ID 86 confirmado nos prints do grafico AUD/CAD OTC e feed M1 local.
    "86": 'AUDCAD-OTC',
    "13": 'AUDJPY',
    "14": 'EURJPY',
    "101": 'CADCHF',
    "103": 'CHFJPY',
    "104": 'EURAUD',
    "105": 'EURCAD',
    "107": 'GBPCHF',
    "108": 'GBPJPY',
    "1001": 'EURUSD-OTC',
    "1002": 'EURGBP-OTC',
    "1003": 'GBPUSD-OTC',
    "1004": 'USDJPY-OTC',
    "1005": 'AUDUSD-OTC',
    "1007": 'USDCAD-OTC',
    "1008": 'USDCHF-OTC',
    "1010": 'NZDUSD-OTC',
    "1013": 'AUDJPY-OTC',
    "1014": 'EURJPY-OTC',
    "1104": 'EURAUD-OTC',
    "1108": 'GBPJPY-OTC',
    "39": 'BTCUSD',
    "40": 'ETHUSD',
    "41": 'LTCUSD',
    "42": 'XRPUSD',
    "44": 'EOSUSD',
    "45": 'OMGUSD',
    "46": 'TRXUSD',
    "48": 'DASHUSD',
    "49": 'ZECUSD',
    "53": 'BCHUSD',
    "20": 'GOLD',
    "21": 'SILVER',
    "22": 'PLATINUM',
    "23": 'CRUDEOIL',
    "24": 'BRENT',
    "25": 'APPLE',
    "26": 'GOOGLE',
    "27": 'FACEBOOK',
    "28": 'AMAZON',
    "29": 'MICROSOFT',
    "30": 'TESLA',
    "31": 'NETFLIX',
    "33": 'TWITTER',
    "34": 'ALIBABA',
    "35": 'BAIDU',
    "50": 'US30',
    "51": 'SP500',
    "52": 'NASDAQ100',
    "54": 'GER30',
    "55": 'UK100',
    "56": 'FRA40',
    "57": 'AUS200',
}
from datetime import datetime
from database import connect

class BullexLiveCapture:
    def __init__(self, driver, asset_id=None):
        self.d = driver
        self.results = None
        self._result_socket_prefix = uuid.uuid4().hex
        # O valor do config (normalmente 76) ÃƒÂ© somente legado; o estado
        # operacional comeÃƒÂ§a vazio e serÃƒÂ¡ preenchido pelo grÃƒÂ¡fico confirmado.
        self._configured_asset_id = str(asset_id) if asset_id not in (None, "", "None") else ""
        self.asset_id = ""
        self.asset_label = None
        self._selected_asset = {'asset_id': None, 'label': 'NAO IDENTIFICADO', 'verified': False}
        self._last_asset_label_check = 0.0
        self.db = connect()
        self.ws_urls = {}
        self.last_console = 0
        self.live_events = 0
        self.history_candles = 0
        # R6: reconcile active_id from the real live stream when DOM cannot expose it.
        self._candidate_live_asset = None
        self._candidate_live_asset_hits = 0
        # R6: acompanha a assinatura M1 enviada pela propria pagina. Isso identifica
        # o ativo selecionado sem adivinhar pelo fluxo recebido de outros ativos.
        self._selected_ws_asset = None
        self._ws_m1_subscriptions = set()
        # Momento em que a assinatura M1 atual foi efetivamente observada/reconstruida.
        # Usado para casar o nome do grafico com um ID fresco, inclusive no primeiro boot.
        self._selected_ws_confirmed_at = 0.0
        self._pending_graph_since = 0.0
        self._ensure_schema()


    def selected_asset_snapshot(self, force=False):
        """Read the Bullex asset currently shown inside the trading chart."""
        self.refresh_selected_asset_label(force=force)
        self.db.commit()  # provider returns to a different executor connection
        return dict(self._selected_asset)

    def refresh_selected_asset_label(self, force=False):
        """Identifica o ativo pelo M1 e usa o cabecalho apenas para validar o nome.

        A ordem continua sendo executada por clique na tela. Portanto, a falta do texto
        do cabecalho nunca invalida um active_id confirmado pelo feed M1.
        """
        if not force and time.time() - self._last_asset_label_check < 0.20:
            return self.asset_label
        self._last_asset_label_check = time.time()

        try:
            info = self.d.execute_script(r"""
                const slashPair=/\b[A-Z0-9]{2,12}\s*\/\s*[A-Z0-9]{2,12}\b/i;
                const compactPair=/\b[A-Z]{3}[-/]?[A-Z]{3}\s*(?:\(\s*OTC\s*\)|OTC)\b/i;
                const otc=/OTC/i;
                const vis=e=>{const r=e.getBoundingClientRect(),c=getComputedStyle(e);return r.width>18&&r.height>8&&r.bottom>0&&r.top<280&&c.display!=='none'&&c.visibility!=='hidden'&&Number(c.opacity||1)>0};
                const parse=t=>{
                  t=String(t||'').trim().toUpperCase();
                  let m=t.match(slashPair); if(m) return {label:m[0].replace(/\s+/g,''),otc:otc.test(t)};
                  m=t.match(compactPair); if(m){let z=m[0].replace(/[^A-Z0-9]/g,''); z=z.replace(/OTC$/,''); return {label:z.slice(0,3)+'/'+z.slice(3,6),otc:true};}
                  return null;
                };
                let out=[];
                for(const e of [...document.querySelectorAll('body *')]){
                  if(!vis(e)||e.closest('#bullex-pro-overlay')||e.querySelector('#bullex-pro-overlay')) continue;
                  const r=e.getBoundingClientRect(); if(r.left<40||r.left>1500) continue;
                  const vals=[e.innerText,e.textContent,e.getAttribute('title'),e.getAttribute('aria-label')].filter(Boolean);
                  let hit=null, raw='';
                  for(const v of vals){const p=parse(v); if(p){hit=p;raw=String(v);break;}}
                  if(!hit) continue;
                  let anc=e,aid=null,full=raw;
                  for(let i=0;i<7&&anc;i++,anc=anc.parentElement){
                    const more=String(anc.innerText||anc.textContent||''); if(more.length<180) full+=' '+more;
                    if(anc.attributes) for(const a of anc.attributes){if(/active|asset|instrument|symbol/i.test(a.name)&&/^\d{1,4}$/.test(String(a.value||''))) aid=String(a.value);}
                  }
                  out.push({label:hit.label,otc:hit.otc||otc.test(full),top:r.top,left:r.left,active_id:aid,raw_len:String(raw).length});
                }
                if(!out.length) return null;
                const graphHeaders=out.filter(x=>x.top>=70&&x.top<230);
                if(graphHeaders.length) out=graphHeaders;
                const unique=[];
                for(const x of out.sort((a,b)=>a.raw_len-b.raw_len||a.top-b.top||a.left-b.left)) if(!unique.some(y=>y.label===x.label&&y.otc===x.otc)) unique.push(x);
                // Menus e ancestrais podem conter vÃƒÂ¡rios pares; o texto menor ÃƒÂ©
                // o cabeÃƒÂ§alho especÃƒÂ­fico do grÃƒÂ¡fico clicado, nÃƒÂ£o a lista inteira.
                // O cabecalho do grafico fica abaixo das abas de instrumentos.
                // A aba antiga pode ter texto menor e continuar assinando M1.
                const headers=unique.filter(x=>x.top>=70&&x.top<230);
                return (headers.length?headers:unique).sort((a,b)=>a.raw_len-b.raw_len||a.top-b.top||a.left-b.left)[0]||null;
            """)
        except Exception as e:
            print('[BULLEX LIVE][ATIVO][DOM]', repr(e), flush=True)
            info = None

        ws_aid = str(self._selected_ws_asset) if self._selected_ws_asset not in (None, '', 'None') else None
        if not info or not info.get('label'):
            # Em algumas versÃƒÂµes da Bullex o cabeÃƒÂ§alho fica dentro de um canvas
            # e nÃƒÂ£o aparece no DOM. A assinatura M1 enviada pela prÃƒÂ³pria pÃƒÂ¡gina
            # continua sendo a identificaÃƒÂ§ÃƒÂ£o do grÃƒÂ¡fico selecionado.
            if ws_aid:
                catalog_row=None
                try:
                    catalog_row=lookup_asset_catalog(self.db, ws_aid)
                except Exception as exc:
                    print('[BULLEX LIVE][ATIVO][CATALOGO]', repr(exc), flush=True)
                label=(catalog_row or {}).get('asset_name') or BULLEX_ASSET_NAMES.get(ws_aid)
                if not label:
                    # A Bullex recebe a ordem pelo clique no botÃƒÂ£o do grÃƒÂ¡fico;
                    # nÃƒÂ£o enviamos active_id. O ID WS provisÃƒÂ³rio identifica o feed
                    # atual e nÃƒÂ£o deve bloquear a execuÃƒÂ§ÃƒÂ£o do clique.
                    provisional=ws_aid or str(info.get('active_id') or '')
                    self.asset_id=provisional
                    self.asset_label=f'ATIVO WS {ws_aid}'
                    self._selected_asset={'asset_id':provisional or None,'label':self.asset_label,'verified':bool(provisional)}
                    return self.asset_label
                if not catalog_row:
                    try:
                        resolve_asset_catalog(self.db, label, ws_aid, confirmed_by='ws-m1')
                    except Exception as exc:
                        print('[BULLEX LIVE][ATIVO][CATALOGO]', repr(exc), flush=True)
                self.asset_id=ws_aid
                self.asset_label=label
                self._selected_asset={'asset_id':ws_aid,'label':label,'verified':True}
                return label
            if time.time() - float(getattr(self, '_last_graph_missing_notice', 0.0) or 0.0) >= 3.0:
                self._last_graph_missing_notice=time.time()
                print('[BULLEX LIVE][ATIVO][GRAFICO] cabecalho interno nao localizado; aguardando feed M1',flush=True)
            self._selected_asset = {'asset_id': None, 'label': 'AGUARDANDO ATIVO', 'verified': False}
            self.asset_label = 'AGUARDANDO ATIVO'
            return self.asset_label

        graph_label = str(info['label']).upper().replace(' ', '')
        if info.get('otc') and 'OTC' not in graph_label:
            graph_label += '-OTC'
        graph_label = graph_label.replace('/', '').replace('(', '').replace(')', '').replace(' OTC', '-OTC')
        if graph_label.endswith('OTC') and not graph_label.endswith('-OTC'):
            graph_label = graph_label[:-3].rstrip('-') + '-OTC'

        # A seleÃƒÂ§ÃƒÂ£o ÃƒÂ© variÃƒÂ¡vel: o grÃƒÂ¡fico ÃƒÂ© a fonte de verdade.
        # NÃƒÂ£o manter o ativo anterior quando o usuÃƒÂ¡rio troca o cabeÃƒÂ§alho.
        def norm(value):
            return ''.join(ch for ch in str(value or '').upper() if ch.isalnum())

        graph_norm = norm(graph_label)
        dom_aid = str(info.get('active_id')) if info.get('active_id') not in (None, '', 'None') else None
        ws_aid = str(self._selected_ws_asset) if self._selected_ws_asset not in (None, '', 'None') else None

        # Primeiro, use o ID que o feed M1 confirmou se o nome dele bate com o grÃƒÂ¡fico.
        candidates = []
        for aid in (dom_aid, ws_aid):
            if aid and aid not in candidates:
                candidates.append(aid)
        # Depois, resolva nomes conhecidos sem depender do ID inicial do config.
        for aid, known_label in BULLEX_ASSET_NAMES.items():
            if norm(known_label) == graph_norm and aid not in candidates:
                candidates.append(aid)

        selected_id = None
        for aid in candidates:
            known = BULLEX_ASSET_NAMES.get(aid)
            if known and norm(known) == graph_norm:
                selected_id = aid
                break

        # O DOM pode conservar o atributo 76 durante a troca. Para IDs sem
        # nome conhecido, sÃƒÂ³ aceite o DOM quando o WS confirmar o mesmo ID.
        if not selected_id and dom_aid and ws_aid and dom_aid == ws_aid:
            selected_id = dom_aid

        # Ativo novo: se a assinatura M1 foi recebida depois da troca do grÃƒÂ¡fico,
        # aceite o active_id mesmo sem existir ainda no mapa local.
        fresh_at = float(getattr(self, '_selected_ws_confirmed_at', 0.0) or 0.0)
        pending_since = float(getattr(self, '_pending_graph_since', 0.0) or 0.0)
        ws_is_fresh = bool(ws_aid and fresh_at and (not pending_since or fresh_at >= pending_since - 0.35))
        if not selected_id and ws_aid and ws_is_fresh:
            selected_id = ws_aid

        if selected_id:
            changed = selected_id != str(self.asset_id) or graph_label != self.asset_label
            self.asset_id = selected_id
            self.asset_label = graph_label
            BULLEX_ASSET_NAMES[selected_id] = graph_label
            self._selected_asset = {'asset_id': selected_id, 'label': graph_label, 'verified': True}
            self._pending_graph_label = None
            self._pending_graph_old_ws = None
            self._pending_graph_since = 0.0
            if changed:
                print(f'[BULLEX LIVE][ATIVO] SELECAO DO GRAFICO -> {graph_label} | active_id={selected_id}', flush=True)
            return self.asset_label

        # Ativo desconhecido: nÃƒÂ£o reutilizar o anterior. O painel acompanha o nome
        # visÃƒÂ­vel e permanece bloqueado apenas atÃƒÂ© o feed fornecer seu active_id.
        if getattr(self, '_pending_graph_label', None) != graph_label:
            self._pending_graph_since = time.time()
            print(f'[BULLEX LIVE][ATIVO] novo ativo no grafico -> {graph_label}; aguardando identificacao do feed', flush=True)
        self._pending_graph_label = graph_label
        self._pending_graph_old_ws = ws_aid
        # Registra imediatamente o nome clicado. O My_ID ÃƒÂ© criado mesmo sem
        # active_id; o ID provisÃƒÂ³rio 76/86 ÃƒÂ© associado quando disponÃƒÂ­vel.
        provisional = dom_aid or ws_aid or ''
        catalog_row=None
        try:
            catalog_row=resolve_asset_catalog(self.db, graph_label, provisional or None, confirmed_by='graph-click')
        except Exception as exc:
            print('[BULLEX LIVE][ATIVO][CATALOGO]', repr(exc), flush=True)
        # O clique no botÃƒÂ£o da Bullex opera o grÃƒÂ¡fico jÃƒÂ¡ selecionado. Se ainda
        # nÃƒÂ£o houver active_id, usa o My_ID local como referÃƒÂªncia do feed.
        operational_id=provisional or (str(catalog_row.get('our_asset_id')) if catalog_row else '')
        self.asset_id = operational_id
        self._selected_asset = {'asset_id': operational_id or None, 'label': graph_label, 'verified': bool(operational_id)}
        self.asset_label = graph_label
        return self.asset_label

    def _ensure_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS bullex_live_state(
                asset_id TEXT PRIMARY KEY,
                current_from INTEGER,
                current_to INTEGER,
                broker_at REAL,
                last_received_at TEXT,
                source TEXT
            )
        """)
        self.db.commit()

    def _upsert_candle(self, ts, o, h, l, c, source="BULLEX", asset_id=None):
        self.db.execute("""
            INSERT INTO candles(timestamp,asset_id,timeframe,open,high,low,close,source)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(timestamp,asset_id,timeframe)
            DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                source=excluded.source
        """, (int(ts), str(asset_id or self.asset_id), 60, float(o), float(h), float(l), float(c), source))


    def _handle_sent_obj(self, obj):
        """Track the M1 subscription selected by the Bullex page itself."""
        if not isinstance(obj, dict):
            return
        name=str(obj.get("name") or "")
        msg=obj.get("msg")
        if name not in ("subscribeMessage", "unsubscribeMessage") or not isinstance(msg, dict):
            return
        if str(msg.get("name") or "") != "candle-generated":
            return
        params=msg.get("params") or {}
        rf=params.get("routingFilters") or params.get("routing_filters") or {}
        try:
            aid=str(rf.get("active_id"))
            size=int(rf.get("size",0) or 0)
        except Exception:
            return
        if not aid or aid == "None" or size != 60:
            return
        if name == "unsubscribeMessage":
            self._ws_m1_subscriptions.discard(aid)
            if self._selected_ws_asset == aid:
                self._selected_ws_asset = None
            return
        self._ws_m1_subscriptions.add(aid)
        # A assinatura confirma o ID, mas NAO confirma sozinha o nome/ativo.
        # O casamento final e feito por refresh_selected_asset_label() contra o grafico.
        self._selected_ws_confirmed_at = time.time()
        if aid != self._selected_ws_asset:
            old=self._selected_ws_asset
            self._selected_ws_asset=aid
            print(f"[BULLEX LIVE][ASSINATURA] M1 selecionado pelo WS: {old} -> {aid}", flush=True)

    def _handle_obj(self, obj):
        if not isinstance(obj, dict):
            return

        name = obj.get("name")
        msg = obj.get("msg")

        # Real-time M1 candle, delivered roughly every second.
        if name == "candle-generated" and isinstance(msg, dict):
            live_asset=str(msg.get("active_id"))
            if int(msg.get("size", 0) or 0) != 60:
                return

            # The DOM often shows the selected pair but not its numeric active_id.
            # In that case self.asset_id can remain from the previous/config asset while
            # the WebSocket keeps delivering the real selected asset. Require 3
            # consecutive M1 live frames before reconciling, avoiding a stray frame.
            # Prefer the explicit M1 subscription sent by the page. Received frames may
            # contain several subscribed assets interleaved, so "3 consecutive frames"
            # alone can fail forever after a tab/asset change.
            if self._selected_ws_asset and live_asset == self._selected_ws_asset and live_asset != self.asset_id:
                old=self.asset_id
                # O WebSocket apenas candidata o ID; a confirmaÃƒÂ§ÃƒÂ£o vem do grÃƒÂ¡fico.
                self._candidate_live_asset=None
                self._candidate_live_asset_hits=0
                print(f"[BULLEX LIVE][ATIVO] active_id reconciliado pela assinatura WS: {old} -> {live_asset}", flush=True)
            elif not self._selected_ws_asset and live_asset != self.asset_id:
                if self._candidate_live_asset == live_asset:
                    self._candidate_live_asset_hits += 1
                else:
                    self._candidate_live_asset = live_asset
                    self._candidate_live_asset_hits = 1
                if self._candidate_live_asset_hits >= 3:
                    old=self.asset_id
                    # R6 P0: se o capturador iniciou depois do subscribeMessage, a
                    # assinatura enviada pela pagina nao aparece no performance log.
                    # Tres frames M1 consecutivos do mesmo active_id reconstroem a
                    # assinatura corrente. O DOM ainda precisa fornecer o par visivel
                    # antes de selected_asset_snapshot() ficar verified=True.
                    self._selected_ws_asset=live_asset
                    self._ws_m1_subscriptions={live_asset}
                    self._selected_ws_confirmed_at=time.time()
                    # Nao marca verified aqui: ainda precisa casar com o texto DENTRO do grafico.
                    self._candidate_live_asset=None
                    self._candidate_live_asset_hits=0
                    print(f"[BULLEX LIVE][ATIVO] assinatura M1 reconstruida pelos frames: {old} -> {live_asset}", flush=True)
            elif live_asset == self.asset_id:
                self._candidate_live_asset=None
                self._candidate_live_asset_hits=0

            frm = int(msg["from"])
            to = int(msg["to"])
            o = msg.get("open")
            c = msg.get("close")
            lo = msg.get("min")
            hi = msg.get("max")
            at = msg.get("at", 0)

            self._upsert_candle(frm, o, hi, lo, c, "BULLEX_WS_LIVE", live_asset)
            self.db.execute("""
                INSERT INTO bullex_live_state(
                    asset_id,current_from,current_to,broker_at,last_received_at,source
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    current_from=excluded.current_from,
                    current_to=excluded.current_to,
                    broker_at=excluded.broker_at,
                    last_received_at=excluded.last_received_at,
                    source=excluded.source
            """, (
                live_asset, frm, to,
                float(at)/1_000_000_000 if float(at) > 10_000_000_000 else float(at),
                datetime.now().isoformat(timespec="milliseconds"),
                "BULLEX_WS_LIVE"
            ))
            self.db.commit()
            self.live_events += 1
            return

        # Historical M1 candles requested by the Bullex UI.
        if name == "candles" and isinstance(msg, dict):
            arr = msg.get("candles") or []
            inserted = 0
            for x in arr:
                try:
                    self._upsert_candle(
                        int(x["from"]),
                        x["open"], x["max"], x["min"], x["close"],
                        "BULLEX_WS_HISTORY"
                    )
                    inserted += 1
                except Exception:
                    pass
            if inserted:
                self.db.commit()
                self.history_candles += inserted

    def poll(self):
        # R6 P0: primeiro consumir o performance log/WebSocket; so depois
        # atualizar o snapshot visual. Assim o active_id capturado neste ciclo
        # ja esta disponivel para a interface, sem ficar preso em AGUARDANDO ATIVO.
        try:
            logs = self.d.get_log("performance")
        except Exception as exc:
            if self.results:
                with self.results.lock, self.results.db:
                    self.results.diagnostic('RESULT_READ_FAILURE','performance',reason='PERFORMANCE_LOG_UNAVAILABLE',error=type(exc).__name__)
            return

        for e in logs:
            try:
                m = json.loads(e["message"])["message"]
                method = m.get("method","")
                p = m.get("params",{})
            except Exception:
                continue

            if method == "Network.webSocketCreated":
                self.ws_urls[p.get("requestId","")] = p.get("url","")
                continue

            if method == "Network.webSocketFrameSent":
                resp=p.get("response",{})
                if resp.get("opcode") == 1:
                    payload=resp.get("payloadData","") or ""
                    if payload.startswith("{"):
                        try:
                            obj=json.loads(payload)
                            self._observe_trade_frame(obj,False,p,e,payload)
                            self._handle_sent_obj(obj)
                        except Exception as exc:
                            print('[R6][TRADE_CAPTURE]',type(exc).__name__,flush=True)
                continue

            if method != "Network.webSocketFrameReceived":
                continue

            resp = p.get("response",{})
            if resp.get("opcode") != 1:
                continue

            payload = resp.get("payloadData","") or ""
            if not payload.startswith("{"):
                continue

            try:
                obj = json.loads(payload)
            except Exception:
                continue

            self._observe_trade_frame(obj,True,p,e,payload)
            self._handle_obj(obj)

        # WebSocket processado: agora publica o active_id/nome confirmado.
        self.refresh_selected_asset_label(force=True)

        # Release catalog/capture writes before a DIFFERENT connection reconciles.
        self.db.commit()
        if self.results:
            try:
                if getattr(self.results,'_async_enabled',False):self.results.schedule_reconcile()
                else:self.results.reconcile()
            except Exception as exc:
                self.results.db.rollback()
                print(json.dumps({'event':'RESULT_READ_FAILURE','stage':'reconcile','error':repr(exc)}),flush=True)

        if time.time() - self.last_console >= 10:
            st = self.db.execute("""
                SELECT current_from,current_to,broker_at,last_received_at
                FROM bullex_live_state WHERE asset_id=?
            """, (self.asset_id,)).fetchone()
            if st:
                print(
                    f"[BULLEX LIVE] candle={st[0]}->{st[1]} | "
                    f"broker_at={st[2]:.3f} | eventos={self.live_events} | "
                    f"hist={self.history_candles}"
                )
            else:
                print("[BULLEX LIVE] aguardando candle-generated...")
            self.last_console = time.time()

    def _observe_trade_frame(self, obj, received, params, entry, raw_payload, from_worker=False):
        if not self.results:
            return
        if not from_worker and getattr(self.results,'_async_enabled',False):
            from types import SimpleNamespace
            snapshot=SimpleNamespace(results=self.results,db=None,ws_urls={params.get('requestId'):self.ws_urls.get(params.get('requestId'),'')},_result_socket_prefix=self._result_socket_prefix)
            self.results.enqueue_frame(snapshot,obj,received,dict(params),dict(entry),raw_payload)
            return
        # A prior graph/catalog update may still own the SQLite writer lock.
        capture_db=getattr(self,'db',None)
        if capture_db is not None: capture_db.commit()
        try:
            endpoint=urlsplit(self.ws_urls.get(params.get('requestId',''),' '))
            host=(endpoint.hostname or '').lower()
            if endpoint.scheme!='wss' or not (host=='bull-ex.com' or host.endswith('.bull-ex.com')):
                with self.results.lock, self.results.db:
                    self.results.diagnostic('RESULT_READ_FAILURE','ws-origin:'+host,reason='UNVERIFIED_WS_ORIGIN',host=host)
                return
            # Chrome performance entries timestamp the event in epoch milliseconds.
            # Missing timestamps cannot prove association with an execution window.
            try: stamp=float(entry.get('timestamp') or 0)/1000
            except (TypeError,ValueError): stamp=0.0
            if stamp<=0:
                with self.results.lock, self.results.db:
                    self.results.diagnostic('RESULT_READ_FAILURE','timestamp',reason='EVENT_TIMESTAMP_MISSING',result='UNKNOWN')
            socket=self._result_socket_prefix+':'+str(params.get('requestId',''))
            self.results.register_source(socket,endpoint.scheme+'://'+host)
            self.results.observe(obj,received,socket,stamp,raw_payload=raw_payload)
        except Exception as exc:
            with self.results.lock, self.results.db:
                self.results.diagnostic('RESULT_READ_FAILURE','capture:'+type(exc).__name__,reason='TRADE_FRAME_READ_FAILED',error=str(exc))
