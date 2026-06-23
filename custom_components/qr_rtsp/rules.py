"""Access-rule matching and validity evaluation for scanned QR codes.

A rule binds a QR payload to optional constraints: a validity date range
(`valid_from`/`valid_until`, inclusive), allowed weekdays, and a daily time
window. A time window may wrap past midnight (e.g. 22:00 → 06:00).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from typing import Any

from .const import (
    REASON_EXPIRED,
    REASON_NOT_YET,
    REASON_OK,
    REASON_OUT_OF_SCHEDULE,
    RULE_END_TIME,
    RULE_PAYLOAD,
    RULE_START_TIME,
    RULE_VALID_FROM,
    RULE_VALID_UNTIL,
    RULE_WEEKDAYS,
    WEEKDAYS,
)


def find_rule(
    rules: Iterable[Mapping[str, Any]], payload: str
) -> Mapping[str, Any] | None:
    """Return the rule whose payload matches, or None."""
    for rule in rules:
        if rule.get(RULE_PAYLOAD) == payload:
            return rule
    return None


def evaluate(rule: Mapping[str, Any], now: datetime) -> tuple[bool, str]:
    """Return (authorized, reason) for a matched rule at the given local time."""
    today = now.date()

    valid_from = _parse_date(rule.get(RULE_VALID_FROM))
    if valid_from and today < valid_from:
        return False, REASON_NOT_YET

    valid_until = _parse_date(rule.get(RULE_VALID_UNTIL))
    if valid_until and today > valid_until:
        return False, REASON_EXPIRED

    weekdays = rule.get(RULE_WEEKDAYS) or []
    if weekdays and WEEKDAYS[now.weekday()] not in weekdays:
        return False, REASON_OUT_OF_SCHEDULE

    start = _parse_time(rule.get(RULE_START_TIME))
    end = _parse_time(rule.get(RULE_END_TIME))
    if (start or end) and not _within_window(now.time(), start, end):
        return False, REASON_OUT_OF_SCHEDULE

    return True, REASON_OK


def _within_window(current: time, start: time | None, end: time | None) -> bool:
    """Whether `current` falls in [start, end], supporting overnight windows."""
    if start and end:
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end  # wraps past midnight
    if start:
        return current >= start
    if end:
        return current <= end
    return True


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO date string, tolerating empty/invalid values."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_time(value: str | None) -> time | None:
    """Parse an ISO time string (HH:MM or HH:MM:SS), tolerating bad values."""
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None
