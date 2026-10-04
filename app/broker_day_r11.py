"""Operational day is explicitly Brasilia/Sao Paulo (UTC-03)."""
from datetime import datetime, timezone, timedelta
SAO_PAULO = timezone(timedelta(hours=-3), 'America/Sao_Paulo')
def today():
    return datetime.now(SAO_PAULO).date().isoformat()
def closed_time(stamp):
    return datetime.fromtimestamp(stamp, SAO_PAULO).isoformat()
def day_of(value):
    dt=datetime.fromisoformat(value)
    # Legacy timestamps were written in the local Sao Paulo clock without offset.
    return (dt.replace(tzinfo=SAO_PAULO) if dt.tzinfo is None else dt.astimezone(SAO_PAULO)).date().isoformat()
