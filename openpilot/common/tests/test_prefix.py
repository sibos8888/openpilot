import os
import unittest
from unittest import mock

from openpilot.common.hardware.hw import DEFAULT_DOWNLOAD_CACHE_ROOT
from openpilot.common.prefix import OpenpilotPrefix


class TestPrefix(unittest.TestCase):
  def prefix(self, **kwargs):
    return OpenpilotPrefix(create_dirs_on_enter=False, clean_dirs_on_exit=False, **kwargs)

  def test_cache_restored(self):
    for original in (None, "", "/tmp/existing-cache"):
      with self.subTest(original=original), mock.patch.dict(os.environ):
        if original is None:
          os.environ.pop("COMMA_CACHE", None)
        else:
          os.environ["COMMA_CACHE"] = original
        with self.prefix(shared_download_cache=True):
          self.assertEqual(os.environ["COMMA_CACHE"], DEFAULT_DOWNLOAD_CACHE_ROOT)
        self.assertEqual(os.environ.get("COMMA_CACHE"), original)

  def test_nested_cache_restored(self):
    with mock.patch.dict(os.environ, {"COMMA_CACHE": "/tmp/original-cache", "OPENPILOT_PREFIX": "original"}):
      with self.prefix(prefix="outer"):
        with self.prefix(prefix="inner", shared_download_cache=True):
          self.assertEqual(os.environ["COMMA_CACHE"], DEFAULT_DOWNLOAD_CACHE_ROOT)
          self.assertEqual(os.environ["OPENPILOT_PREFIX"], "inner")
        self.assertEqual(os.environ["COMMA_CACHE"], "/tmp/original-cache")
        self.assertEqual(os.environ["OPENPILOT_PREFIX"], "outer")
      self.assertEqual(os.environ["COMMA_CACHE"], "/tmp/original-cache")
      self.assertEqual(os.environ["OPENPILOT_PREFIX"], "original")

  def test_exception_restores_environment(self):
    with mock.patch.dict(os.environ, {"COMMA_CACHE": "/tmp/original-cache", "OPENPILOT_PREFIX": "original"}):
      with self.assertRaises(RuntimeError), self.prefix(shared_download_cache=True):
        raise RuntimeError("body failed")
      self.assertEqual(os.environ["COMMA_CACHE"], "/tmp/original-cache")
      self.assertEqual(os.environ["OPENPILOT_PREFIX"], "original")

  def test_cleanup_failure_restores_environment(self):
    context = self.prefix(shared_download_cache=True)
    context.clean_dirs_on_exit = True
    with mock.patch.dict(os.environ, {"COMMA_CACHE": "/tmp/original-cache", "OPENPILOT_PREFIX": "original"}):
      with mock.patch.object(context, "clean_dirs", side_effect=RuntimeError("cleanup failed")):
        with self.assertRaises(RuntimeError), context:
          pass
      self.assertEqual(os.environ["COMMA_CACHE"], "/tmp/original-cache")
      self.assertEqual(os.environ["OPENPILOT_PREFIX"], "original")
