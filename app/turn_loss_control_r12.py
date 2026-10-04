"""Automatic per-shift LOSS gate for Raposo R12.

The controller is read-only with respect to trading history.  It rebuilds the
current shift counter from broker-confirmed results, so restarting the process
inside a shift cannot reset the limit.
"""
from datetime import datetime, time as clock_time, timedelta
import json
import os
from pathlib import Path
import threading
import time

from broker_day_r11 import SAO_PAULO
from paths_r11 import RUNTIME_DIR


SHIFT_SPECS = tuple(
    (f"T{i+1}", clock_time(i*3), clock_time(((i+1)*3)%24))
    for i in range(8)
)


def shift_sql(column):
    """Local ISO timestamp classification shared by dashboard aggregations."""
    return f"('Turno ' || (CAST(substr({column},12,2) AS INTEGER) / 3 + 1))"


def normalize_shift_config(config):
    """Split legacy six-hour settings without changing their loss/free modes."""
    lc = config.setdefault('loss_control', {})
    old = lc.get('shifts') or {}
    if 'T8' not in old:
        lc['shifts'] = {
            f'T{i}': dict(old.get(f'T{(i+1)//2}') or
                         {'mode': 'FREE' if i <= 2 else 'LIMIT', 'limit': None if i <= 2 else 3})
            for i in range(1, 9)
        }
    lc['shift_scheme'] = '8x3h'
    return config


def effective_shift_settings(config, name, moment=None):
    """Dated DEMO override; the permanent shift limit remains unchanged."""
    now=moment or datetime.now(SAO_PAULO)
    now=now.replace(tzinfo=SAO_PAULO) if now.tzinfo is None else now.astimezone(SAO_PAULO)
    settings=dict((config.get('loss_control') or {}).get('shifts',{}).get(name) or {})
    if config.get('demo_only'):
        override=(config.get('loss_control') or {}).get('dated_overrides',{}).get(now.date().isoformat(),{}).get(name)
        if isinstance(override,dict):settings.update(override)
    return settings


def _local_datetime(value):
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SAO_PAULO)
    return parsed.astimezone(SAO_PAULO)


def shift_window(moment=None):
    """Eight half-open three-hour windows in America/Sao_Paulo."""
    now = moment or datetime.now(SAO_PAULO)
    now = now.replace(tzinfo=SAO_PAULO) if now.tzinfo is None else now.astimezone(SAO_PAULO)
    index = now.hour // 3
    start = datetime.combine(now.date(), clock_time(index*3), SAO_PAULO)
    return f"T{index+1}", start, start + timedelta(hours=3)


def windows_for_panel(moment=None):
    now = moment or datetime.now(SAO_PAULO)
    now = now.replace(tzinfo=SAO_PAULO) if now.tzinfo is None else now.astimezone(SAO_PAULO)
    midnight = datetime.combine(now.date(), clock_time(0), SAO_PAULO)
    return {f"T{i+1}": (midnight+timedelta(hours=i*3), midnight+timedelta(hours=(i+1)*3))
            for i in range(8)}


class TurnLossController:
    def __init__(self, db, config, cache_seconds=0.25, state_path=None):
        self.db = db
        self.config = normalize_shift_config(config if config is not None else {})
        self.cache_seconds = float(cache_seconds)
        self.state_path = Path(state_path) if state_path is not None else RUNTIME_DIR / 'turn_loss_baselines_8_shifts_r12.json'
        self._lock = threading.RLock()
        self._cached_at = 0.0
        self._cached_rows = []
        self._periods = self._load_periods()

    def _load_periods(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding='utf-8'))
            return dict(raw.get('periods') or {}) if raw.get('version') == 1 else {}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _period_key(name, start):
        return f'{name}:{start.isoformat()}'

    def _save_periods(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep recent periods only; this file is runtime state, never trading history.
        recent = dict(sorted(self._periods.items())[-16:])
        payload = {'version': 1, 'periods': recent}
        temporary = self.state_path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state_path)
        self._periods = recent

    def _baseline(self, name, start, end, moment, create=False):
        key = self._period_key(name, start)
        with self._lock:
            value = self._periods.get(key)
            if value:
                try:
                    baseline = _local_datetime(value)
                    return min(end, max(start, baseline))
                except (TypeError, ValueError, OverflowError):
                    pass
            if not create:
                return None
            baseline = min(end, max(start, moment))
            self._periods[key] = baseline.isoformat()
            self._save_periods()
            return baseline

    def _settings(self, name, moment=None):
        raw = effective_shift_settings(self.config, name, moment)
        mode = str(raw.get("mode") or "UNSET").upper()
        limit = raw.get("limit")
        try:
            limit = int(limit) if limit is not None and str(limit).strip() else None
        except (TypeError, ValueError):
            limit = None
        if mode == "FREE":
            return "FREE", None
        if mode == "LIMIT" and limit is not None and limit > 0:
            return "LIMIT", limit
        return "UNSET", None

    def _loss_times(self):
        now = time.monotonic()
        with self._lock:
            if now - self._cached_at <= self.cache_seconds:
                return list(self._cached_rows)
            rows = self.db.execute("""SELECT closed_at FROM broker_confirmed_results
                                      WHERE result IN ('LOSS','LOSS_GALE')""").fetchall()
            parsed = []
            for row in rows:
                try:
                    parsed.append(_local_datetime(row[0]))
                except (TypeError, ValueError, OverflowError):
                    continue
            self._cached_rows = parsed
            self._cached_at = now
            return list(parsed)

    def losses_between(self, start, end):
        return sum(1 for closed_at in self._loss_times() if start <= closed_at < end)

    def snapshot(self, moment=None):
        now = moment or datetime.now(SAO_PAULO)
        if now.tzinfo is None:
            now = now.replace(tzinfo=SAO_PAULO)
        else:
            now = now.astimezone(SAO_PAULO)
        current, current_start, current_end = shift_window(now)
        panel_windows = windows_for_panel(now)
        rows = []
        for name, _, _ in SHIFT_SPECS:
            start, end = panel_windows[name]
            mode, limit = self._settings(name, now)
            baseline = (start if self.config.get('loss_control', {}).get('count_from_shift_start')
                        else self._baseline(name, start, end, now, create=name == current))
            losses = self.losses_between(baseline, end) if baseline is not None else 0
            stopped = bool(name == current and mode == "LIMIT" and losses >= limit)
            unconfigured = bool(name == current and mode == "UNSET")
            if mode == "FREE":
                label = f"{name} — LIVRE"
            elif mode == "LIMIT":
                label = (f"{name} — STOP LOSS ATINGIDO {losses}/{limit}" if stopped
                         else f"{name} — LOSS {losses}/{limit}")
            else:
                label = f"{name} — LIMITE NÃO CONFIGURADO"
            rows.append({"name": name, "mode": mode, "limit": limit, "losses": losses,
                         "baseline": baseline.isoformat() if baseline else None,
                         "current": name == current, "stopped": stopped,
                         "unconfigured": unconfigured, "label": label})
        active = next(item for item in rows if item["current"])
        return {"current": current, "start": current_start.isoformat(), "end": current_end.isoformat(),
                "allowed": not active["stopped"] and not active["unconfigured"],
                "stopped": active["stopped"], "unconfigured": active["unconfigured"],
                "losses": active["losses"], "limit": active["limit"], "shifts": rows}

    def allows_new_entries(self, moment=None):
        state = self.snapshot(moment)
        from risk_return_r12 import daily_gate
        daily_ok, daily_reason = daily_gate(self.db, self.config, moment)
        state['daily_reason'] = daily_reason
        if not daily_ok:
            state['allowed'] = False
            state['reason'] = daily_reason
            return False, state
        if state["allowed"]:
            return True, state
        reason = (f"STOP LOSS ATINGIDO {state['current']} {state['losses']}/{state['limit']}"
                  if state["stopped"] else f"LIMITE LOSS NÃO CONFIGURADO {state['current']}")
        state["reason"] = reason
        return False, state
