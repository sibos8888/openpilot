import sys
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from openpilot.common import gpio
from openpilot.system.qcomgpsd import nmeaport


@pytest.mark.parametrize("sentence, expected", [
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,*1D", True),
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,*1d", True),
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,*00", False),
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,*ZZ", False),
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,", False),
  ("$GPGLL,4916.45,N,12311.12,W,225444,A,*1D0", False),
])
def test_nmea_checksum(sentence, expected):
  assert nmeaport.nmea_checksum_ok(sentence) is expected


def test_modem_configuration_accepts_string_responses(monkeypatch):
  responses = {
    "AT+QGPS?": "+QGPS: 0\r\n",
    'AT+QGPSCFG="outport"': '+QGPSCFG: "outport",usbnmea\r\n',
    'AT+QGPSCFG="gnssrawdata"': '+QGPSCFG: "gnssrawdata",3,0\r\n',
    "AT+QGPS=1": "OK",
  }
  at_cmd = Mock(side_effect=responses.__getitem__)
  monkeypatch.setitem(sys.modules, "openpilot.system.qcomgpsd.qcomgpsd", SimpleNamespace(at_cmd=at_cmd))
  monkeypatch.setattr(gpio, "gpio_init", Mock())
  monkeypatch.setattr(gpio, "gpio_set", Mock())
  monkeypatch.setattr(nmeaport, "check_output", Mock(side_effect=CalledProcessError(1, ["pidof", "qcomgpsd"])))
  process_messages = Mock(side_effect=RuntimeError("NMEA port opened"))
  monkeypatch.setattr(nmeaport, "process_nmea_port_messages", process_messages)

  with pytest.raises(RuntimeError, match="NMEA port opened"):
    nmeaport.main()

  assert at_cmd.call_args_list == [call(command) for command in responses]
  process_messages.assert_called_once_with()
