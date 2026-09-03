try:
    import ujson as json
except ImportError:  # CPython tests
    import json

try:
    import uos as os
except ImportError:  # CPython tests
    import os


CONFIG_PATH = "config.json"

DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "setup_ap_password": "configure-me",
    "charger_host": "",
    "charger_port": 502,
    "charger_unit_id": 1,
    "read_connector_id": 1,
    "holding_connector_id": 0,
    "policy_mode": "full_green",
    "charger_min_amps": 6,
    "charger_max_amps": 32,
    "feedback_step_amps": 1,
    "full_green_hold_seconds": 60,
    "update_interval_s": 10,
    "atmoce_station_id": 0,
    "atmoce_token": "",
    "atmoce_username": "",
    "atmoce_password": "",
    "atmoce_password_encoded": False,
    "atmoce_session": "",
    "grid_voltage_v": 230,
    "tou_enabled": False,
    "tou_utc_offset_minutes": 420,
    "tou_night_start": "22:00",
    "tou_night_end": "09:00",
    "tou_weekend_saturday": True,
    "tou_weekend_sunday": True,
}

SECRET_KEYS = ("wifi_password", "atmoce_token", "atmoce_password", "atmoce_session")
PUBLIC_KEYS = tuple(k for k in DEFAULTS if k not in SECRET_KEYS)


def _read(path):
    try:
        with open(path, "r") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def validate(values):
    cfg = dict(DEFAULTS)
    cfg.update(values or {})

    mode = str(cfg.get("policy_mode", "")).strip().lower()
    if mode not in ("full_green", "max_power"):
        raise ValueError("policy_mode must be full_green or max_power")
    cfg["policy_mode"] = mode

    for key in ("charger_port", "charger_unit_id", "read_connector_id",
                "holding_connector_id",
                "charger_min_amps", "charger_max_amps", "feedback_step_amps",
                "full_green_hold_seconds", "update_interval_s",
                "atmoce_station_id", "tou_utc_offset_minutes"):
        cfg[key] = int(cfg[key])
    cfg["grid_voltage_v"] = float(cfg["grid_voltage_v"])

    if not 1 <= cfg["charger_port"] <= 65535:
        raise ValueError("charger_port must be 1..65535")
    if not 0 <= cfg["charger_unit_id"] <= 247:
        raise ValueError("charger_unit_id must be 0..247")
    if not 0 <= cfg["read_connector_id"] <= 9:
        raise ValueError("read_connector_id must be 0..9")
    if not 0 <= cfg["holding_connector_id"] <= 9:
        raise ValueError("holding_connector_id must be 0..9")
    if not 6 <= cfg["charger_min_amps"] <= 32:
        raise ValueError("charger_min_amps must be 6..32")
    if not cfg["charger_min_amps"] <= cfg["charger_max_amps"] <= 32:
        raise ValueError("charger_max_amps must be between min amps and 32")
    if not 1 <= cfg["feedback_step_amps"] <= 10:
        raise ValueError("feedback_step_amps must be 1..10")
    if not 0 <= cfg["full_green_hold_seconds"] <= 600:
        raise ValueError("full_green_hold_seconds must be 0..600")
    if not 2 <= cfg["update_interval_s"] <= 300:
        raise ValueError("update_interval_s must be 2..300")
    if not 100 <= cfg["grid_voltage_v"] <= 300:
        raise ValueError("grid_voltage_v must be 100..300")
    if not -720 <= cfg["tou_utc_offset_minutes"] <= 840:
        raise ValueError("tou_utc_offset_minutes must be -720..840")
    if len(str(cfg.get("setup_ap_password", ""))) < 8:
        raise ValueError("setup_ap_password must contain at least 8 characters")

    for key in ("wifi_ssid", "wifi_password", "setup_ap_password",
                "charger_host", "atmoce_token", "atmoce_username",
                "atmoce_password", "atmoce_session", "tou_night_start",
                "tou_night_end"):
        cfg[key] = str(cfg.get(key, "")).strip()
    from tou_schedule import parse_hhmm
    parse_hhmm(cfg["tou_night_start"])
    parse_hhmm(cfg["tou_night_end"])
    for key in ("tou_enabled", "tou_weekend_saturday", "tou_weekend_sunday",
                "atmoce_password_encoded"):
        value = cfg.get(key)
        cfg[key] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    return cfg


def load(path=CONFIG_PATH):
    return validate(_read(path))


def save(cfg, path=CONFIG_PATH):
    cfg = validate(cfg)
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(cfg, handle)
    try:
        os.rename(temporary, path)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(temporary, path)
    return cfg


def public_config(cfg):
    out = {key: cfg.get(key) for key in PUBLIC_KEYS}
    out["wifi_password_set"] = bool(cfg.get("wifi_password"))
    out["atmoce_token_set"] = bool(cfg.get("atmoce_token"))
    out["atmoce_password_set"] = bool(cfg.get("atmoce_password"))
    out["atmoce_session_set"] = bool(cfg.get("atmoce_session"))
    return out
