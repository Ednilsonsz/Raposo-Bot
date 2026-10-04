from datetime import datetime
from broker_day_r11 import SAO_PAULO

def t4_exception_active(moment=None):
    now = moment or datetime.now(SAO_PAULO)
    now = now.replace(tzinfo=SAO_PAULO) if now.tzinfo is None else now.astimezone(SAO_PAULO)
    return now.date().isoformat() == "2026-09-30" and (now.hour, now.minute) >= (18, 1)

ALLOWED_SETUPS = {"DIDI", "SR_NIVEL"}
