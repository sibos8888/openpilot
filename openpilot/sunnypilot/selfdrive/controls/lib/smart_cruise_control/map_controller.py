import json
import math
import platform
import time

from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import MIN_V

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState

ACTIVE_STATES = (MapState.turning, )
ENABLED_STATES = (MapState.enabled, MapState.overriding, *ACTIVE_STATES)

TARGET_JERK = -0.6  # m/s^3 There's some jounce limits that are not consistent so we're fudging this some
TARGET_ACCEL = -1.2  # m/s^2 should match up with the long planner limit
TARGET_OFFSET = 1.0  # seconds - This controls how soon before the curve you reach the target velocity. It also helps
                     # reach the target velocity when inaccuracies in the distance modeling logic would cause overshoot.
                     # The value is multiplied against the target velocity to determine the additional distance. This is
                     # done to keep the distance calculations consistent but results in the offset actually being less
                     # time than specified depending on how much of a speed differential there is between v_ego and the
                     # target velocity.
TARGET_LAT_ACCEL = 2.0  # m/s^2 matches legacy BGM1 v4 phone target lateral acceleration


def _smooth_curvatures(raw: list[float], window: int = 5) -> list[float]:
  n = len(raw)
  if n <= 1 or window <= 1:
    return [float(k) for k in raw]
  half = window // 2
  out: list[float] = []
  for i in range(n):
    win = [float(raw[min(max(j, 0), n - 1)]) for j in range(i - half, i + half + 1)]
    win.sort()
    out.append(win[len(win) // 2])
  return out


def calculate_accel(t, target_jerk, a_ego):
  return a_ego + target_jerk * t


def calculate_velocity(t, target_jerk, a_ego, v_ego):
  return v_ego + a_ego * t + target_jerk/2 * (t ** 2)


def calculate_distance(t, target_jerk, a_ego, v_ego):
  return t * v_ego + a_ego/2 * (t ** 2) + target_jerk/6 * (t ** 3)


class SmartCruiseControlMap:
  v_target: float = 0
  a_target: float = 0.
  v_ego: float = 0.
  a_ego: float = 0.
  output_v_target: float = V_CRUISE_UNSET
  output_a_target: float = 0.

  def __init__(self, *, clock=None):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.enabled = self.params.get_bool("SmartCruiseControlMap")
    self.long_enabled = False
    self.long_override = False
    self.is_enabled = False
    self.is_active = False
    self.state = MapState.disabled
    self.v_cruise = 0
    self.target_d: float | None = None
    self.frame = -1
    self.clock = clock or (time.monotonic_ns if platform.system() == "Darwin" else
                           lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME))
    self.last_bad_log_ns = 0
    self.forward_targets: list[tuple[float, float]] = []
    self.snapshot = None
    self.sample_time_ns = 0
    self.travelled_m = 0.0
    self.last_update_ns = None
    self.motion_valid = True

  def get_v_target_from_control(self) -> float:
    if self.is_active:
      return max(self.v_target, MIN_V)

    return V_CRUISE_UNSET

  def get_a_target_from_control(self) -> float:
    return self.a_ego

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("SmartCruiseControlMap")

  def clear_targets(self):
    self.snapshot = None
    self.forward_targets = []
    self.v_target = 0.0
    self.target_d = None

  def read_targets(self, now_ns):
    raw = self.mem_params.get("MapTargetVelocities")
    if raw is None:
      return None
    try:
      snapshot = json.loads(raw)
      if snapshot == []:
        return None
      if not isinstance(snapshot, dict):
        raise ValueError("expected curvature envelope")
      if "points" in snapshot:
        raise ValueError("unsupported legacy points format")
      sample_ns, valid_until_ns = snapshot["sample_time_ns"], snapshot["valid_until_ns"]
      if (type(sample_ns) is not int or type(valid_until_ns) is not int or
          not 0 < sample_ns <= now_ns <= valid_until_ns <= sample_ns + 2_000_000_000):
        raise ValueError("invalid or expired sample times")
      spacing_m = snapshot["spacing_m"]
      if type(spacing_m) is not int or not 1 <= spacing_m <= 50:
        raise ValueError("invalid spacing_m")
      first_offset_m = snapshot["first_offset_m"]
      if (type(first_offset_m) not in (int, float) or
          not math.isfinite(first_offset_m) or
          not -100.0 <= first_offset_m <= 100.0):
        raise ValueError("invalid first_offset_m")
      curvatures = snapshot["curvatures"]
      if not isinstance(curvatures, list) or not 1 <= len(curvatures) <= 120:
        raise ValueError("invalid curvature sample count")
      for k in curvatures:
        if type(k) not in (int, float) or not math.isfinite(k) or abs(k) > 1.0:
          raise ValueError("invalid curvature value")
      return snapshot
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
      if now_ns - self.last_bad_log_ns >= 5_000_000_000 or self.last_bad_log_ns == 0:
        cloudlog.warning("SCC-map rejected targets: %s", error)
        self.last_bad_log_ns = now_ns
      return None

  def update_calculations(self) -> None:
    now_ns = self.clock()
    previous_ns = self.last_update_ns
    self.last_update_ns = now_ns
    if (not self.motion_valid or not math.isfinite(self.v_ego) or self.v_ego < 0 or
        not math.isfinite(self.a_ego) or (previous_ns is not None and now_ns < previous_ns)):
      self.clear_targets()
      return
    snapshot = self.read_targets(now_ns)
    if snapshot is None:
      self.clear_targets()
      return
    sample_ns = snapshot["sample_time_ns"]
    if sample_ns < self.sample_time_ns or (sample_ns == self.sample_time_ns and
        (self.snapshot is None or
         snapshot["spacing_m"] != self.snapshot["spacing_m"] or
         snapshot["first_offset_m"] != self.snapshot["first_offset_m"] or
         snapshot["curvatures"] != self.snapshot["curvatures"])):
      self.clear_targets()
      return
    delta_travelled_m = self.v_ego * ((now_ns - previous_ns) / 1e9) if previous_ns is not None else 0.0
    if sample_ns > self.sample_time_ns:
      self.sample_time_ns = sample_ns
      self.travelled_m = self.v_ego * ((now_ns - sample_ns) / 1e9)
    else:
      # 重复快照只能缩短期限；不能重新扣传输延迟或重置已行驶距离。
      snapshot["valid_until_ns"] = min(snapshot["valid_until_ns"], self.snapshot["valid_until_ns"])
      if now_ns > snapshot["valid_until_ns"]:
        self.clear_targets()
        return
      self.travelled_m += delta_travelled_m
    self.snapshot = snapshot

    spacing_m = snapshot["spacing_m"]
    first_offset_m = float(snapshot["first_offset_m"])
    k_smooth = _smooth_curvatures(snapshot["curvatures"])

    self.forward_targets = []
    # find velocities that we are within the distance we need to adjust for
    valid_velocities: list[tuple[float, float]] = []
    for i, k in enumerate(k_smooth):
      d = first_offset_m + i * spacing_m - self.travelled_m
      if d < 0.0:
        continue
      abs_k = abs(k)
      if abs_k <= 1e-6:
        continue
      tv = math.sqrt(TARGET_LAT_ACCEL / abs_k)
      if tv < 100.0:
        self.forward_targets.append((float(d), tv))
      if tv > self.v_ego:
        continue

      a_diff = (self.a_ego - TARGET_ACCEL)
      accel_t = abs(a_diff / TARGET_JERK)
      min_accel_v = calculate_velocity(accel_t, TARGET_JERK, self.a_ego, self.v_ego)

      max_d = 0.0
      if tv > min_accel_v:
        # calculate time needed based on target jerk
        a = 0.5 * TARGET_JERK
        b = self.a_ego
        c = self.v_ego - tv
        t_a = -1 * ((b**2 - 4 * a * c) ** 0.5 + b) / (2 * a)
        t_b = ((b**2 - 4 * a * c) ** 0.5 - b) / (2 * a)
        if not isinstance(t_a, complex) and t_a > 0:
          t = t_a
        else:
          t = t_b
        if isinstance(t, complex):
          continue

        max_d = max_d + calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)
      else:
        t = accel_t
        max_d = calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)

        # calculate additional time needed based on target accel
        t = abs((min_accel_v - tv) / TARGET_ACCEL)
        max_d += calculate_distance(t, 0, TARGET_ACCEL, min_accel_v)

      if d < max_d + tv * TARGET_OFFSET:
        valid_velocities.append((tv, float(d)))

    # Find the smallest velocity we need to adjust for (tie-break nearest distance)
    min_v = 100.0
    min_d: float | None = None
    for tv, d in valid_velocities:
      if tv < min_v or (tv == min_v and (min_d is None or d < min_d)):
        min_v = tv
        min_d = d

    if self.v_target < min_v and self.target_d is not None:
      rem_d = self.target_d - delta_travelled_m
      if rem_d >= 0.0:
        max_d_err = max(float(spacing_m), 15.0)
        max_v_err = max(0.5, self.v_target * 0.05)
        best_d: float | None = None
        best_err = float("inf")
        for d, tv in self.forward_targets:
          err = abs(d - rem_d)
          if err <= max_d_err and tv <= self.v_ego and abs(tv - self.v_target) <= max_v_err and err < best_err:
            best_err = err
            best_d = d
        if best_d is not None:
          self.target_d = best_d
          return

    self.v_target = min_v
    self.target_d = min_d if valid_velocities else None

  def _update_state_machine(self) -> tuple[bool, bool]:
    # ENABLED, TURNING
    if self.state != MapState.disabled:
      if not self.long_enabled or not self.enabled:
        self.state = MapState.disabled
      elif self.long_override:
        self.state = MapState.overriding

      else:
        # ENABLED
        if self.state == MapState.enabled:
          if self.v_cruise > self.v_target != 0:
            self.state = MapState.turning

        # TURNING
        elif self.state == MapState.turning:
          if self.v_cruise <= self.v_target or self.v_target == 0:
            self.state = MapState.enabled

        # OVERRIDING
        elif self.state == MapState.overriding:
          if not self.long_override:
            if self.v_cruise > self.v_target != 0:
              self.state = MapState.turning
            else:
              self.state = MapState.enabled

    # DISABLED
    elif self.state == MapState.disabled:
      if self.long_enabled and self.enabled:
        if self.long_override:
          self.state = MapState.overriding
        else:
          self.state = MapState.enabled

    enabled = self.state in ENABLED_STATES
    active = self.state in ACTIVE_STATES

    return enabled, active

  def update(self, long_enabled: bool, long_override: bool, v_ego, a_ego, v_cruise, *, motion_valid: bool = True) -> None:
    self.motion_valid = motion_valid
    self.long_enabled = long_enabled
    self.long_override = long_override
    self.v_ego = v_ego
    self.a_ego = a_ego
    self.v_cruise = v_cruise

    self.update_params()
    self.update_calculations()

    self.is_enabled, self.is_active = self._update_state_machine()

    self.output_v_target = self.get_v_target_from_control()
    self.output_a_target = self.get_a_target_from_control()

    self.frame += 1
