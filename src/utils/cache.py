"""Simple time-based cache for prices and other data."""
import time
from typing import Optional, Dict, Any
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class SimpleCache:
    """Simple in-memory cache with TTL."""

    def __init__(self, ttl_seconds: int = 60):
        """
        Initialize cache.

        Args:
            ttl_seconds: Time to live for cache entries in seconds
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/not found
        """
        if key not in self._cache:
            return None

        entry = self._cache[key]
        if time.time() - entry['timestamp'] > self.ttl_seconds:
            # Expired, remove
            del self._cache[key]
            return None

        return entry['value']

    def set(self, key: str, value: Any):
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._cache[key] = {
            'value': value,
            'timestamp': time.time()
        }

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()

    def remove(self, key: str):
        """Remove specific key from cache."""
        self._cache.pop(key, None)


# Global price cache (60 seconds TTL)
price_cache = SimpleCache(ttl_seconds=60)
