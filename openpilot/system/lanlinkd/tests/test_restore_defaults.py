import pytest

from openpilot.common.params import Params
from openpilot.system.lanlinkd.params_api import RESTORE_DEFAULT_KEYS, VERSION_KEY, RuntimeState, restore_defaults


def _changed(value):
  if isinstance(value, bool):
    return not value
  if isinstance(value, float):
    return value + 0.5
  if isinstance(value, dict):
    return {"name": "manual"}
  raise AssertionError(f"unexpected default type {type(value)}")


@pytest.mark.parametrize("offroad, expected", [(True, 204), (False, 409)])
def test_restore_defaults_overwrites_user_values_only_offroad(tmp_path, offroad, expected):
  store = Params(str(tmp_path))
  user = {key: _changed(store.get_default_value(key)) for key in RESTORE_DEFAULT_KEYS}
  for key, value in user.items():
    store.put(key, value, block=True)
  store.put_bool("ExperimentalMode", True, block=True)

  assert restore_defaults(store, RuntimeState(offroad, None))[0] == expected

  for key in RESTORE_DEFAULT_KEYS:
    assert store.get(key) == (store.get_default_value(key) if expected == 204 else user[key]), key
  assert store.get_bool("ExperimentalMode")
  assert store.get(VERSION_KEY) == (1 if expected == 204 else None)
