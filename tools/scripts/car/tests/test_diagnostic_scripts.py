from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


CAR_SCRIPTS = Path(__file__).resolve().parents[1]


class MessageTimeoutError(Exception):
  pass


class TestDiagnosticScripts(unittest.TestCase):
  def setUp(self):
    self.stack = ExitStack()
    self.addCleanup(self.stack.close)
    self.panda = Mock()
    self.client = Mock()
    self.panda_type = Mock(return_value=self.panda)
    self.uds_type = Mock(return_value=self.client)
    modules = {}
    for name in ("opendbc", "opendbc.car", "panda", "panda.python"):
      modules[name] = ModuleType(name)
      modules[name].__dict__["__path__"] = []
    modules["panda"].__dict__["Panda"] = self.panda_type
    modules["panda.python"].__dict__["Panda"] = self.panda_type
    modules["opendbc.car.carlog"] = ModuleType("opendbc.car.carlog")
    modules["opendbc.car.carlog"].__dict__["carlog"] = Mock()
    modules["opendbc.car.structs"] = ModuleType("opendbc.car.structs")
    modules["opendbc.car.structs"].__dict__["CarParams"] = SimpleNamespace(SafetyModel=SimpleNamespace(elm327=1))
    modules["opendbc.car.uds"] = ModuleType("opendbc.car.uds")
    modules["opendbc.car.uds"].__dict__.update(
      UdsClient=self.uds_type,
      MessageTimeoutError=MessageTimeoutError,
      SESSION_TYPE=SimpleNamespace(EXTENDED_DIAGNOSTIC=3),
      DTC_GROUP_TYPE=SimpleNamespace(ALL=0xFFFFFF),
      DATA_IDENTIFIER_TYPE=int,
    )
    self.stack.enter_context(patch.dict(sys.modules, modules))
    self.stack.enter_context(patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(1, ["pidof", "pandad"])))
    self.stack.enter_context(patch("builtins.input", return_value="OK"))
    self.stack.enter_context(redirect_stdout(io.StringIO()))
    radar = runpy.run_path(str(CAR_SCRIPTS / "hyundai_enable_radar_points.py"))
    self.firmware, self.config = next(iter(radar["SUPPORTED_FW_VERSIONS"].items()))

  def run_script(self, name, *args):
    with patch.object(sys, "argv", [name, *args]):
      return runpy.run_path(str(CAR_SCRIPTS / name), run_name="__main__")

  def test_unknown_radar_config_never_writes(self):
    self.client.read_data_by_identifier.side_effect = [self.firmware, b"unknown configuration"]
    with self.assertRaises(SystemExit) as error:
      self.run_script("hyundai_enable_radar_points.py")
    self.assertEqual(error.exception.code, 1)
    self.client.write_data_by_identifier.assert_not_called()

  def test_default_radar_config_can_enable_points(self):
    self.client.read_data_by_identifier.side_effect = [self.firmware, self.config.default_config]
    self.run_script("hyundai_enable_radar_points.py")
    self.client.write_data_by_identifier.assert_called_once_with(0x0142, self.config.tracks_enabled)

  def test_enabled_radar_config_does_not_write_again(self):
    self.client.read_data_by_identifier.side_effect = [self.firmware, self.config.tracks_enabled]
    with self.assertRaises(SystemExit) as error:
      self.run_script("hyundai_enable_radar_points.py")
    self.assertEqual(error.exception.code, 0)
    self.client.write_data_by_identifier.assert_not_called()

  def test_explicit_radar_reset_retains_existing_behavior(self):
    self.client.read_data_by_identifier.side_effect = [self.firmware, b"unknown configuration"]
    self.run_script("hyundai_enable_radar_points.py", "--default")
    self.client.write_data_by_identifier.assert_called_once_with(0x0142, self.config.default_config)

  def test_physical_dtc_timeout_is_reported(self):
    for operation in ("diagnostic_session_control", "clear_diagnostic_information"):
      with self.subTest(operation=operation):
        self.client.reset_mock(side_effect=True)
        getattr(self.client, operation).side_effect = MessageTimeoutError("no ECU response")
        with self.assertRaises(MessageTimeoutError):
          self.run_script("clear_dtc.py", "0x7d0")

  def test_functional_dtc_timeouts_remain_tolerated(self):
    self.client.diagnostic_session_control.side_effect = MessageTimeoutError("broadcast session")
    self.client.clear_diagnostic_information.side_effect = MessageTimeoutError("broadcast clear")
    self.run_script("clear_dtc.py")
    self.client.clear_diagnostic_information.assert_called_once()
