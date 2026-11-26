"""navStateSP：schema / rlog 回放 / 服务表。不含经纬度（决策 17），map 目标只走 mem Params。"""
import math

from openpilot.cereal import custom, log
from openpilot.cereal.services import SERVICE_LIST


def _field_names_recursive(schema):
  names = set()
  for field in schema.fields.values():
    names.add(field.proto.name)
    field_type = field.proto.slot.type
    if field_type.which() == "struct":
      names |= _field_names_recursive(field.schema)
    elif field_type.which() == "list" and field_type.list.elementType.which() == "struct":
      names |= _field_names_recursive(field.schema.elementType)
  return names


def test_nav_state_sp_has_no_coordinates_anywhere():
  # C3：递归扫整个 NavStateSP（不是只看 CurveAhead）
  for name in _field_names_recursive(custom.NavStateSP.schema):
    assert "latitude" not in name.lower()
    assert "longitude" not in name.lower()
    assert "x1e7" not in name.lower()
  curve_fields = set(custom.NavStateSP.CurveAhead.schema.fields.keys())
  assert {"pointCount", "horizonDistanceM", "ageMs", "alongRoute", "roadLookahead", "smoothed",
          "spacingM", "firstOffsetM", "curvaturesPerM"} <= curve_fields

def test_nav_state_sp_rlog_roundtrip():
  evt = log.Event.new_message()
  ns = evt.init("navStateSP")
  ns.frameIndex = 9
  ns.timestampEofNs = 123456789
  ns.phoneLocationOk = True
  ns.routeOk = True
  ns.navOk = True
  ns.rerouting = False
  ns.hasNavState = True
  ns.hasRoadAttributes = True
  ns.hasCurveAhead = True
  ns.hasNavDebug = True

  nav = ns.init("navState")
  nav.status = "navigating"
  nav.maneuverType = 20
  nav.nextManeuverType = 255
  nav.roundaboutExitIndex = 2
  nav.distanceToManeuverM = 350
  nav.routeRemainingM = 12000
  nav.routeRemainingSeconds = 900
  nav.ageMs = 40
  nav.navMapFed = True
  nav.navigationFed = True
  nav.onRoute = True
  nav.offRouteDistanceM = 254
  nav.headingDeg = 90.0

  road = ns.init("roadAttributes")
  road.speedLimitKph = 80
  road.roadClass = 3
  road.ramp = True
  road.bridge = True
  road.matchTrusted = True
  road.laneCount = 3
  road.matchDistanceM = 2.5
  road.ageMs = 1000

  curve = ns.init("curveAhead")
  curve.pointCount = 3
  curve.horizonDistanceM = 10
  curve.ageMs = 120
  curve.alongRoute = True
  curve.roadLookahead = False
  curve.smoothed = False
  curve.spacingM = 10
  curve.firstOffsetM = -10.0
  curvatures = curve.init("curvaturesPerM", 3)
  curvatures[0] = 0.0
  curvatures[1] = 0.02
  curvatures[2] = -0.0201
  debug = ns.init("navDebug")
  debug.renderDurationUs = 4200
  debug.mapMatchDurationUs = 1800
  debug.routeComputeDurationMs = 35
  debug.navCpuUsagePercent = 27

  raw = evt.to_bytes()
  with log.Event.from_bytes(raw) as back:  # 回放（rlog 的读写路径）
    assert back.which() == "navStateSP"
    got = back.navStateSP
    assert got.frameIndex == 9
    assert got.phoneLocationOk
    assert got.hasCurveAhead
    assert got.curveAhead.pointCount == 3 and len(got.curveAhead.curvaturesPerM) == 3
    assert got.curveAhead.spacingM == 10
    assert math.isclose(got.curveAhead.firstOffsetM, -10.0, rel_tol=1e-6)
    assert got.curveAhead.horizonDistanceM == 10
    assert got.curveAhead.alongRoute and not got.curveAhead.roadLookahead and not got.curveAhead.smoothed
    assert math.isclose(got.curveAhead.curvaturesPerM[1], 0.02, rel_tol=1e-6)
    assert math.isclose(got.curveAhead.curvaturesPerM[2], -0.0201, rel_tol=1e-6)

def test_nav_state_sp_float_invalid_is_nan():
  evt = log.Event.new_message()
  ns = evt.init("navStateSP")
  nav = ns.init("navState")
  nav.headingDeg = math.nan
  with log.Event.from_bytes(evt.to_bytes()) as back:
    assert math.isnan(back.navStateSP.navState.headingDeg)


def test_nav_state_sp_is_logged_and_map_params_are_not_services():
  svc = SERVICE_LIST["navStateSP"]
  assert svc.should_log and svc.frequency == 20.0 and svc.decimation == 10
  # 弯道经纬度只进 mem Params，不是 msgq 服务 → 不会进 rlog
  assert "LastGPSPosition" not in SERVICE_LIST
  assert "MapTargetVelocities" not in SERVICE_LIST
