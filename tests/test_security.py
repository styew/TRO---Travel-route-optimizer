"""Dependency-free checks for the lightweight public-search safeguards."""

import unittest
from unittest.mock import patch

from backend.security import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    """Verify that a client cannot exceed its configured one-minute budget."""

    def test_rejects_the_next_request_after_the_limit(self) -> None:
        """The configured request cap is enforced for the same client key."""
        limiter = SlidingWindowRateLimiter(2)
        with patch("backend.security.monotonic", side_effect=[0, 1, 2]):
            self.assertTrue(limiter.allowed("client"))
            self.assertTrue(limiter.allowed("client"))
            self.assertFalse(limiter.allowed("client"))

    def test_accepts_a_request_after_the_one_minute_window_expires(self) -> None:
        """Expired entries leave the rolling window and free the budget again."""
        limiter = SlidingWindowRateLimiter(1)
        with patch("backend.security.monotonic", side_effect=[0, 61]):
            self.assertTrue(limiter.allowed("client"))
            self.assertTrue(limiter.allowed("client"))


if __name__ == "__main__":
    unittest.main()
