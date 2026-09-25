"""Rohe Gerätestatus -> kanonische Ereignisse (kind, value, safety).

Bewusst grob: es zählt "jemand war aktiv / hat etwas benutzt". Ausnahme
Rauch/Gas -> safety=True (wird ungefiltert weitergereicht).
"""

from __future__ import annotations

from dataclasses import dataclass

# kinds, die als "Person aktiv" gelten (für Fenster-/Inaktivitäts-Logik)
ACTIVITY_KINDS = {
    "ir", "motion", "door", "window", "button", "light",
    "appliance_on", "appliance_off", "presence",
}
SAFETY_KINDS = {"smoke", "gas"}


@dataclass(slots=True)
class CanonEvent:
    kind: str
    value: float | None = None
    safety: bool = False


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v, extra: set[str] = frozenset()) -> bool:
    if v is True or v == 1:
        return True
    return str(v).strip().lower() in ({"1", "true", "on", "yes"} | set(extra))


# ---------------------------------------------------------------------------
# Tuya
# ---------------------------------------------------------------------------
_TUYA_MOTION = {"pir", "presence_state", "presence", "occupancy"}
# Event-only-DPs (typisch für Tuya-IPC-Kameras): der Code kommt nur, wenn
# gerade Bewegung erkannt wurde - kein persistenter An/Aus-Wert wie bei
# normalen PIR-Meldern, daher ohne _truthy()-Prüfung behandelt (siehe
# normalize_tuya). value ist ein base64-JSON-Blob mit dem Snapshot-Pfad,
# fürs Ereignis selbst irrelevant. Live gegen eine echte Tuya-IPC-Kamera
# verifiziert (2026-09-25).
_TUYA_MOTION_EVENT = {"movement_detect_pic"}
_TUYA_DOOR = {"doorcontact_state", "door_state", "contact_state"}
_TUYA_WINDOW = {"window_state", "windowcontact_state"}
_TUYA_SMOKE = {"smoke_sensor_state", "smoke_sensor_status", "smoke"}
_TUYA_GAS = {"gas_sensor_state", "gas_sensor_status", "ch4_sensor_state", "co_state"}
_TUYA_BUTTON = {"switch1_value", "switch_value", "click"}
_TUYA_POWER = {"cur_power", "power"}
_TUYA_LIGHT = {"switch_led", "switch", "switch_1", "led_switch"}
_ALARM = {"alarm", "abnormal", "warn", "1", "true"}


def normalize_tuya(status_list: list[dict], *, on_threshold_w: float, plug_state: dict, key: str) -> list[CanonEvent]:
    out: list[CanonEvent] = []
    for item in status_list or []:
        code = str(item.get("code", "")).lower()
        value = item.get("value")
        if code in _TUYA_SMOKE and _truthy(value, _ALARM):
            out.append(CanonEvent("smoke", safety=True))
        elif code in _TUYA_GAS and _truthy(value, _ALARM):
            out.append(CanonEvent("gas", safety=True))
        elif code in _TUYA_MOTION and _truthy(value, {"pir", "presence", "motion"}):
            out.append(CanonEvent("motion"))
        elif code in _TUYA_MOTION_EVENT:
            out.append(CanonEvent("motion"))
        elif code in _TUYA_DOOR:
            out.append(CanonEvent("door"))
        elif code in _TUYA_WINDOW:
            out.append(CanonEvent("window"))
        elif code in _TUYA_BUTTON:
            out.append(CanonEvent("button"))
        elif code in _TUYA_LIGHT and _truthy(value):
            out.append(CanonEvent("light"))
        elif code in _TUYA_POWER:
            w = _f(value)
            if w is not None:
                out.extend(_plug_edges(w / 10.0, on_threshold_w, plug_state, key))
    return out


# ---------------------------------------------------------------------------
# Shelly (Gen1 + Gen2)
# ---------------------------------------------------------------------------
def normalize_shelly(status: dict, *, on_threshold_w: float, plug_state: dict, key: str) -> list[CanonEvent]:
    out: list[CanonEvent] = []
    if not isinstance(status, dict):
        return out

    # Gen2: Komponenten "sensor:0", "input:0", "switch:0", "smoke:0", ...
    for comp, data in status.items():
        if not isinstance(data, dict):
            continue
        c = comp.split(":")[0]
        if c in ("sensor", "motion") and (data.get("motion") or data.get("state") == "motion"):
            out.append(CanonEvent("motion"))
        elif c == "smoke" and data.get("alarm"):
            out.append(CanonEvent("smoke", safety=True))
        elif c == "input" and data.get("state") is True:
            out.append(CanonEvent("button"))
        elif c in ("switch", "pm1", "em1"):
            w = _f(data.get("apower", data.get("power")))
            if w is not None:
                out.extend(_plug_edges(w, on_threshold_w, plug_state, key))

    # Gen1: flaches JSON
    if "sensor" in status and isinstance(status["sensor"], dict):
        s = status["sensor"]
        if s.get("motion"):
            out.append(CanonEvent("motion"))
    if isinstance(status.get("meters"), list) and status["meters"]:
        w = _f(status["meters"][0].get("power"))
        if w is not None:
            out.extend(_plug_edges(w, on_threshold_w, plug_state, key))
    for inp in status.get("inputs", []) or []:
        if isinstance(inp, dict) and inp.get("input") == 1:
            out.append(CanonEvent("button"))
    return out


# ---------------------------------------------------------------------------
def _plug_edges(watts: float, threshold: float, plug_state: dict, key: str) -> list[CanonEvent]:
    is_on = watts >= threshold
    was_on = plug_state.get(key, False)
    plug_state[key] = is_on
    if is_on and not was_on:
        return [CanonEvent("appliance_on", value=watts)]
    if was_on and not is_on:
        return [CanonEvent("appliance_off", value=watts)]
    return []
