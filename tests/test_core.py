import json
import calendar
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import atmoce_web
import atmoce_modbus
import config_store
import main
import meter_manager
import modbus_tcp
import policy
import tou_schedule


def sample_config(**updates):
    cfg = dict(config_store.DEFAULTS)
    cfg.update({
        "charger_host": "192.0.2.10",
        "atmoce_station_id": 2209,
        "atmoce_token": "token",
    })
    cfg.update(updates)
    return config_store.validate(cfg)


class ConfigTests(unittest.TestCase):
    def test_atmoce_modbus_settings_are_validated(self):
        cfg = sample_config(
            atmoce_host="192.168.1.120", atmoce_port=502,
            atmoce_unit_id=1)
        self.assertEqual(cfg["atmoce_host"], "192.168.1.120")
        with self.assertRaises(ValueError):
            config_store.validate(sample_config(atmoce_port=0))
        with self.assertRaises(ValueError):
            config_store.validate(sample_config(atmoce_unit_id=248))

    def test_read_and_holding_connector_ids_are_independent(self):
        cfg = sample_config(read_connector_id=1, holding_connector_id=0)
        self.assertEqual(cfg["read_connector_id"], 1)
        self.assertEqual(cfg["holding_connector_id"], 0)

    def test_only_retained_modes_are_accepted(self):
        self.assertEqual(config_store.validate(sample_config(policy_mode="max_power"))["policy_mode"], "max_power")
        with self.assertRaises(ValueError):
            config_store.validate(sample_config(policy_mode="green_priority"))

    def test_save_load_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            config_store.save(sample_config(
                wifi_password="wifi-secret", atmoce_password="meter-secret",
                atmoce_session="session-secret"), path)
            loaded = config_store.load(path)
            self.assertEqual(loaded["atmoce_token"], "token")
            public = config_store.public_config(loaded)
            self.assertNotIn("atmoce_token", public)
            self.assertNotIn("wifi_password", public)
            self.assertNotIn("atmoce_password", public)
            self.assertNotIn("atmoce_session", public)
            self.assertTrue(public["atmoce_token_set"])
            self.assertTrue(public["atmoce_password_set"])


class PolicyTests(unittest.TestCase):
    def test_max_power_does_not_need_meter(self):
        current = policy.CurrentPolicy(sample_config(policy_mode="max_power", charger_max_amps=24))
        self.assertEqual(current.allowed_amps(None), 24)
        self.assertEqual(current.offline_amps(), 24)

    def test_full_green_fails_closed_without_meter(self):
        current = policy.CurrentPolicy(sample_config(policy_mode="full_green"))
        current.feedback_ev = 20
        self.assertEqual(current.allowed_amps(None), 0)
        self.assertEqual(current.offline_amps(), 0)

    def test_full_green_ramps_and_holds_minimum(self):
        current = policy.CurrentPolicy(sample_config(full_green_hold_seconds=60))
        self.assertEqual(current.allowed_amps(-1, 0, 50), 6)
        self.assertEqual(current.allowed_amps(-1, 0, 50), 7)

    def test_full_green_uses_battery_discharge_step(self):
        current = policy.CurrentPolicy(sample_config(full_green_hold_seconds=0))
        current.feedback_ev = 10
        self.assertEqual(current.allowed_amps(0, -4, 50), 14)

    def test_tou_forces_online_and_offline_maximum(self):
        current = policy.CurrentPolicy(sample_config(
            policy_mode="full_green", charger_max_amps=24, tou_enabled=True,
            tou_night_start="00:00", tou_night_end="00:00"))
        self.assertEqual(current.allowed_amps(None), 24)
        self.assertEqual(current.offline_amps(), 24)


class TouTests(unittest.TestCase):
    def _epoch(self, year, month, day, hour, minute):
        return calendar.timegm((year, month, day, hour, minute, 0))

    def test_bangkok_overnight_window(self):
        schedule = tou_schedule.TouSchedule(sample_config(
            tou_enabled=True, tou_utc_offset_minutes=420,
            tou_weekend_saturday=False, tou_weekend_sunday=False))
        # Friday 16:00 UTC is Friday 23:00 in Bangkok.
        self.assertEqual(schedule.active_reason(self._epoch(2026, 8, 28, 16, 0)), "night_window")
        # Friday 02:00 UTC is Friday 09:00 in Bangkok; end is exclusive.
        self.assertIsNone(schedule.active_reason(self._epoch(2026, 8, 28, 2, 0)))

    def test_weekend_override(self):
        schedule = tou_schedule.TouSchedule(sample_config(
            tou_enabled=True, tou_utc_offset_minutes=420,
            tou_night_start="22:00", tou_night_end="09:00"))
        # 2026-08-29 is Saturday in Bangkok.
        self.assertEqual(schedule.active_reason(self._epoch(2026, 8, 29, 5, 0)), "weekend_saturday")

    def test_disabled_schedule_never_activates(self):
        schedule = tou_schedule.TouSchedule(sample_config(tou_enabled=False))
        self.assertIsNone(schedule.active_reason(self._epoch(2026, 8, 29, 5, 0)))


class AtmoceTests(unittest.TestCase):
    def test_password_is_base64_encoded_for_login_api(self):
        self.assertEqual(atmoce_web.encode_password("secret"), "c2VjcmV0")

    def test_login_extracts_data_token(self):
        client = atmoce_web.AtmoceWebClient(
            2209, username="user@example.com", password="secret")
        calls = []
        def fake_post(path, body, authorization=""):
            calls.append((path, body, authorization))
            return {"success": True, "data": {"token": "new-token"}}, 200
        client._post_json = fake_post
        self.assertEqual(client.login(), "new-token")
        self.assertEqual(calls[0][0], atmoce_web.LOGIN_PATH)
        self.assertTrue(calls[0][1]["encrypted"])
        self.assertEqual(calls[0][1]["password"], "c2VjcmV0")

    def test_preencoded_password_is_sent_unchanged(self):
        client = atmoce_web.AtmoceWebClient(
            2209, username="user@example.com", password="already-encoded",
            password_encoded=True)
        bodies = []
        client._post_json = lambda path, body, authorization="": (
            bodies.append(body) or {"data": {"token": "token"}}, 200)
        client.login()
        self.assertEqual(bodies[0]["password"], "already-encoded")

    def test_expired_token_logs_in_and_retries_detail(self):
        client = atmoce_web.AtmoceWebClient(
            2209, token="old", username="user@example.com", password="secret")
        replies = [
            ({"success": False, "code": 401, "msg": "expired"}, 200),
            ({"success": True, "data": {"gridPower": 0, "storagePower": 0}}, 200),
        ]
        logins = []
        client._detail = lambda: replies.pop(0)
        client.login = lambda: logins.append(True) or "new"
        self.assertEqual(client.read()["grid_amps"], 0)
        self.assertEqual(logins, [True])

    def test_session_cookie_is_parsed_from_response_headers(self):
        headers = b"Content-Type: application/json\r\nSet-Cookie: SESSION=abc123; Path=/; HttpOnly"
        self.assertEqual(atmoce_web._session_from_headers(headers), "abc123")

    def test_payload_preserves_grid_plus_storage_calculation(self):
        result = atmoce_web.snapshot_from_payload({
            "success": True,
            "data": {"gridPower": 543, "storagePower": 200,
                     "generationPower": 1500, "storageSoe": 29},
        }, 230)
        self.assertAlmostEqual(result["grid_amps"], 743 / 230)
        self.assertAlmostEqual(result["grid_raw_amps"], 543 / 230)
        self.assertEqual(result["grid_raw_power_w"], 543)
        self.assertAlmostEqual(result["storage_amps"], 200 / 230)
        self.assertEqual(result["battery_soc"], 29)

    def test_chunked_response_decoder(self):
        body = b'7\r\n{"ok":t\r\n4\r\nrue}\r\n0\r\n\r\n'
        self.assertEqual(json.loads(atmoce_web._dechunk(body).decode()), {"ok": True})


class FakeSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = bytearray()

    def sendall(self, value):
        self.sent.extend(value)

    def recv(self, length):
        value = self.response[:length]
        del self.response[:length]
        return bytes(value)

    def close(self):
        pass


class ModbusTests(unittest.TestCase):
    def test_read_holding_register_frame_and_decode(self):
        pdu = bytes((3, 4)) + struct.pack(">HH", 0xFFFE, 2)
        frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, 1) + pdu
        client = modbus_tcp.ModbusTcpClient("unused", unit_id=1)
        client.sock = FakeSocket(frame)
        self.assertEqual(
            client.read_holding_registers(60090, 2), [0xFFFE, 2])
        expected_pdu = bytes((3,)) + struct.pack(">HH", 60090, 2)
        expected = struct.pack(">HHHB", 1, 0, len(expected_pdu) + 1, 1) + expected_pdu
        self.assertEqual(bytes(client.sock.sent), expected)

    def test_signed_register_conversions(self):
        self.assertEqual(modbus_tcp.int16(0xFF9C), -100)
        self.assertEqual(modbus_tcp.uint32((0x1234, 0x5678)), 0x12345678)
        self.assertEqual(modbus_tcp.int32((0xFFFF, 0xFC18)), -1000)

    def test_connector_ids_select_independent_register_blocks(self):
        client = modbus_tcp.ModbusTcpClient("unused")
        writes = []
        client.write_register = lambda address, value: None
        client.write_registers = lambda address, values: writes.append(address)
        client.push_limits(7, 0, connector_id=0)
        self.assertEqual(writes, [20000])

        reads = []
        client.read_input_registers = lambda address, count: (
            reads.append(address) or [0] * count)
        client.read_status(connector_id=1)
        self.assertEqual(reads, [11000])

    def test_read_input_register_frame_and_decode(self):
        transaction = 1
        pdu = bytes((4, 4)) + struct.pack(">HH", 7, 9)
        frame = struct.pack(">HHHB", transaction, 0, len(pdu) + 1, 1) + pdu
        client = modbus_tcp.ModbusTcpClient("unused", unit_id=1)
        client.sock = FakeSocket(frame)
        self.assertEqual(client.read_input_registers(10000, 2), [7, 9])
        expected_pdu = bytes((4,)) + struct.pack(">HH", 10000, 2)
        expected = struct.pack(">HHHB", 1, 0, len(expected_pdu) + 1, 1) + expected_pdu
        self.assertEqual(bytes(client.sock.sent), expected)

    def test_uint32_words_are_big_endian(self):
        self.assertEqual(modbus_tcp._uint32_words(0x12345678), (0x1234, 0x5678))

    def test_close_forces_limits_to_be_reasserted(self):
        client = modbus_tcp.ModbusTcpClient("unused")
        client.last_limits = (1, 2, 3, 4)
        client.close()
        self.assertIsNone(client.last_limits)


class FakeRegisterClient:
    def __init__(self, values):
        self.values = values
        self.last_phase = "complete"

    def read_holding_registers(self, address, count):
        value = self.values[address]
        return list(value) if isinstance(value, tuple) else [value]

    def close(self):
        pass


class AtmoceModbusTests(unittest.TestCase):
    def test_register_map_sign_and_scaling(self):
        reader = atmoce_modbus.AtmoceModbusClient("192.0.2.20")
        reader.client = FakeRegisterClient({
            60069: (0, 2300),
            60073: (0xFFFF, 0xFC18),
            60089: 2315,
            60090: 0xFF9C,
            60095: 73,
        })
        result = reader.read()
        self.assertEqual(result["source"], "Atmoce Modbus")
        self.assertEqual(result["pv_power_w"], 2300)
        self.assertEqual(result["grid_power_w"], -1000)
        self.assertAlmostEqual(result["grid_voltage_v"], 231.5)
        self.assertAlmostEqual(result["grid_amps"], -1.0)
        self.assertEqual(result["grid_raw_amps"], result["grid_amps"])
        self.assertEqual(result["storage_amps"], 0)
        self.assertEqual(result["battery_soc"], 73)


class SequenceClient:
    def __init__(self, values, source="Atmoce Modbus"):
        self.values = list(values)
        self.source = source
        self.endpoint = "192.0.2.20:502"
        self.unit_id = 1
        self.last_phase = "complete"
        self.calls = 0

    def read(self):
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, Exception):
            self.last_phase = "tcp-connect"
            raise value
        return value

    def close(self):
        pass


class MeterManagerTests(unittest.TestCase):
    def test_failure_uses_web_same_cycle_and_retries_local_next_cycle(self):
        local_sample = {"source": "Atmoce Modbus", "grid_amps": -1}
        web_sample = {"source": "Atmoce Web", "grid_amps": 2}
        local = SequenceClient([OSError("local"), local_sample])
        web = SequenceClient([web_sample], source="Atmoce Web")
        manager = meter_manager.MeterManager(local, web)

        self.assertIs(manager.read(), web_sample)
        self.assertTrue(manager.fallback_active)
        self.assertEqual(manager.active_source, "Atmoce Web fallback")
        self.assertEqual(web_sample["source"], "Atmoce Web fallback")
        self.assertIs(manager.read(), local_sample)
        self.assertFalse(manager.fallback_active)
        self.assertEqual(manager.failure_count, 0)

    def test_both_sources_unavailable(self):
        local = SequenceClient([OSError("local")])
        web = SequenceClient([OSError("web")], source="Atmoce Web")
        manager = meter_manager.MeterManager(local, web)
        with self.assertRaises(meter_manager.MeterUnavailable) as caught:
            manager.read()
        self.assertEqual(str(caught.exception.local_error), "local")
        self.assertEqual(str(caught.exception.web_error), "web")

    def test_empty_local_host_uses_web_directly(self):
        web_sample = {"source": "Atmoce Web", "grid_amps": 2}
        web = SequenceClient([web_sample], source="Atmoce Web")
        manager = meter_manager.MeterManager(None, web)
        self.assertIs(manager.read(), web_sample)
        self.assertEqual(manager.active_source, "Atmoce Web")
        self.assertFalse(manager.fallback_active)
        self.assertEqual(manager.status()["modbus_phase"], "disabled")


class FakeCharger:
    def __init__(self):
        self.last_phase = "complete"
        self.last_limits = None
        self.writes = []

    def push_limits(self, amps, offline, connector):
        self.writes.append((amps, offline, connector))
        self.last_limits = self.writes[-1]

    def read_status(self, connector):
        return {"state": 1, "voltage_v": 230, "current_amps": 0,
                "power_w": 0}

    def close(self):
        self.last_limits = None


class NeverReadManager:
    def read(self):
        raise AssertionError("meter must be bypassed")

    def status(self):
        return {"source": "Atmoce unavailable"}

    def close(self):
        pass


class AppFallbackTests(unittest.TestCase):
    def test_modbus_failure_uses_web_then_local_recovers(self):
        cfg = sample_config(
            policy_mode="full_green", full_green_hold_seconds=0,
            atmoce_host="192.0.2.20")
        app = main.EmsApp(cfg, "Wi-Fi test")
        app.charger = FakeCharger()
        local_sample = {"source": "Atmoce Modbus", "grid_amps": -1,
                        "grid_raw_amps": -1, "storage_amps": 0,
                        "battery_soc": 50}
        web_sample = {"source": "Atmoce Web", "grid_amps": -1,
                      "grid_raw_amps": -1, "storage_amps": 0,
                      "battery_soc": 50}
        local = SequenceClient([OSError("local"), local_sample])
        web = SequenceClient([web_sample], source="Atmoce Web")
        app.meter_manager = meter_manager.MeterManager(local, web)

        app._cycle()
        self.assertEqual(app.meter_source, "Atmoce Web fallback")
        self.assertEqual(web.calls, 1)
        app._cycle()
        self.assertEqual(app.meter_source, "Atmoce Modbus")
        self.assertEqual(local.calls, 2)
        self.assertEqual(web.calls, 1)

    def test_both_meter_sources_fail_closed(self):
        cfg = sample_config(policy_mode="full_green", atmoce_host="192.0.2.20")
        app = main.EmsApp(cfg, "Wi-Fi test")
        app.charger = FakeCharger()
        app.ev_limit = 20
        app.policy.feedback_ev = 20
        local = SequenceClient([OSError("local")])
        web = SequenceClient([OSError("web")], source="Atmoce Web")
        app.meter_manager = meter_manager.MeterManager(local, web)
        app._cycle()
        self.assertEqual(app.ev_limit, 0)
        self.assertFalse(app.meter_ok)
        self.assertEqual(app.meter_source, "Atmoce unavailable")

    def test_max_mode_bypasses_both_meter_sources(self):
        app = main.EmsApp(sample_config(policy_mode="max_power"), "Wi-Fi test")
        app.charger = FakeCharger()
        app.meter_manager = NeverReadManager()
        app._cycle()
        self.assertEqual(app.ev_limit, app.cfg["charger_max_amps"])
        self.assertEqual(app.meter_source, "Atmoce bypassed")

    def test_tou_override_bypasses_both_meter_sources(self):
        app = main.EmsApp(sample_config(
            policy_mode="full_green", tou_enabled=True,
            tou_night_start="00:00", tou_night_end="00:00"), "Wi-Fi test")
        app.charger = FakeCharger()
        app.meter_manager = NeverReadManager()
        app._cycle()
        self.assertEqual(app.ev_limit, app.cfg["charger_max_amps"])
        self.assertEqual(app.meter_source, "Atmoce bypassed")


if __name__ == "__main__":
    unittest.main()
