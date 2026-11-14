import threading
from types import SimpleNamespace as NS

import pytest

from openpilot.system.lanlinkd import statusd
from openpilot.system.lanlinkd.params_api import delete_param, write_param
from openpilot.system.lanlinkd.settings import index_items
from openpilot.system.lanlinkd.toyota.session import car_is_parked

from .test_params_api import BOOL, FakeStore


@pytest.fixture
def runtime(monkeypatch):
  clock = {"now": 100.0}
  monkeypatch.setattr(statusd.time, "monotonic", lambda: clock["now"])
  params = FakeStore(initial={"IsOffroad": False, "IsEngaged": True, "AlphaLongitudinalEnabled": False},
                     types={"IsOffroad": BOOL, "IsEngaged": BOOL, "AlphaLongitudinalEnabled": BOOL})
  cache = statusd.StatusCache({}, "pc", params)
  exit_event = threading.Event()

  class Messages:
    def __init__(self):
      self.updated = dict.fromkeys(statusd.SERVICES, False)
      self.seen = dict.fromkeys(statusd.SERVICES, True)
      self.alive = dict.fromkeys(statusd.SERVICES, True)
      self.valid = dict.fromkeys(statusd.SERVICES, True)
      self.recv_time = dict.fromkeys(statusd.SERVICES, clock["now"])
      self.data = {
        "deviceState": NS(started=True),
        "carState": NS(gearShifter=1, standstill=True, vEgo=0.0),
        "pandaStates": [],
        "selfdriveState": NS(enabled=False),
        "selfdriveStateSP": NS(mads=NS(enabled=False)),
      }

    def __getitem__(self, name):
      return self.data.get(name, NS())

    def update(self, timeout):
      exit_event.set()

  sm = Messages()
  monkeypatch.setattr(statusd.messaging, "SubMaster", lambda *args, **kwargs: sm)

  def publish():
    exit_event.clear()
    cache.run(exit_event)

  return NS(cache=cache, params=params, sm=sm, clock=clock, publish=publish)


@pytest.mark.parametrize("ordinary, mads, expected", [(False, False, 204), (True, False, 409), (False, True, 409), (True, True, 409)])
@pytest.mark.parametrize("operation", ["write", "delete"])
def test_runtime_drives_not_engaged_gate(runtime, ordinary, mads, expected, operation):
  runtime.sm.data["selfdriveState"].enabled = ordinary
  runtime.sm.data["selfdriveStateSP"].mads.enabled = mads
  runtime.publish()
  items = index_items({"items": [{"key": "AlphaLongitudinalEnabled", "enablement": [{"type": "not_engaged"}]}]})
  before = dict(runtime.params.data)
  state = runtime.cache.runtime_state()
  code = (write_param(runtime.params, "AlphaLongitudinalEnabled", "1", state, items, {}) if operation == "write"
          else delete_param(runtime.params, "AlphaLongitudinalEnabled", state, items, {}))[0]
  assert code == expected
  if expected == 409:
    assert runtime.params.data == before
    assert runtime.params.versions == 0


@pytest.mark.parametrize("service", ["selfdriveState", "selfdriveStateSP"])
@pytest.mark.parametrize("failure", ["seen", "alive", "valid", "age"])
def test_disengagement_is_unknown_when_either_service_is_stale(runtime, service, failure):
  if failure == "age":
    runtime.sm.recv_time[service] -= statusd.SERVICE_TIMEOUT
  else:
    getattr(runtime.sm, failure)[service] = False
  runtime.publish()
  assert runtime.cache.runtime_state().engaged is None


def test_known_engagement_remains_engaged_when_other_service_is_missing(runtime):
  runtime.sm.data["selfdriveStateSP"].mads.enabled = True
  runtime.sm.seen["selfdriveState"] = False
  runtime.publish()
  assert runtime.cache.runtime_state().engaged is True


@pytest.mark.parametrize("failure", ["seen", "alive", "valid", "age"])
def test_stale_car_cannot_authorize_diagnostics(runtime, failure):
  if failure == "age":
    runtime.sm.recv_time["carState"] -= statusd.SERVICE_TIMEOUT
  else:
    getattr(runtime.sm, failure)["carState"] = False
  runtime.publish()
  snapshot = runtime.cache.snapshot()
  assert snapshot["stale"] is False
  assert snapshot["car"]["fresh"] is False
  assert not car_is_parked(snapshot)


def test_thread_stopping_expires_snapshot_and_engagement(runtime):
  runtime.publish()
  first = runtime.cache.snapshot()
  assert first["stale"] is False
  assert car_is_parked(first)
  assert runtime.cache.runtime_state().engaged is False
  runtime.clock["now"] += statusd.SNAPSHOT_TIMEOUT
  expired = runtime.cache.snapshot()
  assert expired["stale"] is True
  assert expired["car"]["fresh"] is False
  assert not car_is_parked(expired)
  assert runtime.cache.runtime_state().engaged is None
  assert first["car"]["fresh"] is True


def test_service_stopping_expires_even_when_thread_publishes(runtime):
  runtime.publish()
  runtime.clock["now"] += statusd.SERVICE_TIMEOUT
  runtime.sm.recv_time["deviceState"] = runtime.clock["now"]
  runtime.publish()
  snapshot = runtime.cache.snapshot()
  assert snapshot["stale"] is False
  assert not car_is_parked(snapshot)
  assert runtime.cache.runtime_state().engaged is None


def test_offroad_without_car_or_models_keeps_device_online(runtime):
  runtime.params.put("IsOffroad", True)
  runtime.sm.data["deviceState"].started = False
  for service in ("carState", "modelV2", "modelDataV2SP", "selfdriveState", "selfdriveStateSP"):
    runtime.sm.seen[service] = False
    runtime.sm.alive[service] = False
  runtime.publish()
  snapshot = runtime.cache.snapshot()
  assert snapshot["stale"] is False
  assert snapshot["car"]["fresh"] is False
  assert runtime.cache.runtime_state().offroad is True
  assert runtime.cache.runtime_state().engaged is None


def test_invalid_device_state_marks_snapshot_stale(runtime):
  runtime.sm.valid["deviceState"] = False
  runtime.publish()
  assert runtime.cache.snapshot()["stale"] is True
  assert runtime.cache.snapshot()["car"]["fresh"] is True
  assert not car_is_parked(runtime.cache.snapshot())


def test_empty_capabilities_are_computed_without_publishing_worker_cache(monkeypatch):
  params = NS(get=lambda key: None, get_bool=lambda key: False)
  cache = statusd.StatusCache({}, "pc", params)
  generation = {"value": 1}
  monkeypatch.setattr(statusd, "build_capabilities", lambda *args: {"generation": generation["value"]})
  assert cache.capabilities() == {"generation": 1}
  generation["value"] = 2
  assert cache.capabilities() == {"generation": 2}
  assert cache.snapshot()["capabilities"] == {"generation": 2}
  assert cache.snapshot()["stale"] is True
