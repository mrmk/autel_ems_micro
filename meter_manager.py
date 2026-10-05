"""Atmoce local-first meter selection with immediate Web fallback."""


class MeterUnavailable(Exception):
    def __init__(self, local_error=None, web_error=None):
        message = str(web_error or local_error or "Atmoce meter unavailable")
        super().__init__(message)
        self.local_error = local_error
        self.web_error = web_error


class MeterManager:
    def __init__(self, local_client, web_client):
        self.local = local_client
        self.web = web_client
        self.failure_count = 0
        self.fallback_active = False
        self.active_source = "Atmoce unavailable"
        self.last_local_error = None

    def close(self):
        if self.local is not None:
            self.local.close()
        if self.web is not None and hasattr(self.web, "close"):
            self.web.close()

    def read(self):
        self.last_local_error = None
        if self.local is not None:
            try:
                result = self.local.read()
                self.failure_count = 0
                self.fallback_active = False
                self.active_source = "Atmoce Modbus"
                return result
            except Exception as exc:
                self.failure_count += 1
                self.last_local_error = exc
                self.local.close()
                self.fallback_active = True

        try:
            result = self.web.read()
            self.active_source = ("Atmoce Web fallback" if self.local is not None
                                  else "Atmoce Web")
            result["source"] = self.active_source
            return result
        except Exception as exc:
            self.active_source = "Atmoce unavailable"
            raise MeterUnavailable(self.last_local_error, exc)

    def status(self):
        local = self.local
        return {
            "source": self.active_source,
            "endpoint": local.endpoint if local is not None else "",
            "unit_id": local.unit_id if local is not None else None,
            "modbus_phase": local.last_phase if local is not None else "disabled",
            "consecutive_failures": self.failure_count,
            "fallback_active": self.fallback_active,
        }
