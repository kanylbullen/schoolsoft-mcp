"""Unit tests for AsOf temporal-anchor field on response models."""

from __future__ import annotations

import datetime as dt

from schoolsoft_mcp.models import (
    AsOf,
    AttendanceReport,
    ChildList,
    HomeworkList,
    LunchWeek,
    MessageList,
    NewsFeed,
    ScheduleWeek,
)


def test_as_of_default_is_none() -> None:
    """All response models default to as_of=None (filled in by the tool wrapper)."""
    assert ChildList(school="x", children=[]).as_of is None
    assert LunchWeek(week=21, year=2026, school="x", days=[]).as_of is None
    assert ScheduleWeek(week=21, year=2026, school="x", lessons=[]).as_of is None
    assert HomeworkList(school="x", items=[]).as_of is None
    assert AttendanceReport(school="x", weeks=[]).as_of is None
    assert NewsFeed(school="x", items=[]).as_of is None
    assert MessageList(school="x", items=[]).as_of is None


def test_as_of_assignable() -> None:
    """Tool wrappers assign as_of after parsing — the field must accept assignment."""
    feed = NewsFeed(school="x", items=[])
    feed.as_of = AsOf(date="2026-05-19", iso_year=2026, iso_week=21)
    assert feed.as_of is not None
    assert feed.as_of.iso_year == 2026
    assert feed.as_of.iso_week == 21


def test_now_as_of_matches_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The helper used by every tool returns today's date in ISO form.

    Date is frozen via monkeypatch to keep the test deterministic across a
    midnight rollover (otherwise the helper and the test's own
    ``date.today()`` could disagree on which day it is).
    """
    from schoolsoft_mcp import server

    fixed = dt.date(2026, 5, 19)  # Tuesday of ISO week 21, 2026

    class _FrozenDate(dt.date):
        @classmethod
        def today(cls) -> dt.date:
            return fixed

    monkeypatch.setattr(server.dt, "date", _FrozenDate)

    a = server._now_as_of()
    assert a.date == "2026-05-19"
    assert a.iso_year == 2026
    assert a.iso_week == 21


def test_stamp_populates_as_of_when_absent() -> None:
    """``_stamp`` is the one place every tool runs its result through."""
    from schoolsoft_mcp.server import _stamp

    feed = NewsFeed(school="x", items=[])
    stamped = _stamp(feed)
    assert stamped is feed
    assert stamped.as_of is not None


def test_stamp_overwrites_existing_as_of() -> None:
    """Re-stamping replaces any pre-set value — keeps semantics consistent."""
    from schoolsoft_mcp.server import _stamp

    feed = NewsFeed(
        school="x",
        items=[],
        as_of=AsOf(date="1999-12-31", iso_year=1999, iso_week=52),
    )
    _stamp(feed)
    today = dt.date.today()
    assert feed.as_of is not None
    assert feed.as_of.date == today.isoformat()


def test_stamp_returns_same_object() -> None:
    """_stamp mutates in place and returns the same instance for chaining."""
    from schoolsoft_mcp.server import _stamp

    feed = NewsFeed(school="x", items=[])
    assert _stamp(feed) is feed
