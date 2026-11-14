from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd import locationd
from openpilot.selfdrive.locationd.models.pose_kf import PoseKalman


class TestLocationdInitialState(OpenpilotTestCase):
  def test_published_standard_deviations_survive_restart(self):
    std = np.linspace(0.1, 2.0, PoseKalman.initial_P.shape[0])
    estimator = locationd.LocationEstimator(debug=True)
    estimator.reset(10.0, PoseKalman.initial_x, np.diag(std ** 2))
    original = estimator.get_msg(True, True, True)
    np.testing.assert_allclose(original.deviceMotion.debugFilterState.std, std)
    Params().put('LocationFilterInitialState', original.to_bytes(), block=True)

    sm = MagicMock()
    sm.all_checks.return_value = False
    sm.all_valid.return_value = False
    sm.updated = {'cameraOdometry': True}
    pm = MagicMock()
    pm.send.side_effect = StopIteration

    with patch.dict('os.environ', {'DEBUG': '1'}), \
         patch.object(locationd, 'config_realtime_process'), \
         patch.object(locationd.messaging, 'SubMaster', return_value=sm), \
         patch.object(locationd.messaging, 'PubMaster', return_value=pm), \
         patch.object(locationd.messaging, 'sub_sock'), \
         patch.object(locationd.messaging, 'drain_sock', return_value=[]), \
         pytest.raises(StopIteration):
      locationd.main()

    service, restored = pm.send.call_args.args
    assert service == 'deviceMotion'
    np.testing.assert_allclose(restored.deviceMotion.debugFilterState.value, original.deviceMotion.debugFilterState.value)
    np.testing.assert_allclose(restored.deviceMotion.debugFilterState.std, std)
