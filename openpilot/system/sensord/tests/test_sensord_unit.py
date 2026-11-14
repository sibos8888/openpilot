import select
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.common.gpio import gpioevent_data
from openpilot.system.sensord import sensord


def test_worker_exit_stops_service(monkeypatch):
  sensors = [Mock(), Mock(), Mock()]
  for name, sensor in zip(("LSM6DS3_Accel", "LSM6DS3_Gyro", "LSM6DS3_Temp"), sensors, strict=True):
    monkeypatch.setattr(sensord, name, Mock(return_value=sensor))
  monkeypatch.setattr(sensord, "config_realtime_process", Mock())
  irq_thread = Mock()
  irq_thread.is_alive.return_value = False
  polling_thread = Mock()
  polling_thread.is_alive.return_value = True
  thread_factory = Mock(side_effect=[irq_thread, polling_thread])
  monkeypatch.setattr(sensord.threading, "Thread", thread_factory)
  monkeypatch.setattr(sensord.time, "sleep", Mock(side_effect=AssertionError("continued with a dead worker")))

  with pytest.raises(RuntimeError, match="worker thread exited"):
    sensord.main()

  exit_event = thread_factory.call_args_list[0].kwargs["args"][1]
  assert exit_event.is_set()
  polling_thread.join.assert_called_once()
  for sensor in sensors:
    sensor.shutdown.assert_called_once()


def test_interrupt_uses_last_complete_event(monkeypatch):
  exit_event = threading.Event()
  sensor = Mock()
  sensor.is_data_valid.return_value = True
  sensor.get_event.side_effect = lambda ts: exit_event.set()
  pm = Mock()
  monkeypatch.setattr(sensord.messaging, "PubMaster", Mock(return_value=pm))
  monkeypatch.setattr(sensord.messaging, "new_message", lambda *args, **kwargs: SimpleNamespace())
  monkeypatch.setattr(sensord, "gpiochip_get_ro_value_fd", lambda *args: 10)
  monkeypatch.setattr(sensord.os.path, "exists", lambda path: False)
  monkeypatch.setattr(sensord.time, "time_ns", lambda: 1000)
  monkeypatch.setattr(sensord.time, "monotonic_ns", lambda: 900)
  poller = Mock()
  poller.poll.return_value = [(10, select.POLLIN)]
  monkeypatch.setattr(sensord.select, "poll", lambda: poller)
  events = (gpioevent_data * 2)()
  events[0].timestamp = 200
  events[1].timestamp = 300
  monkeypatch.setattr(sensord.os, "read", lambda fd, count: bytes(events))

  sensord.interrupt_loop([(sensor, "accelerometer", True)], exit_event)

  sensor.get_event.assert_called_once_with(200)
  pm.send.assert_called_once()


def test_invalid_polling_samples_still_wait(monkeypatch):
  exit_event = threading.Event()
  sensor = Mock()
  reads = 0

  def get_event():
    nonlocal reads
    reads += 1
    if reads == 2:
      exit_event.set()
    return reads

  sensor.get_event.side_effect = get_event
  sensor.is_data_valid.side_effect = [False, True]
  pm = Mock()
  rk = Mock()
  monkeypatch.setattr(sensord.messaging, "PubMaster", lambda services: pm)
  monkeypatch.setattr(sensord.messaging, "new_message", lambda *args, **kwargs: SimpleNamespace())
  monkeypatch.setattr(sensord, "Ratekeeper", lambda *args, **kwargs: rk)

  sensord.polling_loop(sensor, "temperatureSensor", exit_event)

  assert reads == 2
  assert rk.keep_time.call_count == 2
  assert pm.send.call_count == 1
  assert pm.send.call_args.args[1].temperatureSensor == 2
