"""In-memory forecast cache keyed on a 0.1 degree grid cell plus the forecast hour.

Pure Python, no Django imports. Thread-safe (the server shares one cache across threads).
The clock is injectable so TTL behaviour is testable without sleeping.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from weather.models import HourlySeries, HourValues

CELL_SCALE = 10  # 1 / 0.1 degree
DEFAULT_TTL_SECONDS = 45 * 60  # the guide says 30-60 minutes
DEFAULT_MAX_ENTRIES = 50_000  # ~ 150 heatmap points x 49 hours = 7,350 per trip

Cell = tuple[int, int]  # (lat index, lon index) in tenths of a degree


def grid_cell(lat: float, lon: float) -> Cell:
    """Snap a coordinate to its 0.1 degree grid cell (nearest cell centre, halves round up)."""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError(f"coordinates must be finite, got ({lat!r}, {lon!r})")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordinates out of range: ({lat!r}, {lon!r})")
    return math.floor(lat * CELL_SCALE + 0.5), math.floor(lon * CELL_SCALE + 0.5)


def cell_center(cell: Cell) -> tuple[float, float]:
    """The (lat, lon) at the centre of a cell, rounded to 0.1 degree."""
    return round(cell[0] / CELL_SCALE, 1), round(cell[1] / CELL_SCALE, 1)


def floor_hour(moment: datetime) -> datetime:
    """Truncate a timezone-aware datetime to the start of its UTC hour."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class WeatherCache:
    """TTL cache of `HourValues` keyed by (grid cell, UTC hour).

    An entry expires `ttl_seconds` after it was stored (`age >= ttl` counts as expired).
    Null values are cached like any other value, so a cached null stays flagged as missing.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be finite and > 0, got {ttl_seconds!r}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be at least 1, got {max_entries!r}")
        self._ttl = ttl_seconds
        self._clock = clock
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[Cell, datetime], tuple[float, HourValues]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(cell: Cell, hour: datetime) -> tuple[Cell, datetime]:
        if hour != floor_hour(hour):
            raise ValueError(f"hour must be on the hour (UTC), got {hour.isoformat()}")
        return cell, hour.astimezone(UTC)

    def get(self, cell: Cell, hour: datetime) -> HourValues | None:
        """Return the cached values, or None on a miss or an expired entry."""
        key = self._key(cell, hour)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, values = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                self._misses += 1
                return None
            self._hits += 1
            return values

    def put(self, cell: Cell, hour: datetime, values: HourValues) -> None:
        key = self._key(cell, hour)
        with self._lock:
            self._entries[key] = (self._clock() + self._ttl, values)
            self._entries.move_to_end(key)
            if len(self._entries) > self._max_entries:
                self._evict()

    def put_series(self, cell: Cell, series: HourlySeries) -> None:
        for hour, values in series.items():
            self.put(cell, hour, values)

    def _evict(self) -> None:
        """Drop expired entries first, then the oldest, until within max_entries."""
        now = self._clock()
        for key in [k for k, (expires_at, _) in self._entries.items() if now >= expires_at]:
            del self._entries[key]
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = self._misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(self._hits, self._misses)
