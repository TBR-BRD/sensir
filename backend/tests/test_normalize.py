import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest.normalize import normalize_shelly, normalize_tuya  # noqa: E402


def test_tuya_smoke_is_safety():
    ev = normalize_tuya(
        [{"code": "smoke_sensor_state", "value": "alarm"}],
        on_threshold_w=10, plug_state={}, key="d",
    )
    assert [e.kind for e in ev] == ["smoke"]
    assert ev[0].safety is True


def test_tuya_motion():
    ev = normalize_tuya([{"code": "pir", "value": "pir"}], on_threshold_w=10, plug_state={}, key="d")
    assert [e.kind for e in ev] == ["motion"]


def test_tuya_plug_edges():
    st: dict = {}
    assert normalize_tuya([{"code": "cur_power", "value": 20}], on_threshold_w=10, plug_state=st, key="p") == []
    on = normalize_tuya([{"code": "cur_power", "value": 900}], on_threshold_w=10, plug_state=st, key="p")
    assert on and on[0].kind == "appliance_on"
    assert normalize_tuya([{"code": "cur_power", "value": 950}], on_threshold_w=10, plug_state=st, key="p") == []
    off = normalize_tuya([{"code": "cur_power", "value": 5}], on_threshold_w=10, plug_state=st, key="p")
    assert off and off[0].kind == "appliance_off"


def test_shelly_gen2_switch_power():
    st: dict = {}
    on = normalize_shelly({"switch:0": {"apower": 80.0}}, on_threshold_w=10, plug_state=st, key="s")
    assert on and on[0].kind == "appliance_on"


def test_shelly_gen2_motion_and_smoke():
    ev = normalize_shelly(
        {"sensor:0": {"motion": True}, "smoke:0": {"alarm": True}},
        on_threshold_w=10, plug_state={}, key="s",
    )
    kinds = {e.kind for e in ev}
    assert "motion" in kinds and "smoke" in kinds
    assert any(e.safety for e in ev if e.kind == "smoke")


def test_shelly_gen1_meters():
    st: dict = {}
    on = normalize_shelly({"meters": [{"power": 42.0}]}, on_threshold_w=10, plug_state=st, key="g1")
    assert on and on[0].kind == "appliance_on"
