"""Compatibility import for the frozen Goal 01 action history reader.

Live execution persists only through ``SqliteTaskRepository``.  This module is
kept so callers can deliberately inspect pre-Goal04 action aggregates without
accidentally acquiring a write-capable second repository.
"""

from .history_v1 import LegacyActionHistoryReader

__all__ = ["LegacyActionHistoryReader"]
