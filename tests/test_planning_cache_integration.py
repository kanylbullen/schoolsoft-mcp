"""The cache as the tools see it: which requests a second call still makes.

Routes are mocked with respx; the assertions are on call counts, because
the whole point is the requests that do not happen.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from schoolsoft_mcp import server
from schoolsoft_mcp.cache import PlanningCache
from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.server import AppContext

BASE = "https://example.test"
ROOT = f"{BASE}/yourschool"
HEADER_URL = f"{ROOT}/rest-api/parent/header/parent"
GRID_URL = f"{ROOT}/rest-api/parent/ps/subjectroom/plannings/grid/rows"
VIEW_URL = f"{ROOT}/rest-api/parent/ps/planning_parts/900005/view"
FILES_URL = f"{ROOT}/rest-api/parent/ps/material/900005/file"
LINKS_URL = f"{ROOT}/rest-api/parent/ps/material/900005/link"

CHILD = 900017
HEADER_PAYLOAD = {
    "children": [
        {"id": CHILD, "firstName": "Bea", "schools": [{"orgId": 900003, "className": "7B"}]},
    ],
    "currentChildId": CHILD,
    "currentOrgId": 900003,
}


def grid_row(publish: str = "2026-08-17 14:11", read: bool = True) -> dict[str, Any]:
    return {
        "planningPartId": 900005,
        "planningId": 900006,
        "activityId": 900007,
        "planningTitle": "",
        "planningPartTitle": "Idrott och hälsa terminen",
        "subjects": [{"name": "Idrott och hälsa", "color": ""}],
        "teacher": "Alex Andersson",
        "teacherPictureUrl": "",
        "startDate": "2026-08-19",
        "endDate": "2026-12-31",
        "publishDate": publish,
        "status": "ONGOING",
        "read": read,
    }


VIEW_PAYLOAD = {
    "title": "Idrott och hälsa terminen",
    "subTitle": "ons 19 aug. - tors 31 dec. Planering, Idrott och hälsa",
    "description": "<p>v.37 Orientering (samling vid klubbstugan)</p><p>v.38 Innebandy</p>",
    "publishDate": "2026-08-17 14:11",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Ctx:
    def __init__(self, app: AppContext) -> None:
        self.request_context = type("R", (), {"lifespan_context": app})()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(clock: Clock) -> AppContext:
    settings = Settings(school="yourschool", username="alice", password="secret", base_url=BASE)
    client = SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        base_url=settings.base_url,
    )
    return AppContext(
        settings=settings,
        client=client,
        lock=asyncio.Lock(),
        cache=PlanningCache(grid_ttl=600, body_ttl=3600, clock=clock),
    )


def _mock_session() -> None:
    respx.post(f"{ROOT}/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{ROOT}/jsp/start.jsp").mock(return_value=httpx.Response(200, text="ok"))
    respx.get(HEADER_URL).mock(return_value=httpx.Response(200, json=HEADER_PAYLOAD))
    respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))
    respx.get(FILES_URL).mock(return_value=httpx.Response(404, text="nope"))
    respx.get(LINKS_URL).mock(return_value=httpx.Response(404, text="nope"))


@respx.mock
async def test_a_second_listing_makes_no_planning_requests(app: AppContext) -> None:
    _mock_session()
    grid = respx.get(GRID_URL).mock(return_value=httpx.Response(200, json=[grid_row()]))
    view = respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    ctx = Ctx(app)

    first = await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    second = await server.get_planning(ctx, week=38, year=2026, student_id=CHILD)

    assert grid.call_count == 1
    assert view.call_count == 1
    # Parsing is per call: the same cached body yields the week asked for.
    assert first.items[0].week_lines == ["v.37 Orientering (samling vid klubbstugan)"]
    assert second.items[0].week_lines == ["v.38 Innebandy"]
    assert app.cache.stats.body_hits == 1


@respx.mock
async def test_a_republished_planning_is_fetched_again(app: AppContext, clock: Clock) -> None:
    # The grid expires after ten minutes; when it comes back with a moved
    # publishDate the cached body is stale and must be refetched. When it
    # comes back unchanged, it must not be.
    _mock_session()
    grid = respx.get(GRID_URL).mock(
        side_effect=[
            httpx.Response(200, json=[grid_row()]),
            httpx.Response(200, json=[grid_row(publish="2026-09-10 08:00")]),
            httpx.Response(200, json=[grid_row(publish="2026-09-10 08:00")]),
        ]
    )
    view = respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    ctx = Ctx(app)

    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    clock.now += 601
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    assert (grid.call_count, view.call_count) == (2, 2)

    clock.now += 601
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    assert (grid.call_count, view.call_count) == (3, 2)


@respx.mock
async def test_marked_unread_again_is_fetched_again(app: AppContext, clock: Clock) -> None:
    # SchoolSoft flags an edited planning unread for the guardian. That
    # flip is on the grid row and invalidates the body.
    _mock_session()
    respx.get(GRID_URL).mock(
        side_effect=[
            httpx.Response(200, json=[grid_row(read=True)]),
            httpx.Response(200, json=[grid_row(read=False)]),
        ]
    )
    view = respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    ctx = Ctx(app)
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    clock.now += 601
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    assert view.call_count == 2


@respx.mock
async def test_refresh_bypasses_the_cache(app: AppContext) -> None:
    _mock_session()
    grid = respx.get(GRID_URL).mock(return_value=httpx.Response(200, json=[grid_row()]))
    view = respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    ctx = Ctx(app)
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD, refresh=True)
    assert (grid.call_count, view.call_count) == (2, 2)


@respx.mock
async def test_detail_reuses_the_listing_grid_and_body(app: AppContext) -> None:
    # get_planning_detail used to download the whole grid to read one row's
    # dates and teacher. After a listing, it should download nothing but
    # the material lists it has no cache for.
    _mock_session()
    grid = respx.get(GRID_URL).mock(return_value=httpx.Response(200, json=[grid_row()]))
    view = respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    ctx = Ctx(app)
    await server.get_planning(ctx, week=37, year=2026, student_id=CHILD)
    detail = await server.get_planning_detail(ctx, part_id=900005, week=37, student_id=CHILD)
    assert (grid.call_count, view.call_count) == (1, 1)
    assert detail.teacher == "Alex Andersson"
    assert detail.start_date == "2026-08-19"
    assert detail.week_lines == ["v.37 Orientering (samling vid klubbstugan)"]
    assert detail.note is None


@respx.mock
async def test_a_cold_context_fetches_fresh(clock: Clock) -> None:
    # A one-shot consumer builds its own context and must see the current
    # state; there is no cache to inherit.
    _mock_session()
    grid = respx.get(GRID_URL).mock(return_value=httpx.Response(200, json=[grid_row()]))
    respx.get(VIEW_URL).mock(return_value=httpx.Response(200, json=VIEW_PAYLOAD))
    for _ in range(2):
        settings = Settings(school="yourschool", username="alice", password="secret", base_url=BASE)
        client = SchoolSoftClient(
            school=settings.school, username=settings.username,
            password=settings.password, base_url=settings.base_url,
        )
        app = AppContext(settings=settings, client=client, lock=asyncio.Lock())
        await server.get_planning(Ctx(app), week=37, year=2026, student_id=CHILD)
        await client.close()
    assert grid.call_count == 2
