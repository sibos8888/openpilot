#pragma once

#include <cstdlib>
#include <string>

inline int select_bitrate(const std::string& param, int cli_default) {
  const int value = std::atoi(param.c_str());
  return value > 0 ? value : cli_default;
}
