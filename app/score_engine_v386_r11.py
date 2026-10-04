import math

def ema(vals,n):
    if not vals: return []
    a=2/(n+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

def sma(vals,n):
    return sum(vals[-n:])/n if len(vals)>=n else None

def std(vals,n):
    if len(vals)<n:return None
    x=vals[-n:]; m=sum(x)/n
    return math.sqrt(sum((v-m)**2 for v in x)/n)

def true_ranges(rows):
    out=[]
    for i,r in enumerate(rows):
        _,o,h,l,c=r
        pc=rows[i-1][4] if i else c
        out.append(max(h-l,abs(h-pc),abs(l-pc)))
    return out

def aggregate_m5(rows):
    groups={}
    for r in rows:
        k=(int(r[0])//300)*300; groups.setdefault(k,[]).append(r)
    out=[]
    for k,x in sorted(groups.items()):
        x=sorted(x); ts={int(r[0]) for r in x}
        if len(x)>=5 and all(k+i*60 in ts for i in range(5)):
            by={int(r[0]):r for r in x}; z=[by[k+i*60] for i in range(5)]
            out.append((k,z[0][1],max(r[2] for r in z),min(r[3] for r in z),z[-1][4]))
    return out

def d(r): return 1 if r[4]>r[1] else -1 if r[4]<r[1] else 0

def double_pullback(rows):
    """Duplo Pullback M1: tendencia estabelecida + 2 correcoes sequenciais.

    Regra R6:
      T1/T2 = dois candles consecutivos na direcao da tendencia;
      P1/P2 = dois candles consecutivos contra a tendencia, sem candle de
              tendencia entre eles;
      entrada = CONTINUIDADE de T1/T2, nunca a direcao do pullback.

    A versao anterior olhava somente 3 candles (T1, P1, P2). Assim, um unico
    candle isolado podia ser interpretado como tendencia e gerar entrada em
    sequencia lateral/reversao.
    """
    if len(rows) < 4:
        return 0

    t1, t2, p1, p2 = rows[-4:]
    td = d(t2)
    if td == 0 or d(t1) != td:
        return 0
    if d(p1) != -td or d(p2) != -td:
        return 0

    # Rejeita candles sem corpo util e pullback que ja recuperou/superou todo
    # o impulso dos dois candles de tendencia (sinal de reversao, nao correcao).
    def body(r):
        return abs(float(r[4]) - float(r[1]))

    trend_body = body(t1) + body(t2)
    pullback_body = body(p1) + body(p2)
    if trend_body <= 0 or body(p1) <= 0 or body(p2) <= 0:
        return 0
    if pullback_body >= trend_body:
        return 0

    return td

def _dmi8(rows):
    """DMI/ADX 8/8 simplificado para validar direcao/forca do DIDI."""
    n=8
    if len(rows)<n+2:return None
    rr=rows[-(n+1):]
    trs=true_ranges(rr)[1:]; plus=[]; minus=[]
    for i in range(1,len(rr)):
        up=rr[i][2]-rr[i-1][2]; dn=rr[i-1][3]-rr[i][3]
        plus.append(up if up>dn and up>0 else 0.0)
        minus.append(dn if dn>up and dn>0 else 0.0)
    atr=sum(trs)/n if trs else 0.0
    if not atr:return None
    pdi=100*sum(plus)/n/atr; mdi=100*sum(minus)/n/atr
    dx=100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0.0
    return {'pdi':pdi,'mdi':mdi,'adx':dx}

def didi_state(rows, zero_tol=0.0015):
    """DIDI R6 staging: 3/8/20 normalizado pela MM8 + DMI 8/8 + ponto falso.

    A MM8 e a linha zero. As linhas rapida/lenta sao desvios relativos a MM8.
    A agulhada precisa nascer perto do zero e abrir em lados opostos.
    Cruzamento contrario durante pullback com a lenta preservando o lado anterior
    e classificado como PONTO_FALSO, nao como nova agulhada.
    """
    if len(rows)<22:return {'signal':0,'kind':'NONE'}
    cl=[float(r[4]) for r in rows]
    def vals(x):
        a,b,c=sma(x,3),sma(x,8),sma(x,20)
        if None in (a,b,c) or not b:return None
        return ((a/b)-1.0,(c/b)-1.0) # fast, slow; MM8 == zero
    cur=vals(cl); prev=vals(cl[:-1])
    if cur is None or prev is None:return {'signal':0,'kind':'NONE'}
    f,s=cur; pf,ps=prev
    near_zero=min(abs(pf),abs(ps),abs(f),abs(s)) <= float(zero_tol)
    call_open=(pf<=0 and f>0 and s<0)
    put_open=(pf>=0 and f<0 and s>0)
    # Ponto falso: a rapida cruza contra, mas a lenta continua sustentando o lado
    # da tendencia anterior. Serve como bloqueio da falsa inversao.
    false_sell=(pf>=0 and f<0 and s<0)
    false_buy=(pf<=0 and f>0 and s>0)
    dmi=_dmi8(rows)
    if false_sell:return {'signal':0,'kind':'PONTO_FALSO_VENDA','fast':f,'slow':s,'dmi':dmi}
    if false_buy:return {'signal':0,'kind':'PONTO_FALSO_COMPRA','fast':f,'slow':s,'dmi':dmi}
    sig=1 if call_open else -1 if put_open else 0
    if not sig:return {'signal':0,'kind':'NONE','fast':f,'slow':s,'dmi':dmi}
    if not near_zero:return {'signal':0,'kind':'AGULHADA_LONGE_ZERO','candidate':sig,'fast':f,'slow':s,'dmi':dmi}
    if not dmi:return {'signal':0,'kind':'DMI_INDISPONIVEL','candidate':sig,'fast':f,'slow':s}
    dmi_ok=(sig==1 and dmi['pdi']>dmi['mdi']) or (sig==-1 and dmi['mdi']>dmi['pdi'])
    if not dmi_ok:return {'signal':0,'kind':'DMI_CONTRA','candidate':sig,'fast':f,'slow':s,'dmi':dmi}
    return {'signal':sig,'kind':'AGULHADA','fast':f,'slow':s,'dmi':dmi,'near_zero':near_zero}

def didi_signal(rows):
    return int(didi_state(rows).get('signal',0))

def calc(rows, direction, hist_rate=None):
    if len(rows)<30:return {'score':0,'parts':{},'direction':direction}
    cl=[r[4] for r in rows]; hi=[r[2] for r in rows]; lo=[r[3] for r in rows]
    parts={}; score=0
    # M5 trend
    m5=aggregate_m5(rows)
    if len(m5)>=21:
        mc=[r[4] for r in m5]; s9=sma(mc,9); s21=sma(mc,21)
        if (direction==1 and s9>s21) or (direction==-1 and s9<s21): parts['m5_sma']=2; score+=2
    # ADX/DI simplified Wilder 14
    n=14
    if len(rows)>=n+2:
        trs=true_ranges(rows[-(n+1):]); plus=[]; minus=[]
        rr=rows[-(n+1):]
        for i in range(1,len(rr)):
            up=rr[i][2]-rr[i-1][2]; dn=rr[i-1][3]-rr[i][3]
            plus.append(up if up>dn and up>0 else 0); minus.append(dn if dn>up and dn>0 else 0)
        atr=sum(trs[1:])/n; pdi=100*sum(plus)/n/atr if atr else 0; mdi=100*sum(minus)/n/atr if atr else 0
        dx=100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0
        if dx>=20 and ((direction==1 and pdi>mdi) or (direction==-1 and mdi>pdi)): parts['adx_di']=1; score+=1
    # TRIX 9 proxy triple EMA slope
    e1=ema(cl,9); e2=ema(e1,9); e3=ema(e2,9)
    if len(e3)>1:
        tr=(e3[-1]/e3[-2]-1) if e3[-2] else 0
        if (direction==1 and tr>0) or (direction==-1 and tr<0): parts['trix']=1; score+=1
    # stochastic 14,3 direction-friendly, not hard overbought rule
    if len(rows)>=14:
        hh=max(hi[-14:]); ll=min(lo[-14:]); k=100*(cl[-1]-ll)/(hh-ll) if hh>ll else 50
        if (direction==1 and k>=50) or (direction==-1 and k<=50): parts['stoch']=.5; score+=.5
    # ATR relative to 20-bar median proxy
    trs=true_ranges(rows)
    if len(trs)>=34:
        atr14=sum(trs[-14:])/14
        hist=[]
        for i in range(14,len(trs)): hist.append(sum(trs[i-13:i+1])/14)
        med=sorted(hist[-20:])[len(hist[-20:])//2]
        ratio=atr14/med if med else 1
        if 1.0<=ratio<=1.35: parts['atr']=1; score+=1
    # Bollinger + Fractal(5) — R5 passo 4.
    # Toque externo nao e reversao automatica: separa expansao de rejeicao.
    if len(cl)>=23:
        m=sma(cl,20); sd=std(cl,20); pm=sum(cl[-21:-1])/20; ps=std(cl[:-1],20)
        upper=m+2*sd if sd is not None else None; lower=m-2*sd if sd is not None else None
        width=4*sd if sd else 0; pwidth=4*ps if ps else 0
        widening = width > pwidth*1.03 if pwidth else False
        r=rows[-1]; rg=max(r[2]-r[3],1e-12); body=abs(r[4]-r[1])/rg; cdir=d(r)
        if upper is not None and widening and body>=0.55:
            if direction==1 and cdir==1 and r[4]>=upper:
                parts['bb_expansao']=2.0; score+=2.0
            elif direction==-1 and cdir==-1 and r[4]<=lower:
                parts['bb_expansao']=2.0; score+=2.0
            elif (direction==1 and cdir==-1 and r[4]<=lower) or (direction==-1 and cdir==1 and r[4]>=upper):
                parts['bb_contra_expansao']=-1.5; score-=1.5
        elif upper is not None and ((direction==1 and cl[-1]>=m) or (direction==-1 and cl[-1]<=m)):
            parts['bb']=0.5; score+=0.5

        # Fractal(5) confirmado: o centro e a vela -3; as duas velas seguintes ja fecharam.
        f=rows[-3]; left2,left1,right1,right2=rows[-5],rows[-4],rows[-2],rows[-1]
        upper_fractal=f[2]>left2[2] and f[2]>left1[2] and f[2]>right1[2] and f[2]>right2[2]
        lower_fractal=f[3]<left2[3] and f[3]<left1[3] and f[3]<right1[3] and f[3]<right2[3]
        idx=len(rows)-3
        if idx>=19:
            fcl=cl[:idx+1]; fm=sum(fcl[-20:])/20; fsd=std(fcl,20)
            if fsd is not None:
                fu=fm+2*fsd; fl=fm-2*fsd; tol=max(fsd*0.20,1e-12)
                if lower_fractal and f[3]<=fl+tol:
                    if direction==1: parts['fractal5_bb_inf']=1.5; score+=1.5
                    else: parts['fractal5_contra']=-1.0; score-=1.0
                if upper_fractal and f[2]>=fu-tol:
                    if direction==-1: parts['fractal5_bb_sup']=1.5; score+=1.5
                    else: parts['fractal5_contra']=-1.0; score-=1.0
    # price action wick/body support
    r=rows[-1]; rg=max(r[2]-r[3],1e-12); body=abs(r[4]-r[1])/rg
    lower=(min(r[1],r[4])-r[3])/rg; upper=(r[2]-max(r[1],r[4]))/rg
    if (direction==1 and lower>=upper and body>=.2) or (direction==-1 and upper>=lower and body>=.2): parts['pa']=1; score+=1
    if hist_rate is not None and hist_rate>=.75: parts['history']=2; score+=2
    return {'score':score,'parts':parts,'direction':direction}

# --- R6 dual setup compatibility: legacy remains untouched/available ---
def double_pullback_legacy(rows):
    if len(rows)<4:return 0
    a,b,c=rows[-3:]
    if d(a)==1 and d(b)==-1 and d(c)==-1:return 1
    if d(a)==-1 and d(b)==1 and d(c)==1:return -1
    return 0

def didi_signal_legacy(rows):
    if len(rows)<22:return 0
    cl=[r[4] for r in rows]
    m3=sma(cl,3); m8=sma(cl,8); m20=sma(cl,20)
    prev=cl[:-1]; p3=sma(prev,3); p8=sma(prev,8); p20=sma(prev,20)
    if not all(v is not None for v in [m3,m8,m20,p3,p8,p20]): return 0
    if p3<=p8 and m3>m8 and m8>=m20:return 1
    if p3>=p8 and m3<m8 and m8<=m20:return -1
    return 0

# adjusted aliases
double_pullback_new = double_pullback
didi_signal_new = didi_signal
