import os
import tempfile
from uuid import uuid4

from openpilot.common.test import OpenpilotTestCase
from openpilot.common.utils import atomic_write


class TestFileHelpers(OpenpilotTestCase):
  def run_atomic_write_func(self, atomic_write_func):
    path = f"/tmp/tmp{uuid4()}"
    with atomic_write_func(path) as f:
      f.write("test")
      assert not os.path.exists(path)

    with open(path) as f:
      assert f.read() == "test"
    os.remove(path)

  def test_atomic_write(self):
    self.run_atomic_write_func(atomic_write)

  def test_atomic_write_rejects_competing_creation(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "target")
      with self.assertRaises(FileExistsError):
        with atomic_write(path) as pending:
          pending.write("pending")
          with open(path, "x") as competitor:
            competitor.write("competitor")
      with open(path) as result:
        assert result.read() == "competitor"
      assert os.listdir(directory) == ["target"]

  def test_atomic_write_rejects_competing_symlink(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "target")
      with self.assertRaises(FileExistsError):
        with atomic_write(path) as pending:
          pending.write("pending")
          os.symlink("missing", path)
      assert os.readlink(path) == "missing"
      assert os.listdir(directory) == ["target"]

  def test_atomic_write_overwrites_competing_creation(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "target")
      with atomic_write(path, overwrite=True) as pending:
        pending.write("pending")
        with open(path, "x") as competitor:
          competitor.write("competitor")
      with open(path) as result:
        assert result.read() == "pending"
      assert os.listdir(directory) == ["target"]

  def test_atomic_write_cleans_up_after_exception(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "target")
      with self.assertRaises(RuntimeError):
        with atomic_write(path) as pending:
          pending.write("pending")
          raise RuntimeError("cancel write")
      assert os.listdir(directory) == []
