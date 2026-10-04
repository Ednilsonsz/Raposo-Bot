"""Original LA MAGIA arrows. ATR plot is not an entry filter in the source."""
def signal(rows):
    if len(rows)<9:return 0
    r=rows[-9:]
    if any(int(r[i][0])-int(r[i-1][0])!=60 for i in range(1,9)):return 0
    c=float(r[-1][4]);c2=float(r[-3][4]);o2=float(r[-3][1]);c4=float(r[-5][4]);c8=float(r[-9][4])
    if c>c2 and c2>o2 and c4>c8:return 1
    if c<c2 and c2<o2 and c4<c8:return -1
    return 0
