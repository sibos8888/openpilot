import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from openpilot.common.basedir import BASEDIR
from openpilot.system.manager import process_config


@pytest.mark.parametrize("copyparty_available", [False, True])
def test_process_registration_is_independent_of_cwd(monkeypatch, tmp_path, copyparty_available):
  config_path = Path(process_config.__file__).resolve()
  copyparty_path = os.path.join(BASEDIR, "openpilot/third_party/copyparty/copyparty-sfx.py")
  registrations = []
  for cwd in (Path(BASEDIR), config_path.parent, tmp_path):
    monkeypatch.chdir(cwd)
    with patch.object(process_config.os.path, "exists", side_effect=lambda path: path == copyparty_path and copyparty_available):
      config = runpy.run_path(str(config_path))
    registrations.append(set(config["managed_processes"]))

  assert registrations[0] == registrations[1] == registrations[2]
  assert ("copyparty-sfx" in registrations[0]) is copyparty_available
