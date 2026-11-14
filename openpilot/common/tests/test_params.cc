#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <future>
#include <iterator>
#include <poll.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <system_error>
#include <unistd.h>

#ifdef __APPLE__
#include <mach/mach.h>
#endif

#include "common/params.h"
#include "common/tests/native_test.h"
#include "common/util.h"

namespace {
int init_ready_fd = -1;
int init_release_fd = -1;
constexpr bool mkdtemp_is_noexcept = noexcept(::mkdtemp(std::declval<char *>()));
}

extern "C" char *mkdtemp(char *path) noexcept(mkdtemp_is_noexcept) {
  static const auto real_mkdtemp = reinterpret_cast<decltype(&mkdtemp)>(dlsym(RTLD_NEXT, "mkdtemp"));
  if (real_mkdtemp == nullptr) {
    errno = ENOSYS;
    return nullptr;
  }
  char *result = real_mkdtemp(path);
  if (result != nullptr && init_ready_fd >= 0) {
    char token = 'r';
    if (write(init_ready_fd, &token, 1) != 1 || read(init_release_fd, &token, 1) != 1) {
      _exit(2);
    }
  }
  return result;
}

namespace {
struct TempDirectory {
  char path[32] = "/tmp/test_params_XXXXXX";

  TempDirectory() { CHECK(mkdtemp(path) != nullptr); }
  ~TempDirectory() {
    std::error_code error;
    std::filesystem::remove_all(path, error);
  }
};

void test_unknown_metadata() {
  TempDirectory directory;
  Params params(directory.path);
  const std::string unknown = "UnregisteredMetadataTest";
  CHECK(!params.checkKey(unknown));
  CHECK(params.put(unknown, "orphan") == 0);
  CHECK(params.put("DongleId", "known") == 0);

  CHECK(params.getKeyFlag(unknown) == 0);
  CHECK(!params.checkKey(unknown));
  CHECK(params.getKeyType(unknown) == STRING);
  CHECK(!params.checkKey(unknown));
  CHECK(!params.getKeyDefaultValue(unknown).has_value());
  CHECK(!params.checkKey(unknown));
  CHECK(params.get(unknown) == "orphan");

  params.clearAll(CLEAR_ON_MANAGER_START);
  CHECK(!std::filesystem::exists(params.getParamPath(unknown)));
  CHECK(params.get("DongleId") == "known");
}

size_t thread_count() {
#ifdef __APPLE__
  thread_act_array_t threads;
  mach_msg_type_number_t count;
  CHECK(task_threads(mach_task_self(), &threads, &count) == KERN_SUCCESS);
  for (mach_msg_type_number_t i = 0; i < count; ++i) {
    mach_port_deallocate(mach_task_self(), threads[i]);
  }
  vm_deallocate(mach_task_self(), reinterpret_cast<vm_address_t>(threads), count * sizeof(thread_t));
  return count;
#else
  return std::distance(std::filesystem::directory_iterator("/proc/self/task"), std::filesystem::directory_iterator{});
#endif
}

void test_sync_params_do_not_start_worker() {
  TempDirectory directory;
  const size_t before = thread_count();
  {
    Params params(directory.path);
    CHECK(thread_count() == before);
    CHECK(params.put("DongleId", "synchronous") == 0);
    CHECK(params.get("DongleId") == "synchronous");
    CHECK(thread_count() == before);
  }
  CHECK(thread_count() == before);
}

void test_async_producers_and_destructor_drain() {
  TempDirectory directory;
  constexpr int producer_count = 4;
  constexpr int writes_per_producer = 8;
  {
    Params params(directory.path);
    std::vector<std::future<void>> producers;
    std::promise<void> start;
    auto ready = start.get_future().share();
    unique_fd file_lock(open((std::string(directory.path) + "/.lock").c_str(), O_CREAT | O_RDWR, 0600));
    CHECK(file_lock >= 0);
    CHECK(flock(file_lock, LOCK_EX) == 0);
    for (int producer = 0; producer < producer_count; ++producer) {
      producers.emplace_back(std::async(std::launch::async, [&, producer, ready] {
        ready.wait();
        for (int i = 0; i < writes_per_producer; ++i) {
          const std::string key = "Producer" + std::to_string(producer) + "Key" + std::to_string(i);
          params.putNonBlocking(key, std::to_string(i));
          params.putNonBlocking("Latest" + std::to_string(producer), std::to_string(i));
        }
      }));
    }
    start.set_value();
    bool enqueued_while_io_blocked = true;
    for (auto &producer : producers) {
      enqueued_while_io_blocked &= producer.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
    }
    CHECK(flock(file_lock, LOCK_UN) == 0);
    for (auto &producer : producers) {
      producer.get();
    }
    CHECK(enqueued_while_io_blocked);
  }
  Params reader(directory.path);
  for (int producer = 0; producer < producer_count; ++producer) {
    for (int i = 0; i < writes_per_producer; ++i) {
      const std::string key = "Producer" + std::to_string(producer) + "Key" + std::to_string(i);
      CHECK(reader.get(key) == std::to_string(i));
    }
    CHECK(reader.get("Latest" + std::to_string(producer)) == std::to_string(writes_per_producer - 1));
  }
}

struct Pipe {
  int ends[2];
  Pipe() { CHECK(pipe(ends) == 0); }
  ~Pipe() {
    close(ends[0]);
    close(ends[1]);
  }
};

void receive_token(int fd) {
  pollfd descriptor{fd, POLLIN, 0};
  CHECK(poll(&descriptor, 1, 5000) == 1);
  char token;
  CHECK(read(fd, &token, 1) == 1);
}

struct Initializer {
  Pipe ready;
  Pipe release;
  pid_t pid;

  Initializer(const char *path, const char *key) : pid(fork()) {
    CHECK(pid >= 0);
    if (pid == 0) {
      alarm(10);
      init_ready_fd = ready.ends[1];
      init_release_fd = release.ends[0];
      int status = run_native_test([&] {
        Params params(path);
        CHECK(params.put(key, key) == 0);
      });
      char token = 'd';
      if (write(ready.ends[1], &token, 1) != 1) _exit(2);
      _exit(status);
    }
  }

  ~Initializer() {
    if (pid > 0) {
      kill(pid, SIGKILL);
      waitpid(pid, nullptr, 0);
    }
  }

  void finish() {
    char token = 'g';
    CHECK(write(release.ends[1], &token, 1) == 1);
    receive_token(ready.ends[0]);
    int status;
    CHECK(waitpid(pid, &status, 0) == pid);
    pid = -1;
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 0);
  }
};

void test_concurrent_initialization_keeps_both_writes() {
  TempDirectory directory;
  Initializer first(directory.path, "FirstInitializer");
  receive_token(first.ready.ends[0]);
  Initializer second(directory.path, "SecondInitializer");
  receive_token(second.ready.ends[0]);
  first.finish();
  Params reader(directory.path);
  const auto published = std::filesystem::read_symlink(reader.getParamPath());
  CHECK(reader.get("FirstInitializer") == "FirstInitializer");
  second.finish();
  CHECK(std::filesystem::read_symlink(reader.getParamPath()) == published);
  CHECK(reader.get("FirstInitializer") == "FirstInitializer");
  CHECK(reader.get("SecondInitializer") == "SecondInitializer");
  size_t temporary_directories = 0;
  for (const auto &entry : std::filesystem::directory_iterator(directory.path)) {
    if (entry.path().filename().string().rfind(".tmp_", 0) == 0) {
      ++temporary_directories;
    }
  }
  CHECK(temporary_directories == 1);
}
}

int main() {
  return run_native_test([] {
    test_concurrent_initialization_keeps_both_writes();
    test_sync_params_do_not_start_worker();
    test_async_producers_and_destructor_drain();
    test_unknown_metadata();
  });
}
