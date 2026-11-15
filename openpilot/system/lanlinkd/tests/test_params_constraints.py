import json
from pathlib import Path

import pytest

from openpilot.system.lanlinkd.params_api import RuntimeState, delete_param, write_param
from openpilot.system.lanlinkd.settings import index_items

from .test_params_api import BOOL, FLOAT, INT, STRING, FakeStore


@pytest.fixture
def schema_items():
  return index_items(json.loads((Path(__file__).parents[1] / "settings_ui.json").read_text()))


@pytest.mark.parametrize("operation", ["write", "delete"])
@pytest.mark.parametrize("key", ["Mads", "EnforceTorqueControl", "CustomAccShortPressIncrement"])
def test_real_schema_and_ancestor_gates(operation, key, schema_items):
  store = FakeStore(initial={key: 1}, types={key: INT})
  before = dict(store.data)
  runtime = RuntimeState(False, False)
  code = (write_param(store, key, "1", runtime, schema_items, {}) if operation == "write"
          else delete_param(store, key, runtime, schema_items, {}))[0]
  assert code == 409
  assert store.data == before
  assert store.versions == 0


@pytest.mark.parametrize("operation", ["write", "delete"])
@pytest.mark.parametrize("engaged, expected", [(True, 409), (None, 409), (False, 204)])
def test_not_engaged_requires_known_disengagement(operation, engaged, expected, schema_items):
  key = "AlphaLongitudinalEnabled"
  store = FakeStore(initial={key: False}, types={key: BOOL})
  before = dict(store.data)
  runtime = RuntimeState(False, engaged)
  code = (write_param(store, key, "1", runtime, schema_items, {}) if operation == "write"
          else delete_param(store, key, runtime, schema_items, {}))[0]
  assert code == expected
  if expected != 204:
    assert store.data == before
    assert store.versions == 0
  else:
    assert store.versions == 1


@pytest.mark.parametrize("enabled, expected", [(True, 204), (False, 409)])
def test_any_rule_uses_current_parameter(enabled, expected):
  items = index_items({"items": [{"key": "Target", "enablement": [
    {"type": "any", "conditions": [{"type": "param", "key": "Gate", "equals": True}, OFFROAD]}]}]})
  store = FakeStore(initial={"Gate": enabled}, types={"Gate": BOOL, "Target": FLOAT})
  assert write_param(store, "Target", "0.2", RuntimeState(False, True), items, {})[0] == expected
  assert store.versions == (expected == 204)


OFFROAD = {"type": "offroad_only"}
DISENGAGED = {"type": "not_engaged"}
CAPABILITY = {"type": "capability", "field": "supported", "equals": True}
UNKNOWN = {"type": "new_rule"}


@pytest.mark.parametrize("rule, engaged, caps, expected", [
  ({"type": "any", "conditions": [OFFROAD, CAPABILITY]}, True, {"supported": True}, 204),
  ({"type": "any", "conditions": [OFFROAD, CAPABILITY]}, False, {"supported": False}, 409),
  ({"type": "all", "conditions": [DISENGAGED, CAPABILITY]}, False, {"supported": True}, 204),
  ({"type": "all", "conditions": [DISENGAGED, CAPABILITY]}, False, {}, 409),
  ({"type": "not", "condition": {"type": "any", "conditions": [DISENGAGED, UNKNOWN]}}, True, {}, 409),
  ({"type": "not", "condition": DISENGAGED}, None, {}, 409),
  ({"type": "not", "condition": DISENGAGED}, True, {}, 204),
  ({"type": "all", "conditions": [DISENGAGED, {"type": "param", "key": "Missing", "equals": False}]}, False, {}, 409),
  ({"type": "all", "conditions": [DISENGAGED, {"type": "param_compare", "key": "Level", "op": ">", "value": 1}]},
   False, {}, 204),
  ({"type": "not", "condition": {"type": "all", "conditions": [OFFROAD, UNKNOWN]}}, None, {}, 204),
])
@pytest.mark.parametrize("operation", ["write", "delete"])
def test_composite_rules_keep_three_valued_boolean_semantics(rule, engaged, caps, expected, operation):
  items = index_items({"items": [{"key": "Target", "enablement": [rule]}]})
  store = FakeStore(initial={"Target": False, "Level": 2}, types={"Target": BOOL, "Level": INT})
  before = dict(store.data)
  runtime = RuntimeState(False, engaged)
  code = (write_param(store, "Target", "1", runtime, items, caps) if operation == "write"
          else delete_param(store, "Target", runtime, items, caps))[0]
  assert code == expected
  if expected != 204:
    assert store.data == before
    assert store.versions == 0
  else:
    assert store.versions == 1


@pytest.mark.parametrize("offroad", [False, True])
def test_independent_ui_rules_are_not_permissions(offroad):
  items = index_items({"items": [{"key": "Target", "visibility": [OFFROAD], "enablement": [CAPABILITY, UNKNOWN]}]})
  store = FakeStore(types={"Target": BOOL})
  assert write_param(store, "Target", "1", RuntimeState(offroad, None), items, {})[0] == 204


def test_offroad_keeps_existing_writes_despite_ui_and_unknown_state():
  items = index_items({"items": [{"key": "Target", "enablement": [
    {"type": "all", "conditions": [DISENGAGED, CAPABILITY, UNKNOWN]}]}]})
  store = FakeStore(types={"Target": BOOL})
  assert write_param(store, "Target", "1", RuntimeState(True, None), items, {})[0] == 204


@pytest.mark.parametrize("container", ["sections", "sub_panels", "sub_items"])
def test_device_constraint_inherited_through_schema_containers(container):
  items = index_items({"enablement": [OFFROAD], container: [{"items": [{"key": "Target"}]}]})
  store = FakeStore(types={"Target": BOOL})
  assert write_param(store, "Target", "1", RuntimeState(False, False), items, {})[0] == 409
  assert store.versions == 0


@pytest.mark.parametrize("key, kind, value, expected", [
  ("CameraOffset", FLOAT, "nan", 400),
  ("CameraOffset", FLOAT, "inf", 400),
  ("CameraOffset", FLOAT, "-inf", 400),
  ("CameraOffset", FLOAT, "999", 400),
  ("CameraOffset", FLOAT, "-0.36", 400),
  ("CameraOffset", FLOAT, "0.35", 204),
  ("CameraOffset", FLOAT, "-0.35", 204),
  ("CameraOffset", FLOAT, "0.005", 204),
  ("LongitudinalPersonality", INT, "3", 400),
  ("LongitudinalPersonality", INT, "2", 204),
  ("LongitudinalPersonality", INT, "1" + "0" * 400, 400),
  ("TorqueControlTune", FLOAT, "", 400),
  ("TorqueControlTune", FLOAT, "1", 204),
  ("TorqueControlTune", FLOAT, "1.0", 204),
  ("TorqueControlTune", FLOAT, "2", 400),
  ("TorqueControlTune", FLOAT, "0", 204),
  ("UnlistedFloat", FLOAT, "nan", 400),
  ("UnlistedFloat", FLOAT, "", 400),
  ("CameraOffset", FLOAT, "", 400),
  ("UnlistedString", STRING, "", 204),
])
def test_typed_finite_range_and_option_boundaries(schema_items, key, kind, value, expected):
  store = FakeStore(types={key: kind})
  before = dict(store.data)
  assert write_param(store, key, value, RuntimeState(True, None), schema_items, {})[0] == expected
  if expected == 400:
    assert store.data == before
    assert store.versions == 0
  else:
    assert store.versions == 1


def test_empty_option_restores_default_by_removing_param():
  items = index_items({"items": [{"key": "Target", "options": [{"value": "", "label": "默认"}, {"value": 1.0, "label": "v1"}]}]})
  store = FakeStore(initial={"Target": 1.0}, types={"Target": FLOAT})
  assert write_param(store, "Target", "", RuntimeState(True, None), items, {})[0] == 204
  assert b"Target" not in store.data
  assert store.versions == 1


@pytest.mark.parametrize("key, expected", [("IsOffroad", 403), ("IsEngaged", 403), ("Missing", 404), ("Mads", 409)])
def test_denial_precedes_value_validation(schema_items, key, expected):
  store = FakeStore(types={"IsOffroad": BOOL, "IsEngaged": BOOL, "Mads": BOOL})
  before = dict(store.data)
  assert write_param(store, key, None, RuntimeState(False, None), schema_items, {})[0] == expected
  assert store.data == before
  assert store.versions == 0
