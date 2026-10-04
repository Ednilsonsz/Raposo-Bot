from collections import deque
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
import time


class M1FeedWatchdog:
    """Observe broker_at without changing signal or strategy processing."""

    def __init__(self, config=None):
        options = (config or {}).get('feed_watchdog', {})
        self.min_samples = max(3, int(options.get('min_samples', 5)))
        self.sample_window = max(self.min_samples, int(options.get('sample_window', 30)))
        self.cadence_multiplier = max(2.0, float(options.get('cadence_multiplier', 6.0)))
        self.min_timeout = max(3.0, float(options.get('min_timeout_seconds', 8.0)))
        self.max_timeout = max(self.min_timeout, float(options.get('max_timeout_seconds', 30.0)))
        self.max_sample_interval = max(1.0, float(options.get('max_sample_interval_seconds', 5.0)))
        self.recovery_advances = max(2, int(options.get('recovery_advances', 3)))
        self._intervals = deque(maxlen=self.sample_window)
        self.reset()

    def reset(self):
        self.asset_id = ''
        self.last_broker_at = None
        self.last_current_from = None
        self.last_advance = None
        self.frozen = False
        self.freeze_started = None
        self.freeze_started_at = None
        self.recovery_count = 0
        self._intervals.clear()

    @property
    def cadence_seconds(self):
        return float(median(self._intervals)) if self._intervals else None

    @property
    def tolerance_seconds(self):
        cadence = self.cadence_seconds
        if cadence is None:
            return self.min_timeout
        return min(self.max_timeout, max(self.min_timeout, cadence * self.cadence_multiplier))

    @property
    def learned(self):
        return len(self._intervals) >= self.min_samples

    @property
    def sample_interval_limit(self):
        cadence = self.cadence_seconds
        if cadence is None:
            return self.max_timeout
        return min(self.max_timeout, max(self.max_sample_interval, cadence * 3.0))

    @staticmethod
    def _number(value):
        try:
            result = float(value)
            return result if isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _iso(value):
        return value.astimezone().isoformat(timespec='seconds')

    def observe(self, asset_id, broker_at, current_from=None, now=None, wall_now=None):
        now = time.monotonic() if now is None else float(now)
        wall_now = datetime.now().astimezone() if wall_now is None else wall_now
        asset_id = str(asset_id or '')
        broker_value = self._number(broker_at)
        current_value = self._number(current_from)

        if asset_id and asset_id != self.asset_id:
            self.asset_id = asset_id
            self.last_broker_at = None
            self.last_current_from = None
            self.recovery_count = 0
            if not self.frozen:
                self.last_advance = None
                self._intervals.clear()

        event = None
        advanced = False
        if broker_value is not None:
            if self.last_broker_at is None:
                self.last_broker_at = broker_value
                self.last_current_from = current_value
                if self.last_advance is None:
                    self.last_advance = now
            elif broker_value > self.last_broker_at + 0.001:
                advanced = True
                interval = max(0.0, now - self.last_advance) if self.last_advance is not None else 0.0
                self.last_broker_at = broker_value
                self.last_current_from = current_value
                self.last_advance = now
                if self.frozen:
                    if interval <= self.tolerance_seconds:
                        self.recovery_count += 1
                    else:
                        self.recovery_count = 1
                    if self.recovery_count >= self.recovery_advances:
                        duration = max(0.0, now - self.freeze_started)
                        event = {
                            'kind': 'recovered',
                            'started_at': self.freeze_started_at,
                            'recovered_at': self._iso(wall_now),
                            'duration_seconds': duration,
                            'advances': self.recovery_count,
                        }
                        self.frozen = False
                        self.freeze_started = None
                        self.freeze_started_at = None
                        self.recovery_count = 0
                elif 0.0 < interval <= self.sample_interval_limit:
                    self._intervals.append(interval)

        no_advance = max(0.0, now - self.last_advance) if self.last_advance is not None else 0.0
        if (not self.frozen and self.learned and self.last_advance is not None
                and no_advance > self.tolerance_seconds):
            self.frozen = True
            self.freeze_started = self.last_advance
            started_wall = wall_now - timedelta(seconds=no_advance)
            self.freeze_started_at = self._iso(started_wall)
            self.recovery_count = 0
            event = {
                'kind': 'frozen',
                'started_at': self.freeze_started_at,
                'detected_at': self._iso(wall_now),
                'no_advance_seconds': no_advance,
            }

        return {
            'event': event,
            'frozen': self.frozen,
            'advanced': advanced,
            'learned': self.learned,
            'samples': len(self._intervals),
            'cadence_seconds': self.cadence_seconds,
            'tolerance_seconds': self.tolerance_seconds,
            'no_advance_seconds': no_advance,
            'asset_id': self.asset_id,
            'broker_at': self.last_broker_at,
            'current_from': self.last_current_from,
        }
