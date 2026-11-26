#include "nav_outputs.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <cstdio>
#include <utility>

namespace {

constexpr float kCurvatureX1e5ToPerM = 1e-5f;
constexpr float kCentidegToDeg = 0.01f;
constexpr float kDmToM = 0.1f;

// 事件时刻是否在 now 的 max_age 以内（事件时刻在未来 = 时钟回跳，按过期处理，安全侧）
bool within(uint64_t event_ns, uint64_t now_ns, uint64_t max_age_ns) {
  return event_ns != 0 && now_ns >= event_ns && now_ns - event_ns <= max_age_ns;
}

void append_curvature_json(std::string* out, int16_t curvature_x1e5) {
  if (curvature_x1e5 == 0) {
    *out += '0';
    return;
  }
  char buf[24];
  std::snprintf(buf, sizeof buf, "%.5f", double(curvature_x1e5) * 1e-5);
  *out += buf;
}
}  // namespace

NavStateSnapshot nav_state_snapshot_from_reply(const bgm1::Reply& reply) {
  NavStateSnapshot s;
  s.frame_index = reply.frame_idx;
  s.timestamp_eof_ns = reply.t_eof;

  const bgm1::NavExt& ext = reply.nav_ext;
  s.phone_location_ok = ext.nav_health_flags & bgm1::kNavHealthPhoneLocationOk;
  s.route_ok = ext.nav_health_flags & bgm1::kNavHealthRouteOk;
  s.nav_ok = ext.nav_health_flags & bgm1::kNavHealthNavOk;
  s.rerouting = ext.nav_health_flags & bgm1::kNavHealthRerouting;
  s.has_nav_state = ext.present_flags & bgm1::kNavPresentNavState;
  s.has_road_attributes = ext.present_flags & bgm1::kNavPresentRoadAttr;
  s.has_curve_ahead = ext.present_flags & bgm1::kNavPresentCurveAhead;
  s.has_nav_debug = ext.present_flags & bgm1::kNavPresentNavDebug;

  const bgm1::NavState& n = ext.nav_state;
  s.nav_state.status = n.nav_status;
  s.nav_state.maneuver_type = n.maneuver_type;
  s.nav_state.next_maneuver_type = n.next_maneuver_type;
  s.nav_state.roundabout_exit_index = n.roundabout_exit_index;
  s.nav_state.distance_to_maneuver_m = n.distance_to_maneuver_m;
  s.nav_state.route_remaining_m = n.route_remaining_m;
  s.nav_state.route_remaining_seconds = n.route_remaining_s;
  s.nav_state.age_ms = n.age_ms;
  s.nav_state.nav_map_fed = n.nav_input_flags & 0x01;
  s.nav_state.navigation_fed = n.nav_input_flags & 0x02;
  s.nav_state.on_route = n.nav_input_flags & 0x04;
  s.nav_state.off_route_distance_m = n.off_route_distance_m;
  s.nav_state.heading_deg = n.heading_centideg == bgm1::kNavU16Invalid
                                ? NAN
                                : float(n.heading_centideg) * kCentidegToDeg;

  const bgm1::RoadAttr& r = ext.road_attr;
  s.road_attributes.speed_limit_kph = r.speed_limit_kph;
  s.road_attributes.speed_limit_source = r.speed_limit_source;
  s.road_attributes.speed_limit_ahead_kph = r.speed_limit_ahead_kph;
  s.road_attributes.speed_limit_ahead_distance_m = r.speed_limit_ahead_distance_m;
  s.road_attributes.road_class = r.road_class;
  s.road_attributes.ramp = r.road_edge_flags & 0x01;
  s.road_attributes.tunnel = r.road_edge_flags & 0x02;
  s.road_attributes.bridge = r.road_edge_flags & 0x04;
  s.road_attributes.roundabout = r.road_edge_flags & 0x08;
  s.road_attributes.toll = r.road_edge_flags & 0x10;
  s.road_attributes.oneway = r.road_edge_flags & 0x20;
  s.road_attributes.match_trusted = r.road_edge_flags & 0x80;
  s.road_attributes.lane_count = r.lane_count;
  s.road_attributes.match_distance_m =
      r.match_distance_dm == bgm1::kNavU16Invalid ? NAN : float(r.match_distance_dm) * kDmToM;
  s.road_attributes.age_ms = r.age_ms;

  const bgm1::CurveAhead& c = ext.curve_ahead;
  const uint8_t count = c.sample_count > bgm1::kCurveMaxSamples ? uint8_t(bgm1::kCurveMaxSamples) : c.sample_count;
  s.curve_ahead.sample_count = count;
  s.curve_ahead.spacing_m = c.spacing_m;
  s.curve_ahead.first_offset_m =
      c.first_offset_dm == bgm1::kCurveInvalidOffsetDm ? NAN : float(c.first_offset_dm) * kDmToM;
  if (count > 0 && c.spacing_m > 0 && c.first_offset_dm != bgm1::kCurveInvalidOffsetDm) {
    const double end_m = double(c.first_offset_dm) * 0.1 + double(count - 1) * double(c.spacing_m);
    const long clamped = std::clamp(std::lround(std::max(0.0, end_m)), 0L, 65534L);
    s.curve_ahead.horizon_distance_m = uint16_t(clamped);
  } else {
    s.curve_ahead.horizon_distance_m = 0;
  }
  s.curve_ahead.age_ms = c.age_ms;
  s.curve_ahead.along_route = c.curve_flags & 0x01;
  s.curve_ahead.road_lookahead = c.curve_flags & 0x02;
  s.curve_ahead.smoothed = c.curve_flags & 0x04;
  for (int i = 0; i < count; i++) {
    s.curve_ahead.curvatures_per_m[i] = c.curvature_per_m_x1e5[i] == bgm1::kCurveInvalidCurvature
                                            ? NAN
                                            : float(c.curvature_per_m_x1e5[i]) * kCurvatureX1e5ToPerM;
  }

  const bgm1::NavDebug& d = ext.nav_debug;
  s.nav_debug.render_duration_us = d.render_duration_us;
  s.nav_debug.map_match_duration_us = d.map_match_duration_us;
  s.nav_debug.route_compute_duration_ms = d.route_compute_duration_ms;
  s.nav_debug.nav_cpu_usage_percent = d.nav_cpu_usage_percent;
  return s;
}

void NavLinkCache::on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext) {
  last_reply_ns_ = reply_ns;
  t_eof_ = t_eof;
  present_flags_ = ext.present_flags;
  nav_health_flags_ = ext.nav_health_flags;
  curve_ = ext.curve_ahead;
  nav_state_age_ms_ = ext.nav_state.age_ms;
}

void NavLinkCache::reset() {
  last_reply_ns_ = 0;
  t_eof_ = 0;
  present_flags_ = 0;
  nav_health_flags_ = 0;
  curve_ = bgm1::CurveAhead{};
  nav_state_age_ms_ = bgm1::kNavU16Invalid;
}

bool MapTargetWriter::profile_usable(const bgm1::CurveAhead& c) {
  if (c.sample_count == 0 || c.sample_count > bgm1::kCurveMaxSamples) return false;
  if (c.spacing_m < 1 || c.spacing_m > 50) return false;
  if (c.first_offset_dm == bgm1::kCurveInvalidOffsetDm ||
      c.first_offset_dm < -1000 || c.first_offset_dm > 1000) {
    return false;
  }
  bool has_forward_sample = false;
  for (int i = 0; i < c.sample_count; i++) {
    if (c.curvature_per_m_x1e5[i] == bgm1::kCurveInvalidCurvature) return false;
    if (int32_t(c.first_offset_dm) + int32_t(i) * int32_t(c.spacing_m) * 10 >= 0) {
      has_forward_sample = true;
    }
  }
  return has_forward_sample;
}

bool MapTargetWriter::urgent_change(const MapTargetInput& in, int n) const {
  if (n == 0) return last_sample_count_ > 0;
  if (last_sample_count_ == 0) return true;
  if (in.curve.spacing_m != last_spacing_m_) return true;

  auto max_abs_profile = [](int count, const int16_t* k_x1e5) -> int {
    int m = 0;
    for (int i = 0; i < count; i++) {
      m = std::max(m, std::abs(int(k_x1e5[i])));
    }
    return m;
  };
  auto max_abs_near = [](int count, uint8_t spacing_m, int16_t first_offset_dm,
                         const int16_t* k_x1e5) -> int {
    int m = 0;
    for (int i = 0; i < count; i++) {
      const int32_t d_dm = int32_t(first_offset_dm) + int32_t(i) * int32_t(spacing_m) * 10;
      if (d_dm >= 0 && d_dm <= 1500) {
        m = std::max(m, std::abs(int(k_x1e5[i])));
      }
    }
    return m;
  };

  const int new_max_abs = max_abs_profile(n, in.curve.curvature_per_m_x1e5);
  const int old_max_abs = max_abs_profile(last_sample_count_, last_curvatures_x1e5_);
  if (new_max_abs >= old_max_abs + 5) return true;

  const int new_near_max_abs =
      max_abs_near(n, in.curve.spacing_m, in.curve.first_offset_dm, in.curve.curvature_per_m_x1e5);
  const int old_near_max_abs =
      max_abs_near(last_sample_count_, last_spacing_m_, last_first_offset_dm_, last_curvatures_x1e5_);
  if (old_near_max_abs >= new_near_max_abs + 10) return true;

  if (old_max_abs > 0 && new_max_abs == 0) return true;
  return false;
}

bool MapTargetWriter::same_profile(const MapTargetInput& in, int n) const {
  if (n != last_sample_count_) return false;
  if (n == 0) return true;
  if (in.curve.spacing_m != last_spacing_m_ || in.curve.first_offset_dm != last_first_offset_dm_) {
    return false;
  }
  for (int i = 0; i < n; i++) {
    if (in.curve.curvature_per_m_x1e5[i] != last_curvatures_x1e5_[i]) return false;
  }
  return true;
}

std::optional<std::string> MapTargetWriter::update(const MapTargetInput& in) {
  // t_eof 早于手机收齐本帧：只允许提前过期，绝不按 REPLY 到达时刻续命。
  auto sample_time = [&](uint16_t age_ms) -> uint64_t {
    const uint64_t age_ns = uint64_t(age_ms) * 1'000'000ULL;
    if (age_ms == bgm1::kNavU16Invalid || in.t_eof < age_ns || in.t_eof > in.now_ns) return 0;
    const uint64_t sample = in.t_eof - age_ns;
    if (sample > std::numeric_limits<uint64_t>::max() - kNavDataMaxAgeNs) return 0;
    return within(sample, in.now_ns, kNavDataMaxAgeNs) ? sample : 0;
  };
  const uint64_t curve_sample_ns = sample_time(in.curve.age_ms);
  const uint64_t phone_sample_ns = sample_time(in.nav_state_age_ms);
  const bool phone_ok = in.nav_health_flags & bgm1::kNavHealthPhoneLocationOk;
  const bool rerouting = in.nav_health_flags & bgm1::kNavHealthRerouting;
  const bool along_route = in.curve.curve_flags & 0x01;
  const bool route_ok = in.nav_health_flags & bgm1::kNavHealthRouteOk;
  const bool route_usable = !rerouting && (!along_route || route_ok);
  const bool curve_valid = in.link_connected && in.curve_present && in.nav_state_present && phone_ok &&
                           curve_sample_ns != 0 && phone_sample_ns != 0 && route_usable &&
                           profile_usable(in.curve);

  const int n = curve_valid ? int(in.curve.sample_count) : 0;
  const bool same = wrote_ && same_profile(in, n);
  const uint64_t since_last_write = in.now_ns > last_write_ns_ ? in.now_ns - last_write_ns_ : 0;
  if (same && since_last_write < kRefreshIntervalNs) return std::nullopt;

  const bool urgent = wrote_ && urgent_change(in, n);
  if (!same && !urgent && wrote_ && since_last_write < kMinChangedIntervalNs) {
    return std::nullopt;
  }

  std::string json = "[]";
  if (n > 0) {
    const uint64_t valid_until_ns = std::min(curve_sample_ns, phone_sample_ns) + kNavDataMaxAgeNs;
    json.clear();
    json.reserve(128 + size_t(n) * 10);
    char hdr[160];
    std::snprintf(hdr, sizeof hdr,
                  "{\"sample_time_ns\":%llu,\"valid_until_ns\":%llu,\"spacing_m\":%u,\"first_offset_m\":%.1f,\"curvatures\":[",
                  (unsigned long long)curve_sample_ns, (unsigned long long)valid_until_ns,
                  unsigned(in.curve.spacing_m), double(in.curve.first_offset_dm) * 0.1);
    json += hdr;
    for (int i = 0; i < n; i++) {
      if (i > 0) json += ',';
      append_curvature_json(&json, in.curve.curvature_per_m_x1e5[i]);
    }
    json += "]}";
  }

  wrote_ = true;
  last_write_ns_ = in.now_ns;
  last_sample_count_ = n;
  if (n > 0) {
    last_spacing_m_ = in.curve.spacing_m;
    last_first_offset_dm_ = in.curve.first_offset_dm;
    for (int i = 0; i < n; i++) {
      last_curvatures_x1e5_[i] = in.curve.curvature_per_m_x1e5[i];
    }
  } else {
    last_spacing_m_ = 0;
    last_first_offset_dm_ = 0;
  }
  return json;
}

void NavLinkSession::decide(bool link_connected, const NavLinkTick& tick) {
  MapTargetInput in;
  in.link_connected = link_connected;
  in.curve_present = cache_.present_flags() & bgm1::kNavPresentCurveAhead;
  in.curve = cache_.curve();
  in.nav_state_present = cache_.present_flags() & bgm1::kNavPresentNavState;
  in.nav_state_age_ms = cache_.nav_state_age_ms();
  in.nav_health_flags = cache_.nav_health_flags();
  in.last_reply_ns = cache_.last_reply_ns();
  // N7：t_eof 与 nanos_since_boot 同为 CLOCK_BOOTTIME（camerad spectra.cc 用
  // nanos_since_boot() − timestamp_eof 算 processing_time；SOF 时间戳来自内核）
  in.t_eof = cache_.t_eof();
  in.now_ns = tick.now_ns;
  if (auto json = writer_.update(in)) pending_ = std::move(json);
}

void NavLinkSession::on_disconnect(const NavLinkTick& tick) {
  // S2：断连即丢弃 NavExt 摘要；旧弯道点不得继续驱动纵向（MapTargetVelocities → []）
  hello_seen_ = false;
  cache_.reset();
  decide(/*link_connected=*/false, tick);
}

void NavLinkSession::on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext,
                              const NavLinkTick& tick) {
  cache_.on_reply(reply_ns, t_eof, ext);
  decide(/*link_connected=*/true, tick);
}

void NavLinkSession::on_idle(bool connected, const NavLinkTick& tick) {
  if (!connected) {
    on_disconnect(tick);  // 未连接/启动即清空：不留下上一趟或本趟崩溃前的弯道点
    return;
  }
  decide(/*link_connected=*/true, tick);
}

void clear_map_targets(const MapParamsOps& ops) {
  ops.put("MapTargetVelocities", "[]");
}
