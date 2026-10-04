"""
SessionManager — управление параллельными звонками.

Отвечает за:
- Лимит одновременных звонков (MAX_CONCURRENT_CALLS)
- Учёт активных сессий
- Метрики (для health-чека)
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active: dict[str, asyncio.Task] = {}
        self._stats = {
            "total_calls": 0,
            "active_calls": 0,
            "rejected_calls": 0,
            "avg_duration_sec": 0.0,
        }
        self._durations: list[float] = []

    @property
    def active_count(self) -> int:
        return len(self._active)

    def can_accept(self) -> bool:
        return self.active_count < self.max_concurrent

    async def start_session(self, call_uuid: str, coro) -> bool:
        """
        Try to start a new call session.
        Returns True if accepted, False if capacity full.
        """
        if not self.can_accept():
            self._stats["rejected_calls"] += 1
            logger.warning("Rejected call %s: capacity full (%d/%d)",
                           call_uuid, self.active_count, self.max_concurrent)
            return False

        self._stats["total_calls"] += 1
        self._stats["active_calls"] = self.active_count + 1

        start_time = time.monotonic()

        async def _wrapper():
            try:
                async with self._semaphore:
                    await coro
            finally:
                duration = time.monotonic() - start_time
                self._durations.append(duration)
                if len(self._durations) > 1000:
                    self._durations = self._durations[-500:]
                self._stats["avg_duration_sec"] = sum(self._durations) / len(self._durations)
                self._active.pop(call_uuid, None)
                self._stats["active_calls"] = self.active_count
                logger.info("Call %s ended, duration=%.1fs, active=%d",
                            call_uuid, duration, self.active_count)

        task = asyncio.create_task(_wrapper())
        self._active[call_uuid] = task
        return True

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "active_calls": self.active_count,
            "max_concurrent": self.max_concurrent,
            "active_uuids": list(self._active.keys()),
        }

    async def shutdown(self):
        """Cancel all active sessions."""
        for uuid, task in list(self._active.items()):
            task.cancel()
        if self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)
        self._active.clear()
