#include <limits>

#include "common/tests/native_test.h"
#include "sunnypilot/selfdrive/locationd/locationd.h"

int main() {
  return run_native_test([] {
    Localizer localizer;
    const Eigen::VectorXd initial_state = localizer.get_state();
    const Eigen::VectorXd initial_std = localizer.get_stdev();
    const MatrixXdr covariance = initial_std.array().square().matrix().asDiagonal();

    Eigen::VectorXd finite_state = initial_state;
    finite_state[STATE_ECEF_POS_START] += 10.0;
    const MatrixXdr finite_covariance = covariance * 4.0;
    localizer.reset_kalman(1.0, finite_state, finite_covariance);
    localizer.finite_check(2.0);
    CHECK(localizer.get_state().isApprox(finite_state));
    CHECK(localizer.get_stdev().isApprox(initial_std * 2.0));

    for (double invalid : {std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::infinity()}) {
      for (int invalid_part : {0, 1, 2}) {
        Eigen::VectorXd state = finite_state;
        MatrixXdr cov = finite_covariance;
        if (invalid_part != 1) state[STATE_ECEF_POS_START] = invalid;
        if (invalid_part != 0) cov(0, 0) = invalid;
        localizer.reset_kalman(3.0, state, cov);
        localizer.finite_check(4.0);
        CHECK(localizer.get_state().allFinite());
        CHECK(localizer.get_stdev().allFinite());
        CHECK(localizer.get_state().isApprox(initial_state));
        CHECK(localizer.get_stdev().isApprox(initial_std));
      }
    }
  });
}
