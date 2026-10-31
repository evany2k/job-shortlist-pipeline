import unittest

from job_match.throttle import (
    Blocked,
    RateLimitExceeded,
    Throttle,
    ThrottleSettings,
    raise_if_blocked,
)

SETTINGS = ThrottleSettings(
    fast_delay=(1, 1), fast_long_pause=(2, 2), slow_delay=(3, 3), slow_long_pause=(4, 4),
    long_pause_every=3, rate_limit_sleep=100,
)


class ThrottleTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.throttle = Throttle(SETTINGS, sleep=self.sleeps.append)

    def test_first_request_is_immediate_then_paced(self):
        for _ in range(7):
            self.throttle.wait()
        # long pause after every 3rd request
        self.assertEqual(self.sleeps, [1, 1, 2, 1, 1, 2])

    def test_slows_down_after_rate_limit(self):
        self.throttle.wait()
        self.throttle.record_rate_limit()
        self.throttle.wait()
        self.assertEqual(self.sleeps, [100, 3])

    def test_consecutive_rate_limits_abort(self):
        self.throttle.record_rate_limit()
        with self.assertRaises(RateLimitExceeded):
            self.throttle.record_rate_limit()

    def test_total_rate_limits_abort(self):
        for _ in range(3):
            self.throttle.record_rate_limit()
            self.throttle.record_success()
        with self.assertRaises(RateLimitExceeded):
            self.throttle.record_rate_limit()

    def test_blocked_statuses(self):
        for code in (403, 999):
            with self.assertRaises(Blocked):
                raise_if_blocked(code)
        raise_if_blocked(200)
        raise_if_blocked(None)


if __name__ == "__main__":
    unittest.main()
