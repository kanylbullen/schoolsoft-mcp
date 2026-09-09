"""A per-process cache for the plannings grid and planning bodies.

A subject planning is the same for weeks: a term plan for Idrott is
published in August and read every school day until December. Yet every
question about a day fetched the whole plannings grid and then one body
per subject on the timetable, and a consumer that keeps this server alive
all day asked the same questions at 07:00, on demand, and again at 18:30.

SchoolSoft gives nothing to validate against: no ETag, no Last-Modified,
and a conditional GET is answered 406. So the cache keys on the only cheap
freshness signal there is — the planning's row in the grid — and backs
that with a TTL:

- The **grid** (one request, every planning with its dates, publish date,
  status and read flag) is cached per child for ``grid_ttl`` seconds,
  long enough to absorb a burst of calls about one day, short enough that
  a newly published planning shows up within minutes.
- A **body** is cached per child and part, tagged with a *fingerprint* of
  its grid row. A body is served from cache only while the row still
  fingerprints the same: a re-published part moves ``publishDate``, an
  edited one is marked unread again, a re-dated one moves its bounds. Any
  of those refetches. None of them is a guarantee, which is why bodies
  also expire after ``body_ttl`` seconds.

What is cached is the **raw** response, not the parsed view. Parsing
depends on which week was asked about and how many characters the caller
wants, and both vary per call; the HTTP round trip is the cost.

The cache lives on the app context, so it lives exactly as long as the
server process. A one-shot consumer that builds its own context — the
nightly page writer — starts cold and fetches fresh, which is the right
behaviour for the run that has to see yesterday's edits.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Settings

DEFAULT_GRID_TTL = 600.0  # ten minutes
DEFAULT_BODY_TTL = 6 * 3600.0  # six hours


@dataclass(slots=True)
class _Entry:
    value: Any
    stored_at: float
    fingerprint: Hashable | None = None


@dataclass(slots=True)
class CacheStats:
    grid_hits: int = 0
    grid_misses: int = 0
    body_hits: int = 0
    body_misses: int = 0


class PlanningCache:
    """Grid and body cache keyed by the child the session is pointed at."""

    def __init__(
        self,
        *,
        grid_ttl: float = DEFAULT_GRID_TTL,
        body_ttl: float = DEFAULT_BODY_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grid_ttl = max(0.0, float(grid_ttl))
        self._body_ttl = max(0.0, float(body_ttl))
        self._clock = clock
        self._grids: dict[int | None, _Entry] = {}
        self._bodies: dict[tuple[int | None, int], _Entry] = {}
        self.stats = CacheStats()

    @classmethod
    def from_settings(cls, settings: Settings) -> PlanningCache:
        return cls(grid_ttl=settings.cache_grid_ttl, body_ttl=settings.cache_body_ttl)

    @property
    def enabled(self) -> bool:
        return self._grid_ttl > 0 or self._body_ttl > 0

    def _fresh(self, entry: _Entry, ttl: float) -> bool:
        return ttl > 0 and (self._clock() - entry.stored_at) < ttl

    # -- grid ---------------------------------------------------------------

    def get_grid(self, student: int | None) -> Any | None:
        entry = self._grids.get(student)
        if entry is None or not self._fresh(entry, self._grid_ttl):
            self.stats.grid_misses += 1
            return None
        self.stats.grid_hits += 1
        return entry.value

    def put_grid(self, student: int | None, rows: Any) -> None:
        if self._grid_ttl <= 0:
            return
        self._grids[student] = _Entry(value=rows, stored_at=self._clock())

    # -- bodies -------------------------------------------------------------

    def get_body(
        self, student: int | None, part_id: int, fingerprint: Hashable | None
    ) -> Any | None:
        """The cached raw view, or None when absent, expired or changed.

        A ``fingerprint`` that differs from the one stored means the grid
        row moved under the body; the entry is dropped rather than served.
        A caller with no fingerprint to offer gets whatever is fresh.
        """
        key = (student, part_id)
        entry = self._bodies.get(key)
        if entry is None or not self._fresh(entry, self._body_ttl):
            self.stats.body_misses += 1
            return None
        if fingerprint is not None and entry.fingerprint != fingerprint:
            del self._bodies[key]
            self.stats.body_misses += 1
            return None
        self.stats.body_hits += 1
        return entry.value

    def put_body(
        self,
        student: int | None,
        part_id: int,
        fingerprint: Hashable | None,
        payload: Any,
    ) -> None:
        if self._body_ttl <= 0:
            return
        self._prune()
        self._bodies[(student, part_id)] = _Entry(
            value=payload, stored_at=self._clock(), fingerprint=fingerprint
        )

    # -- housekeeping -------------------------------------------------------

    def clear(self) -> None:
        """Forget everything."""
        self._grids.clear()
        self._bodies.clear()

    def invalidate(self, student: int | None) -> None:
        """Forget one child's grid and bodies."""
        self._grids.pop(student, None)
        for key in [k for k in self._bodies if k[0] == student]:
            del self._bodies[key]

    def _prune(self) -> None:
        """Drop expired bodies so a long-lived process does not grow forever."""
        dead = [k for k, e in self._bodies.items() if not self._fresh(e, self._body_ttl)]
        for key in dead:
            del self._bodies[key]

    def __len__(self) -> int:
        return len(self._grids) + len(self._bodies)
