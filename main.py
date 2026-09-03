import gc

try:
    import uasyncio as asyncio
    import utime as time
except ImportError:  # CPython import checks
    import asyncio
    import time

import atmoce_web
import config_store
import modbus_tcp
from policy import CurrentPolicy
from web_server import WebServer


def _log_failure(service, exc, endpoint="", phase="", station=None):
    free_heap = gc.mem_free() if hasattr(gc, "mem_free") else 0
    connected = bool(station is not None and station.isconnected())
    print("ERROR", service, "phase=" + phase, "endpoint=" + endpoint,
          "wifi=" + ("connected" if connected else "disconnected"),
          "heap_free=" + str(free_heap), "exception=" + repr(exc))


def _sync_clock():
    try:
        import ntptime
        ntptime.settime()
        return True
    except (ImportError, OSError):
        return False


def _network_start(cfg):
    import network
    import machine
    try:
        import ubinascii as binascii
    except ImportError:
        import binascii

    try:
        station = network.WLAN()
    except TypeError:
        sta_id = getattr(network.WLAN, "IF_STA", network.STA_IF)
        station = network.WLAN(sta_id)
    station.active(True)
    if cfg["wifi_ssid"]:
        station.connect(cfg["wifi_ssid"], cfg["wifi_password"])
        deadline = time.time() + 15
        while not station.isconnected() and time.time() < deadline:
            time.sleep(0.25)
    if station.isconnected():
        try:
            ip = station.ipconfig("addr4")[0]
        except (AttributeError, OSError):
            ip = station.ifconfig()[0]
        return station, None, "Wi-Fi " + ip

    try:
        station.disconnect()
        station.active(False)
    except OSError:
        pass
    suffix = binascii.hexlify(machine.unique_id())[-6:].decode().upper()
    ap_name = "AutelEMS-" + suffix
    ap_password = cfg["setup_ap_password"]
    ap_id = getattr(network.WLAN, "IF_AP", getattr(network, "AP_IF", 1))
    access_point = network.WLAN(ap_id)
    # MicroPython 1.29 uses ssid/security/key. Older ESP32 builds use
    # essid/authmode/password. ESP32 firmware requires AP mode to be active
    # before its configuration can be changed (otherwise: Wifi Invalid Mode).
    access_point.active(True)
    try:
        access_point.config(ssid=ap_name, security=3, key=ap_password,
                            channel=6, hidden=False)
    except (AttributeError, TypeError, ValueError, OSError):
        access_point.config(essid=ap_name, authmode=3,
                            password=ap_password, channel=6, hidden=False)
    try:
        ip = access_point.ipconfig("addr4")[0]
    except (AttributeError, OSError):
        ip = access_point.ifconfig()[0]
    return station, access_point, "Setup AP " + ap_name + " at " + ip


class EmsApp:
    def __init__(self, cfg, wifi_label, station=None, clock_synced=False):
        self.cfg = cfg
        self.wifi_label = wifi_label
        self.policy = None
        self.charger = None
        self.meter_client = None
        self.meter = {}
        self.charger_state = {"connected": False}
        self.ev_limit = 0
        self.meter_ok = False
        self.control_ok = False
        self.last_error = None
        self.station = station
        self.clock_synced = clock_synced
        self._configure_clients()

    def _configure_clients(self):
        if self.charger is not None:
            self.charger.close()
        self.policy = CurrentPolicy(self.cfg)
        self.charger = modbus_tcp.ModbusTcpClient(
            self.cfg["charger_host"], self.cfg["charger_port"],
            self.cfg["charger_unit_id"])
        self.meter_client = atmoce_web.AtmoceWebClient(
            self.cfg["atmoce_station_id"], token=self.cfg["atmoce_token"],
            username=self.cfg["atmoce_username"],
            password=self.cfg["atmoce_password"],
            session=self.cfg["atmoce_session"],
            voltage=self.cfg["grid_voltage_v"],
            password_encoded=self.cfg["atmoce_password_encoded"])

    def update_config(self, incoming):
        old_ssid = self.cfg.get("wifi_ssid")
        old_password = self.cfg.get("wifi_password")
        merged = dict(self.cfg)
        for key in config_store.DEFAULTS:
            if key not in incoming:
                continue
            value = incoming[key]
            if key in ("wifi_password", "atmoce_token", "atmoce_password",
                       "atmoce_session", "setup_ap_password") and not str(value).strip():
                continue
            merged[key] = value
        self.cfg = config_store.save(merged)
        self._configure_clients()
        reboot = old_ssid != self.cfg["wifi_ssid"] or old_password != self.cfg["wifi_password"]
        if reboot:
            asyncio.create_task(self._reboot_later())
        return {"ok": True, "rebooting": reboot}

    async def _reboot_later(self):
        await asyncio.sleep(2)
        import machine
        machine.reset()

    def _cycle(self):
        self.last_error = None
        if not self.cfg["charger_host"]:
            raise ValueError("charger_host is not configured")

        if self.policy.effective_max():
            self.meter = {}
            self.meter_ok = True
            self.ev_limit = self.policy.allowed_amps(None)
        else:
            # TLS handshakes need a contiguous block of heap on ESP32.
            gc.collect()
            try:
                self.meter = self.meter_client.read()
                self.meter_ok = True
                self.ev_limit = self.policy.allowed_amps(
                    self.meter["grid_amps"], self.meter["storage_amps"],
                    self.meter["battery_soc"])
            except Exception as exc:
                self.meter = {}
                self.meter_ok = False
                self.ev_limit = self.policy.allowed_amps(None)
                self.last_error = "Atmoce: " + str(exc)
                _log_failure("Atmoce Web", exc,
                             "www.atmocecloud.com:443",
                             self.meter_client.last_phase, self.station)

        try:
            self.charger.push_limits(
                self.ev_limit, self.policy.offline_amps(),
                self.cfg["holding_connector_id"])
        except Exception as exc:
            self.last_error = "Charger write: " + str(exc)
            raise
        try:
            self.charger_state = self.charger.read_status(
                self.cfg["read_connector_id"])
            self.charger_state["connected"] = True
        except Exception as exc:
            self.charger_state = {"connected": False}
            if self.last_error is None:
                self.last_error = "Charger status: " + str(exc)
            _log_failure("charger Modbus status", exc,
                         "%s:%d" % (self.cfg["charger_host"],
                                    self.cfg["charger_port"]),
                         self.charger.last_phase, self.station)
        self.control_ok = True

        tou = self.policy.tou.snapshot()
        if tou["enabled"] and not tou["clock_valid"] and self.last_error is None:
            self.last_error = "TOU clock is not synchronized"

    async def control_loop(self):
        while True:
            try:
                self._cycle()
            except Exception as exc:
                self.control_ok = False
                self.charger_state = {"connected": False}
                service = ("charger Modbus write"
                           if (self.last_error or "").startswith("Charger write:")
                           else "control loop")
                self.last_error = "Control: " + str(exc)
                _log_failure(service, exc,
                             "%s:%d" % (self.cfg["charger_host"],
                                        self.cfg["charger_port"]),
                             self.charger.last_phase, self.station)
                self.charger.close()
            gc.collect()
            await asyncio.sleep(self.cfg["update_interval_s"])

    async def clock_loop(self):
        while True:
            await asyncio.sleep(21600)
            if self.station is not None and self.station.isconnected():
                self.clock_synced = _sync_clock()

    def status(self):
        free_heap = gc.mem_free() if hasattr(gc, "mem_free") else 0
        charger = dict(self.charger_state)
        charger.update({
            "endpoint": "%s:%d" % (self.cfg["charger_host"],
                                     self.cfg["charger_port"]),
            "unit_id": self.cfg["charger_unit_id"],
            "read_connector_id": self.cfg["read_connector_id"],
            "holding_connector_id": self.cfg["holding_connector_id"],
            "modbus_phase": self.charger.last_phase,
            "limits_written": self.charger.last_limits is not None,
        })
        result = {
            "wifi": self.wifi_label,
            "meter_ok": self.meter_ok,
            "control_ok": self.control_ok,
            "ev_limit_amps": self.ev_limit,
            "meter": self.meter,
            "charger": charger,
            "policy": self.policy.status(),
            "last_error": self.last_error,
            "free_heap_bytes": free_heap,
        }
        return result


async def _run(app):
    await WebServer(app).start(80)
    asyncio.create_task(app.clock_loop())
    await app.control_loop()


def run():
    cfg = config_store.load()
    station, access_point, label = _network_start(cfg)
    print(label)
    if not station.isconnected():
        print("WARNING upstream Wi-Fi disconnected; Atmoce Web and LAN charger are unreachable")
    clock_synced = station.isconnected() and _sync_clock()
    app = EmsApp(cfg, label, station, clock_synced)
    asyncio.run(_run(app))


run()
