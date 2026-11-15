import pytest
from unittest.mock import patch

from openpilot.cereal import log, messaging
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.mock.generators import generate_deviceMotion
from openpilot.common.parameterized import parameterized
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.locationd.helpers import Pose
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces
from openpilot.sunnypilot.selfdrive.controls.lib import latcontrol_torque_v0


CONTROLLERS = [("v0", latcontrol_torque_v0.LatControlTorque), ("v1", LatControlTorque)]


def _make_model_v2(speed, accel):
  model = messaging.new_message('modelV2').modelV2
  model.position.x = [float(speed * t) for t in ModelConstants.T_IDXS]
  model.orientation.x = [0.0] * len(ModelConstants.T_IDXS)
  model.orientation.y = [0.0] * len(ModelConstants.T_IDXS)
  model.velocity.x = [speed] * len(ModelConstants.T_IDXS)
  model.acceleration.x = [0.0] * len(ModelConstants.T_IDXS)
  model.acceleration.y = [accel] * len(ModelConstants.T_IDXS)
  return model


def _make_controller(controller_type, enhanced=True):
  params = Params()
  params.put_bool("EnforceTorqueControl", True, block=True)
  params.put_bool("LateralJerkTorqueController", enhanced, block=True)
  params.put_bool("TorqueParamsOverrideEnabled", False, block=True)

  car_name = TOYOTA.TOYOTA_SIENNA_PATCHED
  CarInterface = interfaces[car_name]
  CP = CarInterface.get_non_essential_params(car_name)
  CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
  CI = CarInterface(CP, CP_SP)
  sunnypilot_interfaces.setup_interfaces(CI, params)
  CP.lateralTuning.torque.friction = 0.0
  CP_SP = convert_to_capnp(CP_SP)
  controller = controller_type(CP.as_reader(), CP_SP.as_reader(), CI, DT_CTRL)
  return controller, VehicleModel(CP), CP


def _run_update(controller, VM, CP, speed=15.0, accel=0.02):
  CS = car.CarState.new_message()
  CS.vEgo = speed
  CS.steeringPressed = False
  pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
  params = log.VehicleParameters.new_message(steerRatio=CP.steerRatio, stiffnessFactor=1.0)
  controller.extension.update_model_v2(_make_model_v2(speed, accel))
  controller.extension.update_lateral_lag(0.2)
  for i in range(len(controller.lat_accel_request_buffer)):
    controller.lat_accel_request_buffer[i] = accel
  return controller.update(True, CS, VM, params, False, accel / speed ** 2, pose, False, 0.2)


class TestJerkAwareUnits(OpenpilotTestCase):
  @parameterized.expand(CONTROLLERS)
  def test_single_pid_update(self, _version, controller_type):
    controller, VM, CP = _make_controller(controller_type)
    with patch.object(controller.pid, "update", wraps=controller.pid.update) as update:
      _run_update(controller, VM, CP)
    assert update.call_count == 1

  @parameterized.expand(CONTROLLERS)
  def test_integral_is_accel_space(self, _version, controller_type):
    controller, VM, CP = _make_controller(controller_type)
    controller.pid.i = 0.0
    _, _, pid_log = _run_update(controller, VM, CP)
    assert controller.pid.i == pytest.approx(controller.pid.k_i * 0.01 * pid_log.error)

  @parameterized.expand(CONTROLLERS)
  def test_offset_applied_when_enhanced(self, _version, controller_type):
    differences = []
    for enhanced in (False, True):
      outputs = []
      for offset in (0.0, 0.12):
        controller, VM, CP = _make_controller(controller_type, enhanced)
        controller.torque_params.latAccelOffset = offset
        output, _, _ = _run_update(controller, VM, CP)
        outputs.append(output)
      difference = outputs[1] - outputs[0]
      assert difference == pytest.approx(0.12 / controller.torque_params.latAccelFactor, abs=1e-6)
      differences.append(difference)
    assert differences[1] == pytest.approx(differences[0], abs=1e-6)

  @parameterized.expand(CONTROLLERS)
  def test_limits_consistent(self, _version, controller_type):
    for update_path in ("initial", "global", "speed_bins"):
      with self.subTest(update_path=update_path):
        controller, VM, CP = _make_controller(controller_type)
        factor = controller.torque_params.latAccelFactor
        if update_path == "global":
          controller.update_torque_parameters(factor, 0.0, 0.0)
        elif update_path == "speed_bins":
          tp = log.LateralTorqueParameters.new_message()
          tp.speedBinCenters = [6.5, 10.0, 15.0, 21.0, 26.5, 32.0, 37.5]
          tp.speedBinLatAccelFactors = [1.5] * 7
          tp.speedBinFrictions = [0.0] * 7
          tp.speedBinValid = [True] * 7
          tp.latAccelFactorFiltered = 1.5
          tp.frictionCoefficientFiltered = 0.0
          controller.extension.update_speed_dep_torque(tp)
          factor = 1.5
        output, _, _ = _run_update(controller, VM, CP, speed=30.0, accel=2.0)
        assert controller.torque_params.latAccelFactor == pytest.approx(factor)
        assert controller.pid.pos_limit == pytest.approx(controller.torque_params.latAccelFactor)
        assert controller.pid.neg_limit == pytest.approx(-controller.torque_params.latAccelFactor)
        assert abs(output) <= 1.0 + 1e-6

  @parameterized.expand(CONTROLLERS)
  def test_low_speed_frozen_integral_same_units(self, _version, controller_type):
    outputs = []
    for enhanced in (False, True):
      controller, VM, CP = _make_controller(controller_type, enhanced)
      controller.pid.i = 0.08
      output, _, _ = _run_update(controller, VM, CP, speed=4.0)
      assert controller.pid.i == 0.08
      outputs.append(output)
    assert outputs[1] == pytest.approx(outputs[0], abs=1e-9)
