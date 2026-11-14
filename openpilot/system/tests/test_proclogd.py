from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.cereal import messaging
from openpilot.system import proclogd


def proc_stat(pid, starttime):
  return {
    "name": "worker", "pid": pid, "state": "S", "ppid": 1,
    "utime": 0, "stime": 0, "cutime": 0, "cstime": 0,
    "priority": 20, "nice": 0, "num_threads": 1,
    "starttime": starttime, "vms": 10 * 1024 * 1024,
    "rss": 10 * 1024 * 1024 // proclogd.PAGE_SIZE, "processor": 0,
  }


@pytest.fixture
def proc_boundary(monkeypatch):
  monkeypatch.setattr(proclogd, "_proc_cache", {})
  monkeypatch.setattr(proclogd, "_smaps_cache", {})
  monkeypatch.setattr(proclogd, "_smaps_cycle", 0)
  monkeypatch.setattr(proclogd, "_cpu_times", list)
  monkeypatch.setattr(proclogd, "_mem_info", lambda: dict.fromkeys([
    "MemTotal:", "MemFree:", "MemAvailable:", "Buffers:", "Cached:", "Active:", "Inactive:", "Shmem:",
  ], 0))
  state = SimpleNamespace(exe="/old/worker", cmdline=b"worker\0old\0", pss=1000)
  monkeypatch.setattr(proclogd.os, "readlink", lambda path: state.exe)
  monkeypatch.setattr(proclogd, "open", lambda *args, **kwargs: BytesIO(state.cmdline), raising=False)
  monkeypatch.setattr(proclogd, "_read_smaps", lambda pid: {
    "pss": state.pss, "pss_anon": state.pss, "pss_shmem": 0,
  })
  return state


def test_reused_pid_publishes_new_process_data(monkeypatch, proc_boundary):
  procs = Mock(side_effect=[[proc_stat(123, 10)], [proc_stat(123, 20)]])
  monkeypatch.setattr(proclogd, "_procs", procs)
  first = messaging.new_message("procLog")
  proclogd.build_proc_log_message(first)
  assert first.procLog.procs[0].exe == "/old/worker"
  assert list(first.procLog.procs[0].cmdline) == ["worker", "old"]
  assert first.procLog.procs[0].memPss == 1000

  proc_boundary.exe = "/new/worker"
  proc_boundary.cmdline = b"worker\0new\0"
  proc_boundary.pss = 2000
  second = messaging.new_message("procLog")
  proclogd.build_proc_log_message(second)

  proc = second.procLog.procs[0]
  assert proc.exe == "/new/worker"
  assert list(proc.cmdline) == ["worker", "new"]
  assert proc.memPss == 2000
  assert set(proclogd._proc_cache) == {(123, 20)}
  assert set(proclogd._smaps_cache) == {(123, 20)}


def test_exited_processes_are_reclaimed(monkeypatch, proc_boundary):
  monkeypatch.setattr(proclogd, "_procs", Mock(side_effect=[[proc_stat(123, 10)], []]))
  proclogd.build_proc_log_message(messaging.new_message("procLog"))

  message = messaging.new_message("procLog")
  proclogd.build_proc_log_message(message)

  assert len(message.procLog.procs) == 0
  assert not proclogd._proc_cache
  assert not proclogd._smaps_cache
