import numpy as np
import pytest

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd.models.car_kf import CarKalman
from openpilot.selfdrive.locationd.paramsd import VehicleParamsLearner, retrieve_initial_vehicle_params


class TestParamsd(OpenpilotTestCase):
  def test_read_saved_params(self):
    for debug in [False, True]:
      with self.subTest(debug=debug):
        params = Params()
        CP = car.CarParams(carFingerprint='test', steerRatio=15.0, mass=1500.0, rotationalInertia=2500.0,
                           centerToFront=1.2, wheelbase=2.7, tireStiffnessFront=80000.0, tireStiffnessRear=80000.0)
        std = np.linspace(0.1, 2.0, CarKalman.P_initial.shape[0])
        covariance = np.diag(std ** 2)
        learner = VehicleParamsLearner(CP, 16.0, 0.9, 0.01, covariance)
        msg = learner.get_msg(True, debug=True)
        np.testing.assert_allclose(msg.vehicleParameters.debugFilterState.std, std)
        params.put('LiveParametersV2', msg.to_bytes(), block=True)
        params.put('CarParamsPrevRoute', CP.to_bytes(), block=True)

        sr, sf, offset, p_init = retrieve_initial_vehicle_params(params, CP, replay=True, debug=debug)

        assert sr == pytest.approx(16.0)
        assert sf == pytest.approx(0.9)
        assert offset == pytest.approx(np.degrees(0.01))
        if debug:
          np.testing.assert_allclose(p_init, covariance)
          restored = VehicleParamsLearner(CP, sr, sf, np.radians(offset), p_init)
          np.testing.assert_allclose(restored.get_msg(True, debug=True).vehicleParameters.debugFilterState.std, std)
        else:
          assert p_init is None
