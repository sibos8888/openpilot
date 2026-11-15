#!/usr/bin/env python3
# simple pandad wrapper that updates the panda first
import os
import signal
import subprocess

from panda import Panda, PandaDFU
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.hardware import HARDWARE
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.selfdrive.pandad.rivian_long_flasher import flash_rivian_long

# 复位后 panda 要过一会儿才能被枚举到：comma four 约 0.2 s，C3XL 的 tici 实测 10.0 s（5 轮一致）。
# 给足余量；枚举到就返回，只有 panda 真不在时才会多等这么久再 recover
PANDA_BOOT_TIMEOUT_S = 15


def panda_ready() -> bool:
  """Is the internal panda running the firmware we ship?"""
  try:
    if not Panda.wait_for_panda(None, timeout=PANDA_BOOT_TIMEOUT_S):
      cloudlog.warning("no panda found")
      return False
    panda_serials = Panda.list()
    flash_rivian_long(panda_serials)
    panda_serials = check_panda_support(panda_serials)
    assert len(panda_serials) == 1, "no supported internal panda found"
    with Panda(panda_serials[0], cli=False) as panda:
      if panda.get_type() not in Panda.SUPPORTED_DEVICES:
        cloudlog.warning(f"unsupported panda type: {panda.get_type()}")
        return False
      if panda.bootstub:
        cloudlog.warning(f"panda stuck in bootstub, version {panda.get_version()}")
        return False
      if not panda.up_to_date():
        cloudlog.warning(f"panda firmware out of date, version {panda.get_version()}")
        return False
      return True
  except Exception:
    # Old firmware may not understand our SPI protocol.
    cloudlog.exception("pandad.panda_check_failed")
    return False


def flash_panda() -> None:
  """Recover both bootstub and app through the protocol-independent ST bootloader."""
  cloudlog.warning("flashing panda")
  HARDWARE.recover_internal_panda()
  assert Panda.wait_for_dfu(None, timeout=5), "panda didn't enter DFU"
  with PandaDFU(None) as dfu:
    dfu.recover()

  # On tici, jumping from the ROM loader can leave bootstub unreachable until a hardware reset.
  HARDWARE.reset_internal_panda()

  assert Panda.wait_for_panda(None, timeout=PANDA_BOOT_TIMEOUT_S), "panda didn't boot into bootstub"
  panda_serials = check_panda_support(Panda.list())
  assert len(panda_serials) == 1, "no supported internal panda found after recovery"
  with Panda(panda_serials[0], cli=False) as panda:
    panda.flash()
  cloudlog.warning("done flashing panda")


def check_panda_support(panda_serials: list[str]) -> list[str]:
  spi_serials = set(Panda.spi_list())
  for serial in panda_serials:
    if serial in spi_serials:
      return [serial]

  for serial in panda_serials:
    panda = Panda(serial)
    is_internal = panda.is_internal()
    panda.close()
    if is_internal:
      return [serial]

  return []


def main() -> None:
  # signal pandad to close the relay and exit
  def signal_handler(signum, frame):
    cloudlog.info(f"Caught signal {signum}, exiting")
    nonlocal do_exit
    do_exit = True
    if process is not None:
      process.send_signal(signal.SIGINT)

  process = None
  do_exit = False
  signal.signal(signal.SIGINT, signal_handler)

  # check health for lost heartbeat
  try:
    for s in Panda.list():
      with Panda(s) as p:
        health = p.health()
        if p.is_internal() and health["heartbeat_lost"]:
          Params().put_bool("PandaHeartbeatLost", True, block=True)
          cloudlog.event("heartbeat lost", deviceState=health)
  except Exception:
    cloudlog.exception("pandad.uncaught_exception")

  count = 0
  while not do_exit:
    try:
      cloudlog.event("pandad.flash_and_connect", count=count)
      HARDWARE.reset_internal_panda()
      count += 1

      if not do_exit and not panda_ready():
        if do_exit:
          break
        flash_panda()
        if do_exit:
          break
        assert panda_ready(), "panda not ready after flashing"

      # 刷写期间收到退出信号（如 LANLink 诊断模式让出 panda）：不再启动 ./pandad。
      # 否则 manager 5 s 后 SIGKILL 本进程，./pandad 成为孤儿一直占着 panda
      if not do_exit:
        # run real pandad
        os.environ['MANAGER_DAEMON'] = 'pandad'
        process = subprocess.Popen(["./pandad"], cwd=os.path.join(BASEDIR, "openpilot/selfdrive/pandad"))
        if do_exit:  # 信号正好落在上面的判断和 Popen 之间，signal_handler 没赶上转发
          process.send_signal(signal.SIGINT)
        process.wait()
    except Exception:
      cloudlog.exception("pandad.uncaught_exception")


if __name__ == "__main__":
  main()
