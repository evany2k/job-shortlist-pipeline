"""Request pacing and rate-limit handling shared by the LinkedIn clients."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

BLOCKED_STATUS = {403, 999}  # 999 is LinkedIn's anti-bot response


class RateLimitExceeded(Exception):
    """Raised when LinkedIn keeps rate limiting and the run should stop."""


class Blocked(Exception):
    """Raised when a response means further requests are pointless (403/999)."""


def raise_if_blocked(status_code: int | None) -> None:
    if status_code in BLOCKED_STATUS:
        raise Blocked(f"LinkedIn returned {status_code}; stopping to avoid a ban")


@dataclass
class ThrottleSettings:
    """Delay ranges in seconds, as [min, max]."""

    fast_delay: tuple[float, float] = (30, 45)
    fast_long_pause: tuple[float, float] = (60, 120)
    slow_delay: tuple[float, float] = (90, 150)  # used after any rate limit
    slow_long_pause: tuple[float, float] = (250, 400)
    long_pause_every: int = 5
    rate_limit_sleep: float = 300
    max_consecutive_rate_limits: int = 2
    max_total_rate_limits: int = 4


class Throttle:
    """Randomised delays between requests that slow down after any HTTP 429."""

    def __init__(
        self,
        settings: ThrottleSettings | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or ThrottleSettings()
        self._sleep = sleep
        self.requests = 0
        self.consecutive_rate_limits = 0
        self.total_rate_limits = 0

    @property
    def slowed_down(self) -> bool:
        return self.total_rate_limits > 0

    def next_delay(self) -> float:
        s = self.settings
        long_pause = self.requests % s.long_pause_every == 0
        if self.slowed_down:
            low, high = s.slow_long_pause if long_pause else s.slow_delay
        else:
            low, high = s.fast_long_pause if long_pause else s.fast_delay
        return random.uniform(low, high)

    def wait(self) -> None:
        """Sleep before a request. The first request of a run goes immediately."""
        if self.requests:
            delay = self.next_delay()
            log.info("Waiting %.1fs", delay)
            self._sleep(delay)
        self.requests += 1

    def pause(self, seconds: float, reason: str) -> None:
        log.info("%s; sleeping %.0fs", reason, seconds)
        self._sleep(seconds)

    def record_success(self) -> None:
        self.consecutive_rate_limits = 0

    def record_rate_limit(self) -> None:
        """Register a 429 and sleep it off, or raise if the limits are exceeded."""
        s = self.settings
        self.consecutive_rate_limits += 1
        self.total_rate_limits += 1
        if self.consecutive_rate_limits >= s.max_consecutive_rate_limits:
            raise RateLimitExceeded(f"{self.consecutive_rate_limits} consecutive rate limits")
        if self.total_rate_limits >= s.max_total_rate_limits:
            raise RateLimitExceeded(f"{self.total_rate_limits} total rate limits")
        self.pause(s.rate_limit_sleep, "Rate limited (HTTP 429), switching to slow mode")
