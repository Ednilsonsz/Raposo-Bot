"""Broker-recorded expiry only; do not infer from configured/chart timeframe."""
import json

def expiry_label(evidence):
    try:data=json.loads(evidence) if isinstance(evidence,str) else evidence
    except (ValueError,TypeError):return '—'
    values=[]
    def visit(node):
        if isinstance(node,dict):
            for k,v in node.items():
                if k=='expiration_size':
                    try:
                        value=float(v)
                        if value>0 and value.is_integer():values.append(int(value))
                    except (TypeError,ValueError):pass
                elif isinstance(v,(dict,list)):visit(v)
        elif isinstance(node,list):
            for item in node:visit(item)
    visit(data)
    unique=set(values)
    if len(unique)!=1:return '—'
    seconds=unique.pop()
    return str(seconds//60)+'m' if seconds%60==0 else str(seconds)+'s'
