from estrategy_tg_r12 import signal as estrategy_tg_signal
from la_magia_r12 import signal as la_magia_signal
import candle_flow_observation_r12 as cf_observation
from session_exception_r12 import t4_exception_active, ALLOWED_SETUPS
from broker_day_r11 import today as broker_today
import time, math, json, sqlite3
from datetime import datetime, timezone, timedelta
from database import connect
from broker_results_r11 import ensure_schema, scoreboard
from score_engine_v386_r11 import double_pullback_legacy, didi_signal_legacy, double_pullback_new, didi_signal_new, calc
from adaptive_position_v386_r11 import AdaptivePositionRanker
from strategy_modules_v386_r11 import candle_flow, revz, sr_nivel

def after_nine_setup_priority(setup, signal_ts):
    """User priority only in 09-12,12-15,15-18,18-21,21-24 (Sao Paulo)."""
    hour = datetime.fromtimestamp(int(signal_ts), timezone(timedelta(hours=-3))).hour
    return ({"DIDI": 2, "SR_NIVEL": 1}.get(setup, 0) if hour >= 9 else 0)

def direction(o,c):
    return "G" if c > o else "R" if c < o else "D"

def didi_immediate_rows(by, current_from, broker_sec, max_second=3):
    """Return only closed candles while DIDI's immediate M1 window is open."""
    second=int(float(broker_sec))%60
    if second > int(max_second):
        return None
    return [by[t] for t in sorted(by) if t < int(current_from)]

def candle_features(r):
    _,o,h,l,c = r
    rg = max(h-l, 1e-12)
    body = abs(c-o)/rg
    upper = (h-max(o,c))/rg
    lower = (min(o,c)-l)/rg
    d = 1 if c > o else -1 if c < o else 0
    return (body, upper, lower, d)

def context_distance(ctx_a, ctx_b):
    # Uses only candles already closed at decision time.
    # Last candle is current/historical C1. No C2/C3 leakage.
    if len(ctx_a) != len(ctx_b):
        return 999.0
    total = 0.0
    for a,b in zip(ctx_a, ctx_b):
        fa, fb = candle_features(a), candle_features(b)
        # shape distance + modest direction penalty
        ds = math.sqrt(sum((fa[i]-fb[i])**2 for i in range(3)))
        dd = 0.35 if fa[3] != fb[3] else 0.0
        total += ds + dd
    return total / len(ctx_a)

def classify_doji(r, cfg):
    _,o,h,l,c = r
    rg = max(h-l, 1e-12)
    body = abs(c-o)/rg
    upper = (h-max(o,c))/rg
    lower = (min(o,c)-l)/rg
    if body <= cfg.get("body_max_ratio",0.10):
        if lower >= cfg.get("dominant_wick_min_ratio",0.60) and upper <= cfg.get("opposite_wick_max_ratio",0.20):
            return "DRAGONFLY"
        if upper >= cfg.get("dominant_wick_min_ratio",0.60) and lower <= cfg.get("opposite_wick_max_ratio",0.20):
            return "GRAVESTONE"
        return "DOJI_OTHER"
    return "NONE"

class RealtimeAnalyzer:
    def __init__(self, config, executor=None):
        self.db = connect()
        ensure_schema(self.db)
        self.cfg = config
        self.executor = executor
        self.runflag = True
        self.paused = True
        self.block_until_current_from = 0
        self.asset = str(config.get("asset_id","76"))
        self.stake = float(config.get("demo_stake",10))
        self.gale_mult = float(config.get("gale_multiplier",2.0))
        self.hcfg = config.get("history_gate",{})
        self.dcfg = config.get("doji",{})
        self.rcfg = config.get("risk",{})
        self.scfg = config.get("score_engine",{})
        self.secfg = config.get("strategy_engine",{})
        self.mcfg = config.get("experimental_modules",{})
        self.apcfg = config.get("adaptive_position",{})
        self.execution_mode = config.get("execution_mode","five_candles")
        self._last_strategy_order_ts = None
        self._last_score_candle = None
        self._last_score_eval_key = None
        self._score_calibration_cache_key = None
        self._score_calibration_cache = {}
        self.last_score = None
        self.graph_signals = []
        self._ensure_score_table()
        self.adaptive = AdaptivePositionRanker(self.db, self.apcfg)
        self.session_started = datetime.now().isoformat(timespec="seconds")
        self._feed_stale_logged = False

        st = self.live_state()
        self.session_floor = (st[0] // 300) * 300 if st else 0

        print("[LIVE] Fonte: WebSocket da própria Bullex.")
        print("[HISTORICO] Gate KNN ativado: usa contexto fechado até C1, sem olhar C2/C3 atual.")
        print(
            f"[HISTORICO] k={self.hcfg.get('k_neighbors',25)} | "
            f"amostra mínima={self.hcfg.get('min_samples',12)} | "
            f"taxa mínima WIN+GALE={self.hcfg.get('min_win_gale_rate',0.75)*100:.1f}%"
        )
        print("[RISCO] DEMO contínuo: sem desarme automático por LOSS_GALE.")
        print("[V3.85][DEMO] ranking adaptativo ACTIVE; CANDLE_FLOW/REVZ/SR_NIVEL ativos dentro das regras configuradas.")

    def _ensure_score_table(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS score_signals(
              id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, asset_id TEXT,
              signal_ts INTEGER, setup TEXT, side TEXT, score REAL, components_json TEXT,
              outcome TEXT, resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS strategy_module_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              signal_ts INTEGER NOT NULL,
              base_setup TEXT NOT NULL,
              module TEXT NOT NULL,
              module_side TEXT,
              action TEXT NOT NULL CHECK(action IN ('CONFIRMA','VETA')),
              details_json TEXT,
              UNIQUE(asset_id,signal_ts,base_setup,module,action)
            );
            CREATE TABLE IF NOT EXISTS score_ranking_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              asset_id TEXT NOT NULL,
              signal_ts INTEGER NOT NULL,
              selected_setup TEXT,
              selected_side TEXT,
              raw_score REAL,
              calibrated_win_rate REAL,
              calibration_samples INTEGER NOT NULL DEFAULT 0,
              candidates_json TEXT NOT NULL DEFAULT '[]',
              outcome TEXT,
              resolved_at TEXT,
              locked INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              UNIQUE(asset_id,signal_ts)
            );
        """)
        self.db.commit()

    def _score_calibration(self, setup, raw_score, signal_ts):
        """Bayesian-smoothed setup/score-band win rate from resolved prior M1 signals."""
        minute=int(signal_ts)
        cache_key=(self.asset,minute)
        if self._score_calibration_cache_key != cache_key:
            rows=self.db.execute("""
                WITH latest AS (
                  SELECT asset_id,signal_ts,setup,side,score,outcome,
                         ROW_NUMBER() OVER(PARTITION BY asset_id,signal_ts,setup ORDER BY id DESC) AS rn
                  FROM score_signals
                  WHERE asset_id=? AND signal_ts < ? AND outcome IN ('WIN','LOSS')
                )
                SELECT setup,CAST(score AS INTEGER),outcome FROM latest WHERE rn=1
            """,(self.asset,minute)).fetchall()
            grouped={}
            for hist_setup,band,outcome in rows:
                g=grouped.setdefault(str(hist_setup),{"all":[0,0],"bands":{}})
                g["all"][0]+=1
                g["all"][1]+=int(outcome=='WIN')
                b=g["bands"].setdefault(int(band),[0,0]); b[0]+=1; b[1]+=int(outcome=='WIN')
            self._score_calibration_cache={name:{"all":tuple(v["all"]),"bands":{k:tuple(x) for k,x in v["bands"].items()}} for name,v in grouped.items()}
            self._score_calibration_cache_key=cache_key
        stats=self._score_calibration_cache.get(str(setup),{"all":(0,0),"bands":{}})
        base_n,base_w=stats["all"]
        prior=(base_w/base_n) if base_n else 0.5
        n,w=stats["bands"].get(int(float(raw_score)),(0,0))
        strength=float(self.scfg.get("calibration_prior_strength",20))
        rate=(w+prior*strength)/(n+strength) if n+strength else prior
        return rate,n

    def _record_score_ranking(self, signal_ts, selected, candidates):
        now=datetime.now().isoformat(timespec="seconds")
        chosen=selected or {}
        self.db.execute("""
            INSERT INTO score_ranking_log(asset_id,signal_ts,selected_setup,selected_side,raw_score,
              calibrated_win_rate,calibration_samples,candidates_json,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id,signal_ts) DO UPDATE SET
              selected_setup=excluded.selected_setup,selected_side=excluded.selected_side,
              raw_score=excluded.raw_score,calibrated_win_rate=excluded.calibrated_win_rate,
              calibration_samples=excluded.calibration_samples,candidates_json=excluded.candidates_json,
              updated_at=excluded.updated_at
            WHERE score_ranking_log.locked=0
        """,(self.asset,int(signal_ts),chosen.get("setup"),chosen.get("side"),chosen.get("score"),
             chosen.get("calibrated_win_rate"),int(chosen.get("calibration_samples",0)),
             json.dumps(candidates,ensure_ascii=False),now))
        self.db.commit()

    def _record_module_event(self, signal_ts, base_setup, module, module_side, action, details):
        self.db.execute("""
            INSERT OR IGNORE INTO strategy_module_events(
              created_at,asset_id,signal_ts,base_setup,module,module_side,action,details_json
            ) VALUES(?,?,?,?,?,?,?,?)
        """, (datetime.now().isoformat(timespec="seconds"), self.asset, int(signal_ts),
              str(base_setup), str(module), str(module_side), str(action),
              json.dumps(details, ensure_ascii=False)))
        self.db.commit()

    def _record_score_signal(self, ts, setup, side, total, components):
        now=datetime.now().isoformat(timespec="seconds")
        exists=self.db.execute("SELECT id FROM score_signals WHERE asset_id=? AND signal_ts=? AND setup=? AND side=?",(self.asset,int(ts),setup,side)).fetchone()
        if not exists:
            self.db.execute("INSERT INTO score_signals(created_at,asset_id,signal_ts,setup,side,score,components_json) VALUES(?,?,?,?,?,?,?)",
                            (now,self.asset,int(ts),setup,side,float(total),json.dumps(components,ensure_ascii=False)))
            self.db.commit()
        else:
            # Keep the final pre-resolution score snapshot for future calibration.
            self.db.execute("UPDATE score_signals SET score=?,components_json=? WHERE id=? AND outcome IS NULL",
                            (float(total),json.dumps(components,ensure_ascii=False),int(exists[0])))
            self.db.commit()
        self.graph_signals.append({"ts":int(ts),"setup":setup,"side":"CALL" if side=="G" else "PUT","score":float(total)})
        self.graph_signals=self.graph_signals[-40:]

    def _resolve_score_signals(self, by, current_from):
        rows=self.db.execute("SELECT id,signal_ts,side FROM score_signals WHERE asset_id=? AND outcome IS NULL AND signal_ts < ? ORDER BY signal_ts",(self.asset,int(current_from),)).fetchall()
        now=datetime.now().isoformat(timespec="seconds"); changed=False
        for sid,ts,side in rows:
            candle=by.get(int(ts))
            if not candle: continue
            d=direction(candle[1],candle[4]); outcome="DRAW" if d not in ("G","R") else ("WIN" if d==side else "LOSS")
            self.db.execute("UPDATE score_signals SET outcome=?,resolved_at=? WHERE id=?",(outcome,now,sid)); changed=True
        ranked=self.db.execute("""
            SELECT id,signal_ts,selected_side FROM score_ranking_log
            WHERE asset_id=? AND outcome IS NULL AND locked=1 AND selected_side IS NOT NULL AND signal_ts < ?
        """,(self.asset,int(current_from))).fetchall()
        for rid,ts,side in ranked:
            candle=by.get(int(ts))
            if not candle: continue
            d=direction(candle[1],candle[4]); predicted="G" if side=="CALL" else "R" if side=="PUT" else None
            outcome="DRAW" if d not in ("G","R") else ("WIN" if d==predicted else "LOSS")
            self.db.execute("UPDATE score_ranking_log SET outcome=?,resolved_at=? WHERE id=?",(outcome,now,rid)); changed=True
        if changed: self.db.commit()

    def set_asset(self, asset_id):
        asset_id = str(asset_id)
        if asset_id == self.asset:
            return False
        old = self.asset
        self.asset = asset_id
        self._last_strategy_order_ts = None
        self._last_score_candle = None
        self._last_score_eval_key = None
        self._score_calibration_cache_key = None
        self._score_calibration_cache = {}
        self.last_score = None
        self.graph_signals = []
        st = self.live_state()
        self.session_floor = (int(st[0]) // 300) * 300 if st else 0
        print(f"[ATIVO] motor atualizado: {old} -> {asset_id}", flush=True)
        return True

    def stop(self):
        self.runflag = False

    def live_state(self):
        return self.db.execute("""
            SELECT current_from,current_to,broker_at
            FROM bullex_live_state WHERE asset_id=?
        """,(self.asset,)).fetchone()

    def broker_clock_snapshot(self):
        """Return one fresh, interpolated broker clock for analysis and dispatch."""
        row=self.db.execute("""
            SELECT current_from,current_to,broker_at,last_received_at
            FROM bullex_live_state WHERE asset_id=?
        """,(self.asset,)).fetchone()
        if not row or row[2] is None:
            return (time.time()%60.0), time.monotonic()
        age=0.0
        if row[3]:
            try:
                age=max(0.0,min((datetime.now()-datetime.fromisoformat(str(row[3]))).total_seconds(),15.0))
            except (TypeError, ValueError):
                age=0.0
        return (float(row[2])+age)%60.0, time.monotonic()

    def feed_is_fresh(self, max_age_seconds=5.0):
        try:
            row=self.db.execute("SELECT last_received_at FROM bullex_live_state WHERE asset_id=?",(str(self.asset),)).fetchone()
            if not row or not row[0]:
                return False
            t=datetime.fromisoformat(str(row[0]))
            age=(datetime.now()-t).total_seconds()
            fresh=age <= float(max_age_seconds)
            if fresh:
                self._feed_fresh_once=True
            # Antes do primeiro pacote fresco desta execucao, um timestamp herdado
            # do banco nao deve virar falso CDP BLOQUEADO. Continua sem liberar
            # estrategia: retorna False, mas o loop principal tratara como espera.
            return fresh
        except Exception:
            return False

    def m1(self):
        return self.db.execute("""
            SELECT timestamp,open,high,low,close
            FROM candles
            WHERE asset_id=? AND timeframe=60
            ORDER BY timestamp
        """,(self.asset,)).fetchall()

    def historical_outcome(self, by, s):
        c1=by.get(s); c2=by.get(s+60); c3=by.get(s+120)
        if not (c1 and c2 and c3):
            return None
        pred=direction(c1[1],c1[4])
        if pred not in ("G","R"):
            return None
        d2=direction(c2[1],c2[4])
        if d2 == pred:
            return "WIN"
        d3=direction(c3[1],c3[4])
        return "WIN_GALE" if d3 == pred else "LOSS_GALE"

    def context(self, by, s, n=5):
        arr=[]
        start = s - (n-1)*60
        for t in range(start, s+1, 60):
            r=by.get(t)
            if not r:
                return None
            arr.append(r)
        return arr

    def history_gate(self, by, cycle):
        if not self.hcfg.get("enabled",True):
            return True, {"reason":"disabled"}

        nctx=int(self.hcfg.get("context_candles",5))
        current=self.context(by,cycle,nctx)
        if not current:
            return False, {"reason":"contexto_atual_incompleto"}

        pred=direction(by[cycle][1],by[cycle][4])
        candidates=[]
        all_cycles=sorted({int(t//300)*300 for t in by if int(t//300)*300 < cycle})

        for hs in all_cycles:
            hc1=by.get(hs)
            if not hc1:
                continue
            if self.hcfg.get("same_c1_direction_only",True):
                if direction(hc1[1],hc1[4]) != pred:
                    continue
            hctx=self.context(by,hs,nctx)
            if not hctx:
                continue
            outcome=self.historical_outcome(by,hs)
            if not outcome:
                continue
            candidates.append((context_distance(current,hctx),hs,outcome))

        candidates.sort(key=lambda x:x[0])
        k=int(self.hcfg.get("k_neighbors",25))
        nearest=candidates[:k]

        if len(nearest) < int(self.hcfg.get("min_samples",12)):
            return False,{
                "reason":"amostra_insuficiente",
                "samples":len(nearest)
            }

        w=sum(1 for x in nearest if x[2]=="WIN")
        g=sum(1 for x in nearest if x[2]=="WIN_GALE")
        l=sum(1 for x in nearest if x[2]=="LOSS_GALE")
        rate=(w+g)/len(nearest)
        direct=w/len(nearest)
        avg_dist=sum(x[0] for x in nearest)/len(nearest)
        min_rate=float(self.hcfg.get("min_win_gale_rate",0.75))
        ok=rate >= min_rate

        return ok,{
            "reason":"ok" if ok else "historico_fraco",
            "samples":len(nearest),
            "win":w,
            "win_gale":g,
            "loss_gale":l,
            "rate":rate,
            "direct_rate":direct,
            "avg_distance":avg_dist,
            "best_match":nearest[0][1] if nearest else None,
            "best_distance":nearest[0][0] if nearest else None
        }

    def _consecutive_real_loss_gale(self):
        rows=self.db.execute('SELECT result FROM broker_confirmed_results ORDER BY closed_at DESC,demo_order_id DESC').fetchall()
        streak=0
        for (result,) in rows:
            if result!='LOSS_GALE': break
            streak+=1
        return streak

    def _risk_ok(self):
        # V3.13: usuário está em DEMO e pediu para não desarmar.
        # Mantemos a função por compatibilidade, mas sempre permite continuar.
        return True

    def _setup_roi_today(self, setup):
        """ROI financeiro diário: P/L confirmado dividido pelo capital apostado."""
        try:
            day=broker_today()
            row=self.db.execute("""
                 SELECT COALESCE(SUM(r.pnl_minor),0),
                        COALESCE(SUM(r.stake_minor),0),
                        COUNT(DISTINCT r.currency)
                  FROM decisions d
                  JOIN demo_orders o ON o.decision_id=d.id AND o.status='EXECUTED'
                  JOIN broker_confirmed_results r ON r.demo_order_id=o.id
                 WHERE d.c1_dir=? AND substr(r.closed_at,1,10)=?
            """,(str(setup),day)).fetchone()
            pnl_minor=int(row[0] or 0)
            stake_minor=int(row[1] or 0)
            currencies=int(row[2] or 0)
            if currencies > 1:
                print(f'[SCORE LAB][ROI] moedas diferentes no mesmo dia para {setup}; ROI indisponivel sem conversao.', flush=True)
                return 0.0
            return pnl_minor/stake_minor if stake_minor > 0 else 0.0
        except Exception as exc:
            print(f'[SCORE LAB][ROI] indisponivel para {setup}: {exc!r}', flush=True)
            return 0.0

    def _daily_candle_rank_state(self):
        """Confirmed financial performance by entry candle for the current asset/day."""
        cfg=self.scfg.get("daily_candle_rank",{})
        if not cfg.get("enabled",True):
            return {},0
        day=broker_today()
        slot_expr="COALESCE(o.candle_exec,CAST((COALESCE(o.cycle_start,d.cycle_start)%300)/60 AS INTEGER)+1)"
        try:
            rows=self.db.execute(f"""
                SELECT {slot_expr},COUNT(DISTINCT r.demo_order_id),
                       COALESCE(SUM(r.pnl_minor),0),COUNT(DISTINCT r.currency),MIN(r.currency)
                  FROM broker_confirmed_results r
                  JOIN demo_orders o ON o.id=r.demo_order_id AND o.status='EXECUTED'
                  LEFT JOIN decisions d ON d.id=o.decision_id
                 WHERE o.asset_id=?
                   AND substr(r.closed_at,1,10)=?
                   AND r.result IN ('WIN','LOSS','DRAW')
                   AND {slot_expr} BETWEEN 1 AND 5
                 GROUP BY {slot_expr}
            """,(str(self.asset),day)).fetchall()
            state={int(candle):{"operations":int(n or 0),"pnl_minor":int(pnl or 0),
                                "currencies":int(currencies or 0),"currency":str(currency or "")}
                   for candle,n,pnl,currencies,currency in rows if candle is not None}
            return state,sum(v["operations"] for v in state.values())
        except Exception as exc:
            print(f"[SCORE CANDLE][ERRO] estado financeiro diário indisponível: {exc!r}",flush=True)
            return {},0


    def score_lab(self, by, current_from):
        """R6 M1: avalia o candle EM FORMACAO, nao apenas depois da virada.

        O snapshot antigo calculava uma unica vez quando current_from mudava. Isso fazia
        o setup aparecer somente depois que o novo candle ja tinha aberto e empurrava a
        entrada um M1 para frente. Agora o score acompanha os snapshots do broker durante
        o candle atual; o strategy_engine congela a decisao perto do fechamento.
        """
        cf_observation.resolve(self.db,self.asset,current_from)
        self._resolve_score_signals(by,current_from)
        if not self.scfg.get("enabled",False):
            return
        broker_sec, observed_at=self.broker_clock_snapshot()
        # Recalcula quando o relogio/feed avanca. Evita loop de CPU sem perder mudancas M1.
        eval_key=(int(current_from), int(broker_sec))
        if self._last_score_eval_key == eval_key:
            return
        self._last_score_eval_key=eval_key
        forming=[by[t] for t in sorted(by) if t <= current_from]
        if len(forming)<35:
            return
        # DIDI/DIDI NEW are opening-candle triggers.  They must be fed from the
        # candle that has just closed and only while their immediate window is
        # open.  Other setups intentionally keep following the forming candle
        # and retain their existing end-of-candle (:57) scheduling.
        immediate_max_second=int(self.cfg.get('auto_demo',{}).get('immediate_max_second',3))
        didi_rows=didi_immediate_rows(by,current_from,broker_sec,immediate_max_second)
        # R6 dual setups preserved; V3.85 modules are evaluated independently.
        dp=double_pullback_legacy(forming)
        ds=didi_signal_legacy(didi_rows) if didi_rows is not None else 0
        dp_new=double_pullback_new(forming)
        ds_new=didi_signal_new(didi_rows) if didi_rows is not None else 0
        signals=[]
        if dp: signals.append(("DUPLO_PULLBACK",dp,3.0))
        if ds: signals.append(("DIDI",ds,3.0))
        if dp_new: signals.append(("DUPLO_PULLBACK NEW",dp_new,3.0))
        if ds_new: signals.append(("DIDI NEW",ds_new,3.0))
        lm=la_magia_signal(forming)
        if lm: signals.append(("LA_MAGIA",lm,3.0))
        tg_cfg=self.cfg.get('estrategy_tg',{})
        if tg_cfg.get('enabled') and self.cfg.get('demo_only'):
            tg=estrategy_tg_signal(forming)
            if tg: signals.append(("Estrategy_TG",tg,3.0))
        module_results={}
        module_functions=(("CANDLE_FLOW",candle_flow),("REVZ",revz),("SR_NIVEL",sr_nivel))
        for module_name,module_fn in module_functions:
            module_cfg=self.mcfg.get(module_name,{})
            if not self.mcfg.get("enabled",True) or not module_cfg.get("enabled",True):
                continue
            if module_name=="CANDLE_FLOW":
                result=module_fn(forming,module_cfg,self.cfg.get("payout"))
            else:
                result=module_fn(forming,module_cfg)
            module_results[module_name]=result
            module_side=int(result.get("signal",0) or 0)
            if module_side:
                signals.append((module_name,module_side,float(result.get("score",5.0))))
                print(f"[V3.85][MODULO][SINAL] {module_name} | {'CALL' if module_side==1 else 'PUT'} | score={float(result.get('score',0)):.1f} | {result.get('reason')}", flush=True)
        if not cf_observation.ENTRY_ENABLED and not self.cfg.get('risk_return',{}).get('replace_legacy_gates'):
            arm_second=max(40.0,min(float(self.cfg.get('auto_demo',{}).get('entry_arm_second',57.0)),45.0))
            cf_observation.observe(self.db,self.asset,current_from,broker_sec,
                module_results.get('CANDLE_FLOW'),arm_second,
                max(float(self.secfg.get('min_score',5.0)),float(self.mcfg.get('CANDLE_FLOW',{}).get('min_score',5.0))))
            signals=[item for item in signals if item[0] != 'CANDLE_FLOW']
        if t4_exception_active() and not self.cfg.get('risk_return',{}).get('replace_legacy_gates'):
            signals=[item for item in signals if item[0] in ALLOWED_SETUPS]
            module_results={name:value for name,value in module_results.items() if name in ALLOWED_SETUPS}
        if not signals:
            self.last_score={"setup":"AGUARDANDO SETUP","score":None,"side":"—","parts_text":"monitorando M1 · sem sinal","ts":current_from,"broker_sec":broker_sec,"broker_observed_at":observed_at}
            self._record_score_ranking(current_from,None,[])
            return
        best=None
        ranked_candidates=[]
        batch_candidates=[]
        candle_state,daily_operations=self._daily_candle_rank_state()
        candle_rank_cfg=self.scfg.get("daily_candle_rank",{})
        candle_share_limit=float(candle_rank_cfg.get("share_vs_others",0.30))
        candle_min_operations=max(1,int(candle_rank_cfg.get("minimum_operations",8)))
        for name,side,base in signals:
            is_module=name in module_results
            if is_module:
                result=module_results[name]
                x={"score":0.0,"parts":result.get("components",{})}
                total=float(result.get("score",base)); parts=str(result.get("reason") or "modulo")
            else:
                score_rows=didi_rows if name in ('DIDI','DIDI NEW') else forming
                x=calc(score_rows,side,None); total=base+x["score"]; parts=",".join(x["parts"].keys()) or "sem_confirmacoes"
            dire="G" if side==1 else "R"
            confirmations=[]; vetoes=[]
            if not is_module:
                for module_name,result in module_results.items():
                    module_side=int(result.get("signal",0) or 0)
                    if not module_side:
                        continue
                    action="CONFIRMA" if module_side==side else "VETA"
                    self._record_module_event(current_from,name,module_name,"CALL" if module_side==1 else "PUT",action,result)
                    (confirmations if action=="CONFIRMA" else vetoes).append(module_name)
                    print(f"[V3.85][CONFLUENCIA][{action}] {module_name} {'CALL' if module_side==1 else 'PUT'} x {name} {dire} | {result.get('reason')}", flush=True)
                total+=len(confirmations)*float(self.mcfg.get("confirmation_bonus",0.5))
                if vetoes and self.mcfg.get("veto_opposite",True):
                    self._record_score_signal(current_from,name,dire,total,{"parts":x["parts"],"base":base,"confirma":confirmations,"veta":vetoes,"blocked":"module_veto"})
                    print(f"[V3.85][MODULO][BLOQUEADO] {name} | veto={','.join(vetoes)} | direção={dire}", flush=True)
                    continue
                if confirmations: parts+=",CONFIRMA:"+"+".join(confirmations)
            print(f"[SCORE LAB] {name} | sinal={dire} | base={base:.1f} | confirm={x['score']:.1f} | TOTAL={total:.1f} | sec={broker_sec:.0f} | {parts}")
            self._record_score_signal(current_from,name,dire,total,{"parts":x["parts"],"base":base,"confirma":confirmations,"veta":vetoes})
            # Financial eligibility must be evaluated BEFORE choosing the winner.
            # Flush any score writes before the executor's separate connection is used.
            self.db.commit()
            if self.cfg.get('risk_return',{}).get('enabled'):
                from risk_return_r12 import entry_gate
                risk_ok,risk_reason=entry_gate(self.db,self.cfg,name)
                if not risk_ok:
                    print('[SCORE][RISCO][BLOQUEADO] '+risk_reason,flush=True)
                    continue
            roi=self._setup_roi_today(name)
            from daily_setup_control import setup_gate
            setup_allowed, setup_reason = setup_gate(self.db, self.cfg, name)
            if not setup_allowed:
                print(f'[SCORE SETUP][BLOQUEADO] {setup_reason}', flush=True)
                continue
            # Bloqueio por ROI suspenso para observação. O ROI financeiro segue
            # registrado e compondo o ranking; não elimina mais o candidato.
            if roi < 0.0:
                print(f"[SCORE LAB][ROI][OBSERVACAO] {name} ROI={roi*100:.1f}% | bloqueio suspenso", flush=True)
            position=self.adaptive.position_for(self.asset,name,current_from)
            position_ok,adaptive_mode,adaptive_reason,ranking=self.adaptive.evaluate(name,position)
            parts=(parts+f",POS={position}/5,{adaptive_mode}") if parts else f"POS={position}/5,{adaptive_mode}"
            print(f"[V3.85][ADAPTIVE][{'AUTORIZADO' if position_ok else 'BLOQUEADO'}] {name} | segundo={int(broker_sec)%60} | {adaptive_reason}", flush=True)
            if not position_ok and not self.cfg.get('risk_return',{}).get('adaptive_selection_only'):
                continue
            calibrated,sample_count=self._score_calibration(name,total,current_from)
            immediate=name in ('DIDI','DIDI NEW')
            entry_ts=(int(current_from)//60)*60+(0 if immediate else 60)
            entry_candle=(entry_ts%300)//60+1
            candle_stats=candle_state.get(entry_candle,{"operations":0,"pnl_minor":0,"currencies":0,"currency":""})
            candle_operations=int(candle_stats["operations"])
            other_operations=max(0,int(daily_operations)-candle_operations)
            share_vs_others=(candle_operations/other_operations) if other_operations else 0.0
            comparable_finance=int(candle_stats["currencies"])<=1
            candle_unfavorable=(
                comparable_finance
                and candle_operations>=candle_min_operations
                and other_operations>0
                and share_vs_others>=candle_share_limit
                and int(candle_stats["pnl_minor"])<0
            )
            item={"setup":name,"score":total,"roi":roi,"side":"CALL" if side==1 else "PUT","parts_text":parts,
                  "ts":current_from,"broker_sec":broker_sec,"broker_observed_at":observed_at,"adaptive_position":position,
                  "adaptive_mode":adaptive_mode,"adaptive_reason":adaptive_reason,"adaptive_ranking":ranking,
                  "calibrated_win_rate":calibrated,"calibration_samples":sample_count,
                  "entry_candle":entry_candle,"daily_candle_demoted":candle_unfavorable}
            # DIDI e gatilho de momento e nao pode desaparecer por um DP de score maior.
            # Quando presente, ele segue para o motor e e tratado como IMEDIATO.
            priority_rank=(2 if name=='DIDI NEW' and total>=7.0 else 1 if name=='DIDI' else 1 if name=='DUPLO_PULLBACK NEW' and total>=7.0 else 0)
            daily_candle_priority=0 if candle_unfavorable else 1
            time_priority=after_nine_setup_priority(name,current_from)
            if self.cfg.get('risk_return',{}).get('replace_legacy_gates'):
                daily_candle_priority=1
                time_priority=0
                priority_rank=0
                candle_unfavorable=False
            rank=(daily_candle_priority,time_priority,calibrated,priority_rank,total,roi)
            ranked_candidates.append({"setup":name,"side":item["side"],"raw_score":round(total,3),
                                      "calibrated_win_rate":round(calibrated,6),"calibration_samples":sample_count,
                                      "priority_rank":priority_rank,"time_priority":time_priority,"roi_financial":roi,
                                      "entry_candle":entry_candle,"daily_candle_demoted":candle_unfavorable,
                                      "daily_candle_operations":candle_operations,
                                      "daily_other_candle_operations":other_operations,
                                      "daily_candle_share_vs_others":round(share_vs_others,6),
                                      "daily_candle_pnl_minor":int(candle_stats["pnl_minor"]),
                                      "rank":[round(v,6) for v in rank]})
            if candle_unfavorable:
                print(f"[SCORE CANDLE][FIM DA FILA] C{entry_candle} | operacoes={candle_operations}/{other_operations} "
                      f"({share_vs_others*100:.1f}% dos demais) | P/L diario={candle_stats['currency']} "
                      f"{int(candle_stats['pnl_minor'])/100:+.2f} | "
                      f"minimo={candle_min_operations}",flush=True)
            batch_candidates.append((rank,item))
            if best is None or rank>best[0]: best=(rank,item)
        self._batch_candidates=[x[1] for x in sorted(batch_candidates,key=lambda x:x[0],reverse=True)]
        self.last_score=best[1] if best else None
        ranked_candidates.sort(key=lambda c:tuple(c["rank"]),reverse=True)
        self._record_score_ranking(current_from,self.last_score,ranked_candidates)
        self._last_score_candle=current_from

    def _resolve_strategy_decisions(self, by, current_from):
        # R6: resultado NAO e mais inferido pela cor do candle.
        # EXECUTED significa somente clique/ordem confirmada. WIN/LOSS sera
        # gravado apenas quando houver confirmacao real da corretora.
        # Tambem corrige bases antigas em que a ordem executou mas a decisao
        # ficou indevidamente em SIGNAL_PENDING.
        now=datetime.now().isoformat(timespec='seconds')
        rows=self.db.execute("""
            SELECT DISTINCT d.id
            FROM decisions d
            JOIN demo_orders o ON o.decision_id=d.id
            WHERE d.decision_mode='STRATEGY'
              AND d.status='SIGNAL_PENDING'
              AND o.status='EXECUTED'
        """).fetchall()
        for (did,) in rows:
            self.db.execute("UPDATE decisions SET status='EXECUTED',updated_at=? WHERE id=?",(now,did))
            print(f"[MOTOR][ESTADO] decision_id={did} | SIGNAL_PENDING -> EXECUTED", flush=True)
        if rows:
            self.db.commit()

    def goal_stats(self):
        real=scoreboard(self.db,broker_today())
        return real['WIN']+real['WIN_GALE'],real['LOSS']+real['LOSS_GALE']

    def strategy_engine(self, by, current_from):
        batch=self.cfg.get('multi_setup_demo',{})
        if not (self.cfg.get('demo_only') and batch.get('enabled')):
            return self._strategy_engine_one(by,current_from)
        signal_ts=(int(current_from)//60)*60
        if getattr(self,'_batch_consumed_ts',None)==signal_ts:return
        primary=self.last_score or {}
        if not primary.get('setup'):return
        immediate=primary['setup'] in ('DIDI','DIDI NEW')
        sec,observed=self.broker_clock_snapshot()
        arm=max(40.,min(45.,float(self.cfg.get('auto_demo',{}).get('entry_arm_second',57))))
        if not immediate and sec<arm:return
        target=signal_ts if immediate else signal_ts+60
        candidates=[]
        for item in getattr(self,'_batch_candidates',[]):
            name=item['setup']
            if (name in ('DIDI','DIDI NEW'))!=immediate:continue
            minimum=float(self.secfg.get('min_score',5))
            if name in ('DIDI NEW','DUPLO_PULLBACK NEW'):minimum=max(7.,minimum)
            if name in ('CANDLE_FLOW','REVZ','SR_NIVEL'):minimum=max(minimum,float(self.mcfg.get(name,{}).get('min_score',5)))
            if float(item['score'])<minimum:continue
            if name not in [x['setup'] for x in candidates]:candidates.append(dict(item))
            if len(candidates)==2:break
        if not candidates:return
        from risk_return_r12 import entry_gate
        ok,reason=entry_gate(self.db,self.cfg,candidates[0]['setup'],asset_id=self.asset,target_ts=target,requested_slots=len(candidates))
        if not ok and len(candidates)==2:
            candidates=candidates[:1]
            ok,reason=entry_gate(self.db,self.cfg,candidates[0]['setup'],asset_id=self.asset,target_ts=target)
        if not ok:
            print('[MOTOR][DUPLO][BLOQUEADO] '+reason,flush=True);return
        self._batch_consumed_ts=signal_ts
        saved=self.last_score
        try:
            for item in candidates:
                self.last_score=item
                self._last_strategy_order_ts=None
                self._strategy_engine_one(by,current_from)
        finally:
            self.last_score=saved
            self._last_strategy_order_ts=signal_ts

    def _strategy_engine_one(self, by, current_from):
        # V3.74 hotfix: a chave de uma entrada M1 e SEMPRE o inicio exato do minuto.
        # Alguns eventos da Bullex podem chegar com timestamps intermediarios; sem
        # normalizar, o mesmo sinal podia ser tratado novamente ~10 s depois.
        signal_ts=(int(current_from)//60)*60
        self._resolve_strategy_decisions(by,signal_ts)
        if getattr(self,'paused',False):
            return
        # R11: a captura visual fornece o relógio/candle usado abaixo.
        # Nao bloquear pelo timestamp de chegada nem pelo contador de eventos:
        # a janela de entrada continua sendo validada por broker_clock_snapshot()
        # e pelas regras especificas do setup.
        if int(current_from) < int(getattr(self,'block_until_current_from',0) or 0):
            return
        if not self.secfg.get('enabled',False) or self._last_strategy_order_ts==signal_ts:
            return
        score=self.last_score or {}
        setup=score.get('setup')
        side=score.get('side')
        total=score.get('score')
        if not setup or setup=='AGUARDANDO SETUP' or total is None:
            return
        if int(score.get('ts', -1)) != signal_ts:
            print(f'[MOTOR][IGNORADO] {setup} | sinal vencido ou sem timestamp | candle={signal_ts}', flush=True)
            self._last_strategy_order_ts=signal_ts
            return
        # Freeze the per-minute ranking snapshot when the motor consumes its
        # selected candidate; score_lab may continue updating unlocked minutes.
        rank_lock=self.db.execute("UPDATE score_ranking_log SET locked=1,updated_at=? WHERE asset_id=? AND signal_ts=? AND locked=0",
                                  (datetime.now().isoformat(timespec='seconds'),self.asset,int(score.get('ts'))))
        self.db.commit()
        if rank_lock.rowcount:
            print(f"[SCORE RANKING][MINUTO] {signal_ts} | escolhido={setup} {side} | score={float(total):.2f} | calibrado={float(score.get('calibrated_win_rate',0))*100:.2f}% | amostras={int(score.get('calibration_samples',0))}",flush=True)
        priority=float(self.secfg.get('historical_priority',{}).get(setup,0.0))
        effective=float(total)+priority
        min_score=float(self.secfg.get('min_score',5.0))
        # Calibrated NEW variants use the historical cut tested on the R5 base.
        if setup=='DIDI NEW': min_score=max(min_score,7.0)
        elif setup=='DUPLO_PULLBACK NEW': min_score=max(min_score,7.0)
        elif setup in ('CANDLE_FLOW','REVZ','SR_NIVEL'):
            min_score=max(min_score,float(self.mcfg.get(setup,{}).get('min_score',5.0)))
        immediate=(setup in ('DIDI','DIDI NEW'))
        if effective < min_score:
            print(f"[MOTOR][FILTRO] {setup} score={total:.1f} efetivo={effective:.1f} < {min_score:.1f}")
            # Com score dinamico, DIDI abaixo do corte pode amadurecer ainda neste candle.
            # Nao o marca como consumido antes da hora.
            if not immediate:
                live=self.live_state(); sec=(float(live[2])%60.0) if live and live[2] is not None else (time.time()%60.0)
                if sec >= 50.0: self._last_strategy_order_ts=signal_ts
            return
        pred='G' if side=='CALL' else 'R' if side=='PUT' else None
        if pred not in ('G','R'):
            return
        # Teste DEMO isolado: somente quando CANDLE_FLOW foi o setup escolhido
        # para despacho, inverte a direção final depois de todos os filtros.
        # A decisão mantém pred como direção original para comparação histórica.
        direction_original=pred
        candle_flow_inverted=(setup == 'CANDLE_FLOW')
        direction_executed=('R' if pred == 'G' else 'G') if candle_flow_inverted else pred

        # O sinal é pré-armado perto do fechamento; o executor espera o candle
        # alvo abrir (contador visual em 00:59) para enviar a ordem.
        configured_arm=float(self.cfg.get('auto_demo',{}).get('entry_arm_second',57.0))
        # Mantém a pré-armação atual; somente a janela final de clique é no candle alvo.
        edge_arm_second=min(configured_arm,45.0)
        edge_arm_second=max(40.0,edge_arm_second)
        # V3.79: usa o relogio do feed Bullex (broker_at), nao o relogio do Windows.
        if immediate:
            broker_sec=float(score.get('broker_sec', time.time()%60.0))%60.0
            broker_observed_at=score.get('broker_observed_at')
        else:
            broker_sec, broker_observed_at=self.broker_clock_snapshot()
        # DIDI e excecao: gatilho de momento, executa assim que aparece aprovado.
        # Demais setups so sao congelados no fim do candle que esta formando.
        if not immediate and broker_sec < edge_arm_second:
            return

        # V3.79: o setup pertence ao candle que esta fechando, mas a ORDEM pertence
        # ao proximo M1. A decisao e registrada no minuto-alvo para que execucao,
        # marcador e apuracao WIN/LOSS apontem para a mesma vela real da entrada.
        target_ts=signal_ts if immediate else signal_ts+60
        now=datetime.now().isoformat(timespec='seconds')
        # A tabela decisions possui UNIQUE(asset_id, cycle_start). Em loops rapidos ou
        # apos restore do banco, o mesmo candle pode ja existir. Nunca transformar
        # isso em excecao/retry infinito e nunca executar ordem duplicada.
        cur=self.db.execute("""INSERT OR IGNORE INTO decisions(created_at,updated_at,asset_id,cycle_start,c1_dir,
            similarity,matched_cycle,prediction,status,decision_mode) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (now,now,self.asset,int(target_ts),setup,effective,None,pred,'SIGNAL_PENDING','STRATEGY'))
        self.db.commit()
        if cur.rowcount == 0:
            # R5 FIX: restore/restart can leave a SIGNAL_PENDING for the same target minute.
            # Previously INSERT OR IGNORE returned zero and the motor silently abandoned
            # an otherwise approved setup. Reuse only a pending decision with no executed
            # order; duplicate EXECUTED orders remain protected in DemoExecutor.
            old=self.db.execute("""SELECT id,status FROM decisions
                WHERE asset_id=? AND cycle_start=? AND c1_dir=? AND decision_mode='STRATEGY'
                ORDER BY id DESC LIMIT 1""",(self.asset,int(target_ts),setup)).fetchone()
            if not old:
                print(f"[MOTOR][BLOQUEADO] {setup} | colisao sem decisao recuperavel | alvo={target_ts}", flush=True)
                self._last_strategy_order_ts=signal_ts
                return
            did=int(old[0])
            already=self.db.execute("SELECT 1 FROM demo_orders WHERE decision_id=? AND status='EXECUTED' LIMIT 1",(did,)).fetchone()
            if already:
                print(f"[MOTOR][IGNORADO] alvo={target_ts} ja possui ordem EXECUTED | decision_id={did}", flush=True)
                self._last_strategy_order_ts=signal_ts
                return
            self.db.execute("UPDATE decisions SET updated_at=?,c1_dir=?,similarity=?,prediction=?,status='SIGNAL_PENDING' WHERE id=?",
                            (now,setup,effective,pred,did)); self.db.commit()
            print(f"[MOTOR][RECUPERADO] {setup} | {side} | decision_id={did} | alvo={target_ts}", flush=True)
        else:
            did=cur.lastrowid
        self.adaptive.record(did,self.asset,setup,signal_ts,int(score.get('adaptive_position',1)),
                             score.get('adaptive_mode','UNKNOWN'),score.get('adaptive_reason',''))
        mode='IMEDIATO' if immediate else 'JANELA_M1'
        dispatch_second=int(broker_sec)%60
        if candle_flow_inverted:
            print(f"[CANDLE_FLOW_INVERTIDO] direcao_original={'CALL' if direction_original=='G' else 'PUT'} | direcao_executada={'CALL' if direction_executed=='G' else 'PUT'} | CANDLE_FLOW_INVERTIDO=1", flush=True)
        print(f"[MOTOR][ENTRADA] {setup} | {'CALL' if direction_executed=='G' else 'PUT'} | direcao_original={'CALL' if direction_original=='G' else 'PUT'} | score={total:.1f} + hist={priority:.1f} | {mode} | SEM GALE | segundo={dispatch_second}", flush=True)
        # Close every dispatch attempt, including exceptions and absent executors.
        error=None
        executed=False
        try:
            if self.executor is None:
                error='executor indisponivel'
            else:
                try:
                    self.executor.record_favorable_setup(did, self.asset, int(target_ts), "SIG", pred, self.stake, setup, total,
                                                          event_second=dispatch_second)
                except Exception as exc:
                    print(f"[MOTOR][SETUP_FAVORAVEL][ERRO] {type(exc).__name__}: {exc}", flush=True)
                executed=self.executor.execute(did,self.asset,int(target_ts),'SIG',direction_executed,self.stake,
                                                immediate=immediate,
                                                direction_original=direction_original,
                                                candle_flow_inverted=candle_flow_inverted,
                                                event_second=dispatch_second,
                                                event_observed_at=broker_observed_at)
        except Exception as exc:
            error=f'executor falhou: {type(exc).__name__}: {exc}'
        order=self.db.execute("SELECT status,error FROM demo_orders WHERE decision_id=? AND leg='SIG' ORDER BY id DESC LIMIT 1",(did,)).fetchone()
        order_status=str(order[0]).upper() if order and order[0] else None
        # An already recorded execution must never be overwritten by a later error.
        if order_status=='EXECUTED':
            final_status='EXECUTED'
        elif error:
            final_status='ERROR'
        elif order_status in ('SUBMITTED','BLOCKED','ERROR','SKIPPED'):
            final_status=order_status
        else:
            final_status='ERROR' if executed else 'BLOCKED'
        reason=error or (order[1] if order and order[1] else None) or ('execucao registrada' if final_status=='EXECUTED' else 'executor sem confirmacao de execucao')
        self.db.execute("UPDATE decisions SET status=?,updated_at=? WHERE id=?", (final_status,datetime.now().isoformat(timespec='seconds'),did))
        self.db.commit()
        print(f"[MOTOR][{final_status}] {setup} | decision_id={did} | {reason}", flush=True)
        self._last_strategy_order_ts=signal_ts

    def process(self):
        st=self.live_state()
        if not st:
            return

        current_from=int(st[0])
        rows=self.m1()
        by={int(x[0]):x for x in rows}
        cycle=(current_from//300)*300
        index=int((current_from-cycle)//60)+1
        self.score_lab(by,current_from)
        if self.execution_mode == "strategy_engine":
            self.strategy_engine(by,current_from)
            return

        # C2 entry
        if index==2 and cycle>=self.session_floor:
            c1=by.get(cycle)
            if c1:
                exists=self.db.execute("""
                    SELECT id FROM decisions WHERE asset_id=? AND cycle_start=?
                """,(self.asset,cycle)).fetchone()

                if not exists:
                    pred=direction(c1[1],c1[4])
                    doji=classify_doji(c1,self.dcfg)
                    gate_ok,stats=self.history_gate(by,cycle)

                    if stats.get("rate") is not None:
                        print(
                            f"[HISTORICO] ciclo={cycle} | n={stats['samples']} | "
                            f"WIN={stats['win']} | WIN_GALE={stats['win_gale']} | "
                            f"LOSS_GALE={stats['loss_gale']} | "
                            f"acerto={stats['rate']*100:.1f}% | "
                            f"direto={stats['direct_rate']*100:.1f}% | "
                            f"doji={doji}"
                        )
                    else:
                        print(f"[HISTORICO] ciclo={cycle} | {stats}")

                    now=datetime.now().isoformat(timespec="seconds")
                    cur=self.db.execute("""
                        INSERT OR IGNORE INTO decisions(
                            created_at,updated_at,asset_id,cycle_start,c1_dir,
                            similarity,matched_cycle,prediction,status,decision_mode
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,(
                        now,now,self.asset,cycle,pred,
                        None,stats.get("best_match"),pred,
                        "C2_PENDING" if gate_ok else "FILTERED","LIVE"
                    ))
                    self.db.commit()
                    if cur.rowcount == 0:
                        return
                    did=cur.lastrowid

                    if not gate_ok:
                        print(
                            f"[FILTRO] ciclo={cycle} SEM ENTRADA | "
                            f"motivo={stats.get('reason')} | doji={doji}"
                        )
                        return

                    if self.dcfg.get("enabled_as_filter",False):
                        allowed=(pred=="G" and doji=="DRAGONFLY") or (pred=="R" and doji=="GRAVESTONE")
                        if not allowed:
                            self.db.execute("""
                                UPDATE decisions SET status='FILTERED',updated_at=? WHERE id=?
                            """,(datetime.now().isoformat(timespec="seconds"),did))
                            self.db.commit()
                            print(f"[FILTRO DOJI] ciclo={cycle} SEM ENTRADA | pred={pred} | doji={doji}")
                            return

                    if not self._risk_ok():
                        return

                    print(f"[ANALISE][LIVE] ciclo={cycle} C1={pred} | HISTORICO APROVADO | entrada C2={pred}")
                    if self.executor and pred in ("G","R"):
                        try:
                            self.executor.record_favorable_setup(did, self.asset, cycle, "SIG", pred, self.stake, setup, total)
                        except Exception as exc:
                            print(f"[MOTOR][SETUP_FAVORAVEL][ERRO] {type(exc).__name__}: {exc}", flush=True)
                        self.executor.execute(did,self.asset,cycle,"SIG",pred,self.stake)

        # C3 decision
        if index==3:
            pending=self.db.execute("""
                SELECT id,prediction,status
                FROM decisions
                WHERE asset_id=? AND cycle_start=? AND decision_mode='LIVE'
            """,(self.asset,cycle)).fetchone()

            if pending and pending[2]=="C2_PENDING" and self.executor is None:
                c2=by.get(cycle+60)
                if c2:
                    d2=direction(c2[1],c2[4])
                    now=datetime.now().isoformat(timespec="seconds")
                    if d2==pending[1]:
                        self.db.execute("""
                            UPDATE decisions SET c2_dir=?,result='WIN',
                            status='FINALIZED',updated_at=? WHERE id=?
                        """,(d2,now,pending[0]))
                        self.db.commit()
                        print(f"[RESULTADO][TEORICO] ciclo={cycle} -> WIN direto")
                    else:
                        self.db.execute("""
                            UPDATE decisions SET c2_dir=?,status='C3_PENDING',
                            updated_at=? WHERE id=?
                        """,(d2,now,pending[0]))
                        self.db.commit()
                        print(f"[RESULTADO][TEORICO] ciclo={cycle} -> LOSS C2 / GALE C3")
                        if self.executor and pending[1] in ("G","R") and self._risk_ok():
                            self.executor.execute(
                                pending[0],self.asset,cycle,"C3",pending[1],
                                round(self.stake*self.gale_mult,2)
                            )

        # C3 result
        if index==4:
            pending=self.db.execute("""
                SELECT id,prediction,status
                FROM decisions
                WHERE asset_id=? AND cycle_start=? AND decision_mode='LIVE'
            """,(self.asset,cycle)).fetchone()

            if pending and pending[2]=="C3_PENDING" and self.executor is None:
                c3=by.get(cycle+120)
                if c3:
                    d3=direction(c3[1],c3[4])
                    result="WIN_GALE" if d3==pending[1] else "LOSS_GALE"
                    self.db.execute("""
                        UPDATE decisions SET c3_dir=?,result=?,
                        status='FINALIZED',updated_at=? WHERE id=?
                    """,(d3,result,datetime.now().isoformat(timespec="seconds"),pending[0]))
                    self.db.commit()
                    print(f"[RESULTADO][TEORICO] ciclo={cycle} -> {result}")

    def placares(self):
        q=dict(self.db.execute("""
            SELECT result,COUNT(*) FROM decisions
            WHERE decision_mode='LIVE' AND result IS NOT NULL
            GROUP BY result
        """).fetchall())
        w=q.get("WIN",0); g=q.get("WIN_GALE",0); l=q.get("LOSS_GALE",0)
        t=w+g+l
        if t:
            print(
                f"[PLACAR TEORICO] {t} | WIN={w} | WIN_GALE={g} | "
                f"LOSS_GALE={l} | acerto={(w+g)/t*100:.1f}%"
            )

        real=scoreboard(self.db)
        print('[PLACAR REAL CONFIRMADO]',json.dumps(real,ensure_ascii=False),flush=True)

    def run(self):
        last=0
        lock_failures=0
        last_lock_log=0.0
        while self.runflag:
            try:
                self.process()
                if time.time()-last>=30:
                    self.placares()
                    last=time.time()
                lock_failures=0
            except sqlite3.OperationalError as e:
                try: self.db.rollback()
                except Exception: pass
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    lock_failures+=1
                    delay=min(5.0,0.25*(2**min(lock_failures-1,5)))
                    now=time.monotonic()
                    if lock_failures==1 or now-last_lock_log>=10.0:
                        print(f"[ANALISADOR][DB_LOCK] tentativa={lock_failures} pausa={delay:.2f}s",flush=True)
                        last_lock_log=now
                    time.sleep(delay)
                    continue
                print("[ANALISADOR][SQLITE]",repr(e),flush=True)
                time.sleep(.5)
            except Exception as e:
                # Uma falha depois de INSERT/UPDATE pode deixar a transacao
                # aberta. Sem rollback, as iteracoes seguintes nunca recuperam.
                try: self.db.rollback()
                except Exception: pass
                print("[ANALISADOR]",repr(e),flush=True)
                time.sleep(.5)
            time.sleep(.12)
