import unittest

import numpy as np

from openpilot.common.transformations.camera import img_from_device


class TestCameraProjection(unittest.TestCase):
  def test_single_and_batch(self):
    points = np.array([[2., 1., -1.], [1., 0., 0.]])
    expected = np.array([[0.5, -0.5], [0., 0.]])
    np.testing.assert_allclose(img_from_device(points), expected)
    projected = img_from_device(points[0])
    self.assertEqual(projected.shape, (2,))
    np.testing.assert_allclose(projected, expected[0])
    np.testing.assert_allclose(projected, img_from_device(points)[0])

  def test_negative_depth(self):
    point = np.array([-1., 2., 3.])
    self.assertTrue(np.isnan(img_from_device(point)).all())
    projected = img_from_device(np.array([point, [1., 0., 0.]]))
    self.assertEqual(projected.shape, (2, 2))
    self.assertTrue(np.isnan(projected[0]).all())
    np.testing.assert_allclose(projected[1], [0., 0.])
