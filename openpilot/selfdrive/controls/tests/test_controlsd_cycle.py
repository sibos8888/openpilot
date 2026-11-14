from collections import defaultdict
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from opendbc.car.structs import car
from openpilot.cereal import custom, messaging
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.sunnypilot.selfdrive.controls.lib.blinker_pause_lateral import BlinkerPauseLateral


def build_controls():
  controls = Controls.__new__(Controls)
  controls.CP = car.CarParams.new_message()
  controls.CP.steerControlType = 'angle'
  controls.CP.lateralTuning.init('pid')
  controls.CP_SP = custom.CarParamsSP.new_message()
  services: dict[str, Any] = {}
  for service in ('carState', 'vehicleParameters', 'longitudinalPlan', 'modelV2', 'selfdriveState', 'selfdriveStateSP',
                  'lateralDelay', 'carOutput', 'radarState'):
    services[service] = getattr(messaging.new_message(service), service)
  services['onroadEvents'] = []
  services['carState'].vEgo = 4.5
  services['carState'].canValid = True
  services['selfdriveState'].active = True
  services['selfdriveState'].enabled = True
  services['carOutput'].actuatorsOutput.steeringAngleDeg = 10.0
  controls.sm = MagicMock()
  controls.sm.__getitem__.side_effect = services.__getitem__
  controls.sm.valid = defaultdict(bool)
  controls.sm.updated = defaultdict(bool)
  controls.sm.logMonoTime = defaultdict(int)
  controls.pm = Mock()
  controls.VM = Mock()
  controls.VM.calc_curvature.return_value = 0.0
  controls.CI = Mock()
  controls.CI.get_pid_accel_limits.return_value = (-1.0, 1.0)
  controls.LoC = Mock(long_control_state='off', pid=Mock(p=0.0, i=0.0, f=0.0))
  controls.LoC.update.return_value = 0.0
  controls.LaC = Mock()
  angle_log = messaging.new_message('controlsState').controlsState.lateralControlState.init('angleState')
  controls.LaC.update.return_value = (0.0, 0.0, angle_log)
  controls.steer_limited_by_safety = False
  controls.curvature = 0.0
  controls.desired_curvature = 0.0
  controls.calibrated_pose = None
  controls._param_update_time = float('inf')
  controls.blinker_pause_lateral = BlinkerPauseLateral()
  controls.blinker_pause_lateral.enabled = True
  controls.blinker_pause_lateral.min_speed = 20.0
  controls.blinker_pause_lateral.reengage_delay = 2.0
  return controls, services


class TestControlsCycle(OpenpilotTestCase):
  def test_blinker_delay_spans_two_hundred_control_cycles(self):
    controls, services = build_controls()
    services['carState'].leftBlinker = True
    published = []

    def capture(service, msg):
      if service == 'carControl':
        published.append((msg.carControl.latActive, controls.steer_limited_by_safety))

    def next_cycle():
      services['carState'].leftBlinker = False
      if len(published) == 201:
        raise StopIteration

    clock = Mock()
    clock.monitor_time.side_effect = next_cycle
    with patch.object(controls.pm, 'send', side_effect=capture), \
         patch('openpilot.selfdrive.controls.controlsd.Ratekeeper', return_value=clock), pytest.raises(StopIteration):
      controls.run()

    assert published[:200] == [(False, False)] * 200
    assert published[200] == (True, True)

  def test_publication_uses_activity_before_actuator_gates(self):
    for blocked_by in ['steerFaultTemporary', 'steerFaultPermanent', 'standstill']:
      with self.subTest(blocked_by=blocked_by):
        controls, services = build_controls()
        setattr(services['carState'], blocked_by, True)
        cc, lateral_log = controls.state_control()
        assert not cc.latActive
        controls.publish(cc, lateral_log)
        assert controls.steer_limited_by_safety
