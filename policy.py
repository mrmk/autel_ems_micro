try:
    import utime as time
except ImportError:  # CPython tests
    import time

from tou_schedule import TouSchedule


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def _ticks_diff(new, old):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(new, old)
    return new - old


class CurrentPolicy:
    """Memory-small port of the two retained charging policies."""

    def __init__(self, cfg):
        self.configure(cfg)

    def configure(self, cfg):
        self.mode = cfg["policy_mode"]
        self.minimum = int(cfg["charger_min_amps"])
        self.maximum = int(cfg["charger_max_amps"])
        self.step = max(1, int(cfg["feedback_step_amps"]))
        self.hold_ms = max(0, int(cfg["full_green_hold_seconds"])) * 1000
        self.tou = TouSchedule(cfg)
        self.effective_max_active = False
        self.feedback_ev = None
        self.hold_since = None

    def offline_amps(self):
        return self.maximum if self.effective_max_active or self.mode == "max_power" else 0

    def effective_max(self):
        self.effective_max_active = self.mode == "max_power" or self.tou.is_active()
        return self.effective_max_active

    def _step_up(self, storage_amps, battery_soc):
        storage = float(storage_amps or 0)
        if storage < 0:
            return max(self.step, int(abs(storage)))
        return self.step

    def _step_down(self, grid_amps, storage_amps):
        excess = max(0, float(grid_amps)) + max(0, float(storage_amps or 0))
        whole = int(excess)
        if excess > whole:
            whole += 1
        return max(self.step, whole)

    def _apply_minimum_hold(self):
        value = int(self.feedback_ev or 0)
        now = _ticks_ms()
        if value >= self.minimum:
            self.hold_since = None
            return value
        if self.hold_since is None:
            self.hold_since = now
        if _ticks_diff(now, self.hold_since) < self.hold_ms:
            self.feedback_ev = self.minimum
            return self.minimum
        return value

    def allowed_amps(self, grid_amps, storage_amps=0, battery_soc=0):
        if self.effective_max():
            return self.maximum
        if grid_amps is None:
            # Embedded fail-safe: do not charge from stale or missing cloud data.
            self.feedback_ev = 0
            self.hold_since = None
            return 0

        current = int(self.feedback_ev or 0)
        grid = float(grid_amps)
        storage = float(storage_amps or 0)
        soc = float(battery_soc or 0)
        if grid <= 0 and storage == 0:
            current += self._step_up(storage, soc)
        elif storage <= -1 or (soc > 98 and storage < 1):
            current += self._step_up(storage, soc)
        elif grid >= 1 or storage > 0:
            current -= self._step_down(grid, storage)
        self.feedback_ev = max(0, min(current, self.maximum))
        return self._apply_minimum_hold()

    def status(self):
        remaining = None
        if self.hold_since is not None and self.hold_ms:
            remaining_ms = self.hold_ms - _ticks_diff(_ticks_ms(), self.hold_since)
            if remaining_ms > 0:
                remaining = (remaining_ms + 999) // 1000
        tou = self.tou.snapshot()
        return {
            "mode": self.mode,
            "effective_mode": "max_power" if tou["active_now"] else self.mode,
            "ev_setpoint_amps": self.feedback_ev,
            "full_green_hold_remaining_s": remaining,
            "tou": tou,
        }
