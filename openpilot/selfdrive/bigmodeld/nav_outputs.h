#pragma once

// NavExt（REPLY 8304，304 B）→ 车机侧输出。纯逻辑、无 cereal / Params 依赖，宿主单测直接编译：
//   1. NavStateSnapshot：REPLY → navStateSP 的字段快照（main.cc 再逐字段写 capnp）。
//      CURVE_AHEAD 只含等距沿路线/道路曲率剖面（不含经纬度，决策 17）。
//   2. NavLinkCache：最近一条 REPLY 的 NavExt 摘要；断连必须 reset()，保证重连后第一条 REPLY
//      到达前按「无数据」处理（S2：旧弯道曲线不能被写回）。
//   3. MapTargetWriter：手机位置健康 + CURVE 沿路线距离 → 带本机期限的 Params envelope。
//      失效/删点/降速立即清空或更新，其余变化 ≤2 Hz，不变 1 Hz 刷新。
//   4. clear_map_targets()：退出/上路清理，由 main 注入阻塞 Params put。
//   5. NavLinkSession：reply 线程的链路状态（epoch / HELLO / NavLinkCache / MapTargetWriter）合成一个
//      类，main.cc 的每条断连 / 空闲 / REPLY 路径只调它（AU-1）。
//
// 单位口径：整数编码字段保留线上原值（单位即名字里的单位，无效哨兵原样保留）；定点字段换算成
// SI 浮点；浮点无效哨兵（0xFFFF）用 NaN。navStateSP 字段名与这里一一对应。

#include <cstdint>
#include <functional>
#include <optional>
#include <string>

#include "frame_codec.h"

// REPLY 的 NavExt 快照。has_* 来自 present_flags；四个子结构只在对应 has_* 为真时有意义
// （parse_reply 已把 present=0 的子块读作 0）。
struct NavStateSnapshot {
  uint32_t frame_index = 0;
  uint64_t timestamp_eof_ns = 0;
  bool phone_location_ok = false;
  bool route_ok = false;
  bool nav_ok = false;
  bool rerouting = false;
  bool has_nav_state = false;
  bool has_road_attributes = false;
  bool has_curve_ahead = false;
  bool has_nav_debug = false;

  struct NavStateFields {
    uint8_t status = 0;
    uint8_t maneuver_type = 0;
    uint8_t next_maneuver_type = 0;
    uint8_t roundabout_exit_index = 0;
    uint32_t distance_to_maneuver_m = 0;   // 0xFFFFFFFF 无效
    uint32_t route_remaining_m = 0;        // 0xFFFFFFFF 无效
    uint32_t route_remaining_seconds = 0;  // 0xFFFFFFFF 无效
    uint16_t age_ms = 0;                   // 0xFFFF 无效
    bool nav_map_fed = false;
    bool navigation_fed = false;
    bool on_route = false;
    uint8_t off_route_distance_m = 0;      // 255 无效
    float heading_deg = 0.f;               // NaN = 线上 0xFFFF 无效
  } nav_state;

  struct RoadAttributeFields {
    uint8_t speed_limit_kph = 0;
    uint8_t speed_limit_source = 0;
    uint8_t speed_limit_ahead_kph = 0;
    uint16_t speed_limit_ahead_distance_m = 0;  // 0xFFFF 无效
    uint8_t road_class = 0;
    bool ramp = false;
    bool tunnel = false;
    bool bridge = false;
    bool roundabout = false;
    bool toll = false;
    bool oneway = false;
    bool match_trusted = false;
    uint8_t lane_count = 0;
    float match_distance_m = 0.f;          // NaN = 线上 0xFFFF 未匹配
    uint16_t age_ms = 0;                   // 0xFFFF 无效
  } road_attributes;

  struct CurveAheadFields {
    uint8_t sample_count = 0;
    uint8_t spacing_m = 0;
    float first_offset_m = 0.f;           // NaN = 线上 INT16_MIN 无效
    uint16_t horizon_distance_m = 0;      // 派生可用前方距离 m
    uint16_t age_ms = 0;                  // 0xFFFF 无效
    bool along_route = false;             // curve_flags bit0：沿路线取
    bool road_lookahead = false;          // bit1：沿当前道路前扫
    bool smoothed = false;                // bit2：曲率已平滑（0 = 原始曲率）
    float curvatures_per_m[bgm1::kCurveMaxSamples] = {};  // 1/m；NaN = INT16_MIN 无效
  } curve_ahead;

  struct NavDebugFields {
    uint16_t render_duration_us = 0;       // 0xFFFF 无效（饱和 65534）
    uint16_t map_match_duration_us = 0;    // 0xFFFF 无效
    uint16_t route_compute_duration_ms = 0;  // 0xFFFF 无效
    uint8_t nav_cpu_usage_percent = 0;     // 254 饱和，255 无效
  } nav_debug;
};

NavStateSnapshot nav_state_snapshot_from_reply(const bgm1::Reply& reply);

// reply 线程的连接代号跟踪：sender->conn_epoch() 变化（drop 后已重连）时返回 true，
// 调用方据此复位 hello_seen_ / framer / NavLinkCache（R1）。首次调用也返回 true（启动清残留）。
class ConnEpochTracker {
 public:
  bool update(uint32_t epoch) {
    if (!initialized_ || epoch != epoch_) {
      initialized_ = true;
      epoch_ = epoch;
      return true;
    }
    return false;
  }

 private:
  bool initialized_ = false;
  uint32_t epoch_ = 0;
};

// 最近一条 REPLY 的 NavExt 摘要（reply 线程独占）。reset() 后 present=0 / reply_ns=0，
// 重连窗口内的空闲拍不会拿旧数据写回 Params。
class NavLinkCache {
 public:
  // t_eof = REPLY 回显的帧 timestamp_eof（与 nanos_since_boot 同时基，见 main.cc 注释）
  void on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext);
  void reset();
  uint64_t last_reply_ns() const { return last_reply_ns_; }
  uint64_t t_eof() const { return t_eof_; }
  uint8_t present_flags() const { return present_flags_; }
  uint8_t nav_health_flags() const { return nav_health_flags_; }
  const bgm1::CurveAhead& curve() const { return curve_; }
  uint16_t nav_state_age_ms() const { return nav_state_age_ms_; }

 private:
  uint64_t last_reply_ns_ = 0;
  uint64_t t_eof_ = 0;
  uint8_t present_flags_ = 0;
  uint8_t nav_health_flags_ = 0;
  bgm1::CurveAhead curve_;
  uint16_t nav_state_age_ms_ = bgm1::kNavU16Invalid;
};

constexpr uint64_t kNavDataMaxAgeNs = 2'000'000'000;  // 手机采样与距离结果各自的期限

// 手机 NAV_STATE 和 CURVE 的年龄独立判期；时间基准为回显的 C4 t_eof。
struct MapTargetInput {
  bool link_connected = false;
  bool curve_present = false;
  bool nav_state_present = false;
  uint16_t nav_state_age_ms = bgm1::kNavU16Invalid;
  bgm1::CurveAhead curve;
  uint8_t nav_health_flags = 0;  // REPLY 的 nav_health_flags（AU-4：rerouting / route_ok）
  uint64_t last_reply_ns = 0;  // 仅用于事实记录；数据新鲜度按 t_eof − age_ms（N7）
  uint64_t t_eof = 0;          // REPLY 回显的帧 timestamp_eof（ns，同 nanos_since_boot）
  uint64_t now_ns = 0;
};

// 写 MapTargetVelocities 的时机：
//   - 清空 / spacing 变化 / 最大曲率增大 ≥5e-5 / 近场 [0,150 m] 最大曲率下降 ≥1e-4 /
//     整体最大曲率降回 0 → 下一拍立即写（≤1 个 calib 拍：正常 ≤50 ms）。
//   - 其他内容变化（首样偏移推进 / 小幅曲率更新）：≥500 ms（≤2 Hz）。
//   - 内容不变：≥1 s 刷新（有意为之：可自愈被外部改写的值）。
//   - 首次调用必写（清掉上趟进程遗留的弯道数据）。
//   - NAV_STATE/CURVE 缺失、phone_location_ok=0、任一年龄未知/过期或断连 → []。
//   - rerouting 或沿路线但 route_ok=0 → []；要求合法间距/首偏移/无无效采样且至少有一前向采样。
class MapTargetWriter {
 public:
  // 返回 nullopt = 本次不写。
  std::optional<std::string> update(const MapTargetInput& in);

 private:
  static constexpr uint64_t kMinChangedIntervalNs = 500'000'000;  // 2 Hz 上限
  static constexpr uint64_t kRefreshIntervalNs = 1'000'000'000;   // 不变也 1 Hz 刷新

  static bool profile_usable(const bgm1::CurveAhead& c);
  bool same_profile(const MapTargetInput& in, int n) const;
  bool urgent_change(const MapTargetInput& in, int n) const;

  bool wrote_ = false;
  uint64_t last_write_ns_ = 0;
  int last_sample_count_ = 0;
  uint8_t last_spacing_m_ = 0;
  int16_t last_first_offset_dm_ = 0;
  int16_t last_curvatures_x1e5_[bgm1::kCurveMaxSamples] = {};
};

struct NavLinkTick {
  uint64_t now_ns = 0;
};

// AU-1：reply 线程的 NavExt 链路状态（epoch 跟踪 + NavExt 摘要 + 弯道目标 writer + HELLO 标志）。
// main.cc 的 fail / epoch 变化 / 未连接 / 空闲拍 / REPLY / ERR 只调这里的方法，再把 take_pending()
// 交给 calib 线程写 Params；宿主单测驱动同一个类，不再手抄 main 的接线。
// 每个方法内部决定「复位 + 以 link=false 走 writer」，保证 fail/ERR/换连接/未连接都立即得到 "[]"。
// reply 线程独占，无锁。
class NavLinkSession {
 public:
  // epoch 变化（首次调用也算）→ 复位并返回 true；调用方据此 framer.clear()。
  // make_tick 仅在 epoch 变化时取当前车机 BOOTTIME。
  template <class MakeTick>
  bool on_epoch(uint32_t epoch, MakeTick&& make_tick) {
    if (!epoch_.update(epoch)) return false;
    on_disconnect(make_tick());
    return true;
  }
  // fail / ERR / 未连接：HELLO 标志、NavExt 摘要全部复位，并以 link=false 走 writer。
  void on_disconnect(const NavLinkTick& tick);
  void on_hello_ok() { hello_seen_ = true; }
  bool hello_seen() const { return hello_seen_; }
  // 成功解析一条 REPLY：reply_ns 为到达时刻，t_eof 为 REPLY 回显的帧时刻。
  void on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext, const NavLinkTick& tick);
  // 没有新 REPLY 的拍：手机位置 / CURVE 过期时清掉旧弯道点。
  void on_idle(bool connected, const NavLinkTick& tick);
  // 取走待写的 MapTargetVelocities（只有最后一次决策有意义，取走即空）。
  std::optional<std::string> take_pending() {
    std::optional<std::string> out;
    out.swap(pending_);
    return out;
  }

 private:
  void decide(bool link_connected, const NavLinkTick& tick);

  ConnEpochTracker epoch_;
  NavLinkCache cache_;
  MapTargetWriter writer_;
  bool hello_seen_ = false;
  std::optional<std::string> pending_;
};

// 退出清理 / 上路清理的 Params 操作（main 注入：mem_params.put，阻塞调用）。
struct MapParamsOps {
  std::function<void(const char*, const std::string&)> put;
};
// MapTargetVelocities = "[]"。
void clear_map_targets(const MapParamsOps& ops);
