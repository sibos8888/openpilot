#include "common/params.h"

#include <dirent.h>
#include <sys/file.h>

#include <algorithm>
#include <csignal>
#include <unordered_map>

#include "common/params_keys.h"
#include "common/swaglog.h"
#include "common/util.h"
#include "common/hardware/hw.h"

namespace {

const ParamKeyAttributes &lookup(const std::string &key) {
  static const ParamKeyAttributes unknown{0, STRING};
  auto it = keys.find(key);
  return it != keys.end() ? it->second : unknown;
}

volatile sig_atomic_t params_do_exit = 0;
void params_sig_handler(int signal) {
  params_do_exit = 1;
}

int fsync_dir(const std::string &path) {
  int result = -1;
  int fd = HANDLE_EINTR(open(path.c_str(), O_RDONLY, 0755));
  if (fd >= 0) {
    result = HANDLE_EINTR(fsync(fd));
    HANDLE_EINTR(close(fd));
  }
  return result;
}

bool create_params_path(const std::string &param_path, const std::string &key_path) {
  // Make sure params path exists
  if (!util::file_exists(param_path) && !util::create_directories(param_path, 0775)) {
    return false;
  }

  if (!util::file_exists(key_path)) {
    std::string tmp_path = param_path + "/.tmp_XXXXXX";
    char *tmp_dir = mkdtemp(tmp_path.data());
    if (tmp_dir == nullptr) {
      return false;
    }

    // Publishing directly cannot replace another initializer's directory.
    if (symlink(tmp_dir, key_path.c_str()) != 0) {
      int error = errno;
      rmdir(tmp_dir);
      errno = error;
      return error == EEXIST;
    }
  }

  return true;
}

std::string ensure_params_path(const std::string &prefix, const std::string &path = {}) {
  std::string params_path = path.empty() ? Path::params() : path;
  if (!create_params_path(params_path, params_path + prefix)) {
    throw std::runtime_error(util::string_format(
        "Failed to ensure params path, errno=%d, path=%s, param_prefix=%s",
        errno, params_path.c_str(), prefix.c_str()));
  }
  return params_path;
}

class FileLock {
public:
  FileLock(const std::string &fn) {
    fd_ = HANDLE_EINTR(open(fn.c_str(), O_CREAT, 0775));
    if (fd_ < 0 || HANDLE_EINTR(flock(fd_, LOCK_EX)) < 0) {
      LOGE("Failed to lock file %s, errno=%d", fn.c_str(), errno);
    }
  }
  ~FileLock() { close(fd_); }

private:
  int fd_ = -1;
};

} // namespace


Params::Params(const std::string &path) {
  params_prefix = "/" + util::getenv("OPENPILOT_PREFIX", "d");
  params_path = ensure_params_path(params_prefix, path);
}

Params::~Params() {
  {
    std::lock_guard lock(queue_mutex);
    stopping = true;
  }
  queue_cv.notify_one();
  if (worker.joinable()) {
    worker.join();
  }
}

std::vector<std::string> Params::allKeys(ParamKeyFlag flag) const {
  std::vector<std::string> ret;
  for (auto &p : keys) {
    if (flag == ALL || (p.second.flags & flag)) {
      ret.push_back(p.first);
    }
  }
  return ret;
}

bool Params::checkKey(const std::string &key) {
  return keys.find(key) != keys.end();
}

ParamKeyFlag Params::getKeyFlag(const std::string &key) {
  return static_cast<ParamKeyFlag>(lookup(key).flags);
}

ParamKeyType Params::getKeyType(const std::string &key) {
  return lookup(key).type;
}

std::optional<std::string> Params::getKeyDefaultValue(const std::string &key) {
  return lookup(key).default_value;
}

int Params::put(const char* key, const char* value, size_t value_size) {
  // Information about safely and atomically writing a file: https://lwn.net/Articles/457667/
  // 1) Create temp file
  // 2) Write data to temp file
  // 3) fsync() the temp file
  // 4) rename the temp file to the real name
  // 5) fsync() the containing directory
  std::string tmp_path = params_path + "/.tmp_value_XXXXXX";
  int tmp_fd = mkstemp((char*)tmp_path.c_str());
  if (tmp_fd < 0) return -1;

  int result = -1;
  do {
    // Write value to temp.
    ssize_t bytes_written = HANDLE_EINTR(write(tmp_fd, value, value_size));
    if (bytes_written < 0 || (size_t)bytes_written != value_size) {
      result = -20;
      break;
    }

    // fsync to force persist the changes.
    if ((result = HANDLE_EINTR(fsync(tmp_fd))) < 0) break;

    FileLock file_lock(params_path + "/.lock");

    // Move temp into place.
    if ((result = rename(tmp_path.c_str(), getParamPath(key).c_str())) < 0) break;

    // fsync parent directory
    result = fsync_dir(getParamPath());
  } while (false);

  close(tmp_fd);
  if (result != 0) {
    ::unlink(tmp_path.c_str());
  }
  return result;
}

int Params::remove(const std::string &key) {
  FileLock file_lock(params_path + "/.lock");
  int result = unlink(getParamPath(key).c_str());
  if (result != 0) {
    return result;
  }
  return fsync_dir(getParamPath());
}

std::string Params::get(const std::string &key, bool block) {
  if (!block) {
    return util::read_file(getParamPath(key));
  } else {
    // blocking read until successful
    params_do_exit = 0;
    void (*prev_handler_sigint)(int) = std::signal(SIGINT, params_sig_handler);
    void (*prev_handler_sigterm)(int) = std::signal(SIGTERM, params_sig_handler);

    std::string value;
    while (!params_do_exit) {
      if (value = util::read_file(getParamPath(key)); !value.empty()) {
        break;
      }
      util::sleep_for(100);  // 0.1 s
    }

    std::signal(SIGINT, prev_handler_sigint);
    std::signal(SIGTERM, prev_handler_sigterm);
    return value;
  }
}

std::map<std::string, std::string> Params::readAll() {
  FileLock file_lock(params_path + "/.lock");
  return util::read_files_in_dir(getParamPath());
}

void Params::clearAll(ParamKeyFlag key_flag) {
  FileLock file_lock(params_path + "/.lock");

  // 1) delete params of key_flag
  // 2) delete files that are not defined in the keys.
  if (DIR *d = opendir(getParamPath().c_str())) {
    struct dirent *de = NULL;
    while ((de = readdir(d))) {
      if (de->d_type != DT_DIR) {
        auto it = keys.find(de->d_name);
        if (it == keys.end() || (it->second.flags & key_flag)) {
          unlink(getParamPath(de->d_name).c_str());
        }
      }
    }
    closedir(d);
  }

  fsync_dir(getParamPath());
}

void Params::putNonBlocking(const std::string &key, const std::string &val) {
  {
    std::lock_guard lock(queue_mutex);
    if (!worker.joinable()) {
      worker = std::thread(&Params::asyncWriteThread, this);
    }
    queue.emplace(key, val);
  }
  queue_cv.notify_one();
}

void Params::asyncWriteThread() {
  std::unique_lock lock(queue_mutex);
  while (true) {
    queue_cv.wait(lock, [this] { return stopping || !queue.empty(); });
    if (queue.empty()) {
      return;
    }
    auto item = std::move(queue.front());
    queue.pop();
    lock.unlock();
    put(item.first, item.second);
    lock.lock();
  }
}
