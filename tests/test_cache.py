"""Unit tests for the planning cache, driven by a fake clock."""

from __future__ import annotations

from schoolsoft_mcp.cache import PlanningCache
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.models import PlanningPart
from schoolsoft_mcp.parsers import subjectrooms as sr


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(grid_ttl: float = 600.0, body_ttl: float = 3600.0) -> tuple[PlanningCache, Clock]:
    clock = Clock()
    return PlanningCache(grid_ttl=grid_ttl, body_ttl=body_ttl, clock=clock), clock


FP = ("2026-08-17 14:11", "Idrott och hälsa terminen", "2026-08-19", "2026-12-31", "ONGOING", "True")


class TestGrid:
    def test_served_while_fresh_and_dropped_after_ttl(self) -> None:
        cache, clock = make(grid_ttl=600)
        assert cache.get_grid(900017) is None
        cache.put_grid(900017, [{"planningPartId": 900005}])
        clock.now += 599
        assert cache.get_grid(900017) == [{"planningPartId": 900005}]
        clock.now += 2
        assert cache.get_grid(900017) is None

    def test_children_do_not_share_a_grid(self) -> None:
        # The grid is per selected child. Serving one child's plannings to a
        # question about the other is the sibling mix-up this server has
        # already been fixed for once.
        cache, _ = make()
        cache.put_grid(900017, ["a"])
        assert cache.get_grid(900018) is None
        assert cache.get_grid(None) is None

    def test_zero_ttl_disables(self) -> None:
        cache, _ = make(grid_ttl=0)
        cache.put_grid(900017, ["a"])
        assert cache.get_grid(900017) is None
        assert len(cache) == 0


class TestBodies:
    def test_served_with_the_same_fingerprint(self) -> None:
        cache, _ = make()
        cache.put_body(900017, 900005, FP, {"description": "<p>x</p>"})
        assert cache.get_body(900017, 900005, FP) == {"description": "<p>x</p>"}
        assert cache.stats.body_hits == 1

    def test_a_changed_row_drops_the_body(self) -> None:
        # Re-published: publishDate moved. The old body must not be served,
        # and the stale entry must not linger for a later caller with no
        # fingerprint to offer.
        cache, _ = make()
        cache.put_body(900017, 900005, FP, {"description": "old"})
        moved = ("2026-09-10 08:00", *FP[1:])
        assert cache.get_body(900017, 900005, moved) is None
        assert cache.get_body(900017, 900005, None) is None

    def test_marked_unread_again_counts_as_changed(self) -> None:
        cache, _ = make()
        cache.put_body(900017, 900005, FP, {"description": "old"})
        unread = (*FP[:-1], "False")
        assert cache.get_body(900017, 900005, unread) is None

    def test_expires_after_ttl_even_if_unchanged(self) -> None:
        # The fingerprint is a hint, not a guarantee: a silent edit moves
        # nothing on the grid row. The TTL is what bounds that.
        cache, clock = make(body_ttl=3600)
        cache.put_body(900017, 900005, FP, {"description": "old"})
        clock.now += 3601
        assert cache.get_body(900017, 900005, FP) is None

    def test_no_fingerprint_accepts_whatever_is_fresh(self) -> None:
        cache, _ = make()
        cache.put_body(900017, 900005, FP, {"description": "x"})
        assert cache.get_body(900017, 900005, None) == {"description": "x"}

    def test_children_do_not_share_bodies(self) -> None:
        cache, _ = make()
        cache.put_body(900017, 900005, FP, {"description": "x"})
        assert cache.get_body(900018, 900005, FP) is None

    def test_expired_entries_are_pruned_on_write(self) -> None:
        cache, clock = make(body_ttl=10)
        for part in range(900001, 900031):
            cache.put_body(900017, part, FP, {})
        clock.now += 11
        cache.put_body(900017, 900099, FP, {})
        assert len(cache) == 1

    def test_zero_ttl_disables(self) -> None:
        cache, _ = make(body_ttl=0)
        cache.put_body(900017, 900005, FP, {})
        assert cache.get_body(900017, 900005, FP) is None


class TestInvalidate:
    def test_one_child(self) -> None:
        cache, _ = make()
        cache.put_grid(900017, ["a"])
        cache.put_body(900017, 900005, FP, {})
        cache.put_grid(900018, ["b"])
        cache.invalidate(900017)
        assert cache.get_grid(900017) is None
        assert cache.get_body(900017, 900005, FP) is None
        assert cache.get_grid(900018) == ["b"]

    def test_everything(self) -> None:
        cache, _ = make()
        cache.put_grid(900017, ["a"])
        cache.put_body(900018, 900005, FP, {})
        cache.clear()
        assert len(cache) == 0


class TestFingerprint:
    def test_same_row_same_fingerprint_from_dict_and_model(self) -> None:
        row = {
            "publish_date": "2026-08-17 14:11",
            "title": "Idrott och hälsa terminen",
            "start_date": "2026-08-19",
            "end_date": "2026-12-31",
            "status": "ONGOING",
            "read": True,
        }
        part = PlanningPart(**row)
        assert sr.row_fingerprint(row) == sr.row_fingerprint(part) == FP

    def test_each_signal_moves_it(self) -> None:
        base = {"publish_date": "a", "title": "t", "start_date": "s", "end_date": "e", "status": "o", "read": True}
        for key, value in (("publish_date", "b"), ("title", "u"), ("start_date", "x"),
                           ("end_date", "y"), ("status", "DONE"), ("read", False)):
            assert sr.row_fingerprint({**base, key: value}) != sr.row_fingerprint(base), key

    def test_missing_values_are_empty_not_none(self) -> None:
        assert sr.row_fingerprint({}) == ("",) * 6


class TestSettings:
    def test_defaults_and_from_settings(self) -> None:
        s = Settings(school="x", username="u", password="p")
        assert (s.cache_grid_ttl, s.cache_body_ttl) == (600.0, 21600.0)
        cache = PlanningCache.from_settings(s)
        assert cache.enabled

    def test_env_overrides(self, monkeypatch) -> None:
        for name, value in (("SCHOOLSOFT_SCHOOL", "x"), ("SCHOOLSOFT_USERNAME", "u"),
                            ("SCHOOLSOFT_PASSWORD", "p"), ("SCHOOLSOFT_CACHE_GRID_TTL", "0"),
                            ("SCHOOLSOFT_CACHE_BODY_TTL", "90")):
            monkeypatch.setenv(name, value)
        s = Settings.from_env()
        assert (s.cache_grid_ttl, s.cache_body_ttl) == (0.0, 90.0)

    def test_bad_env_is_a_config_error(self, monkeypatch) -> None:
        import pytest

        from schoolsoft_mcp.config import ConfigError

        for name, value in (("SCHOOLSOFT_SCHOOL", "x"), ("SCHOOLSOFT_USERNAME", "u"),
                            ("SCHOOLSOFT_PASSWORD", "p"), ("SCHOOLSOFT_CACHE_BODY_TTL", "soon")):
            monkeypatch.setenv(name, value)
        with pytest.raises(ConfigError, match="SCHOOLSOFT_CACHE_BODY_TTL"):
            Settings.from_env()
        monkeypatch.setenv("SCHOOLSOFT_CACHE_BODY_TTL", "-1")
        with pytest.raises(ConfigError, match="negative"):
            Settings.from_env()
