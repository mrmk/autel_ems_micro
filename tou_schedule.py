try:
    import utime as time
except ImportError:  # CPython tests
    import time


def parse_hhmm(value):
    text = str(value).strip()
    if len(text) != 5 or text[2] != ":":
        raise ValueError("TOU time must be HH:MM")
    hour = int(text[:2])
    minute = int(text[3:])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("TOU time is out of range")
    return hour * 60 + minute


class TouSchedule:
    """Fixed-offset TOU schedule; avoids the RAM cost of a timezone database."""

    def __init__(self, cfg):
        self.enabled = bool(cfg["tou_enabled"])
        self.offset_minutes = int(cfg["tou_utc_offset_minutes"])
        self.night_start = str(cfg["tou_night_start"])
        self.night_end = str(cfg["tou_night_end"])
        self.start_minute = parse_hhmm(self.night_start)
        self.end_minute = parse_hhmm(self.night_end)
        self.saturday = bool(cfg["tou_weekend_saturday"])
        self.sunday = bool(cfg["tou_weekend_sunday"])

    def local_tuple(self, now=None):
        epoch = time.time() if now is None else now
        return time.gmtime(epoch + self.offset_minutes * 60)

    def clock_valid(self, now=None):
        try:
            return self.local_tuple(now)[0] >= 2024
        except (OverflowError, OSError, ValueError):
            return False

    def active_reason(self, now=None):
        if not self.enabled or not self.clock_valid(now):
            return None
        local = self.local_tuple(now)
        weekday = local[6]
        if self.saturday and weekday == 5:
            return "weekend_saturday"
        if self.sunday and weekday == 6:
            return "weekend_sunday"
        minute = local[3] * 60 + local[4]
        start = self.start_minute
        end = self.end_minute
        if start == end:
            return "night_window"
        if start < end:
            active = start <= minute < end
        else:
            active = minute >= start or minute < end
        return "night_window" if active else None

    def is_active(self, now=None):
        return self.active_reason(now) is not None

    def snapshot(self, now=None):
        valid = self.clock_valid(now)
        local = self.local_tuple(now) if valid else None
        reason = self.active_reason(now)
        local_text = ""
        if local is not None:
            local_text = "%04d-%02d-%02d %02d:%02d" % (
                local[0], local[1], local[2], local[3], local[4])
        return {
            "enabled": self.enabled,
            "active_now": reason is not None,
            "active_reason": reason or "",
            "clock_valid": valid,
            "utc_offset_minutes": self.offset_minutes,
            "night_start": self.night_start,
            "night_end": self.night_end,
            "weekend_saturday": self.saturday,
            "weekend_sunday": self.sunday,
            "local_time": local_text,
        }

