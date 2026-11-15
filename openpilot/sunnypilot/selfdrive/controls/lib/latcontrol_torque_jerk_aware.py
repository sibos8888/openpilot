"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from opendbc.car.lateral import FRICTION_THRESHOLD, get_friction
from openpilot.common.params import Params

from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_ext_base import LatControlTorqueExtBase


class LatControlTorqueJerkAware(LatControlTorqueExtBase):
  def __init__(self, lac_torque, CP, CP_SP, CI):
    super().__init__(lac_torque, CP, CP_SP, CI)
    self.params = Params()
    self._jerk_aware_enabled = self.params.get_bool("LateralJerkTorqueController")

  def get_jerk_aware_feedforward(self, CS, VM, desired_lateral_accel, actual_lateral_accel,
                                 lateral_accel_deadzone, gravity_adjusted_lateral_accel) -> float | None:
    if not self._jerk_aware_enabled:
      return None
    self.update_calculations(CS, VM, desired_lateral_accel)
    friction_input = self.update_friction_input(desired_lateral_accel, actual_lateral_accel)
    return (gravity_adjusted_lateral_accel - self.torque_params.latAccelOffset +
            get_friction(friction_input, lateral_accel_deadzone, FRICTION_THRESHOLD, self.torque_params))
