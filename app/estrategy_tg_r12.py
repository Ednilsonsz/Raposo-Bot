"""DEMO TG adaptation: prearm touch, entry next M1 opening; no intrabar orders."""
import statistics
def cluster(values,tol):
    # Highest-density price band, rather than farthest wick. All values are past.
    vs=sorted(values);best=[];left=0
    for right in range(len(vs)):
        while vs[right]-vs[left]>2*tol:left+=1
        part=vs[left:right+1]
        if len(part)>len(best):best=part
    return statistics.median(best)

def evaluate(rs,i,window,tol_factor,min_distance):
    past=rs[i-window:i];scale=statistics.median(r[2]-r[3] for r in past)
    if scale<=0:return []
    tol=scale*tol_factor
    sup=cluster([x for r in past for x in (r[3],min(r[1],r[4]))],tol)
    res=cluster([x for r in past for x in (r[2],max(r[1],r[4]))],tol)
    if res-sup<scale or res-sup>4*scale:return []
    # Operational definition of sideways: overlapping candles, little net drift.
    overlaps=sum(min(a[2],b[2])>=max(a[3],b[3]) for a,b in zip(past,past[1:]))/(window-1)
    if overlaps<.7 or abs(past[-1][4]-past[0][1])>.75*(res-sup):return []
    support=resistance=0
    # Each count requires next candle's closing reaction, already known at signal.
    for j,r in enumerate(past[:-1]):
        nxt=past[j+1]
        if r[3]<=sup+tol and r[2]>=sup-tol and nxt[4]>sup+tol and nxt[4]>r[4]:support+=1
        if r[3]<=res+tol and r[2]>=res-tol and nxt[4]<res-tol and nxt[4]<r[4]:resistance+=1
    both=support>=4 and resistance>=4
    out=[];r=rs[i]
    for side,level,count in [(1,sup,support),(-1,res,resistance)]:
        if not (both or count>=5):continue
        # Opening distance is known; normal approach size must reach a past-scaled distance.
        distance=(r[1]-level)*side
        if distance<scale*min_distance:continue
        if not r[3]<=level<=r[2]:continue
        out.append((side,level,support,resistance,scale))
    # OHLC cannot determine first side if both levels touched. Drop ambiguous bar.
    return out if len(out)==1 else []


def signal(rows):
    if len(rows)<21:return 0
    rs=[tuple(float(v) for v in r[:5]) for r in rows[-21:]]
    if any(rs[j][0]-rs[j-1][0]!=60 for j in range(1,len(rs))):return 0
    found=evaluate(rs,20,20,.20,1.0)
    return found[0][0] if found else 0
