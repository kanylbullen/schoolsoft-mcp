"""Guardian childcare/fritids day-comment context and record safety."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
import respx

from schoolsoft_mcp.client import SchoolSoftClient, SchoolSoftConnectionError
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.server import AppContext, _fritids_day_record, _fritids_write_context

BASE = "https://example.test"
HEADER_URL = f"{BASE}/yourschool/rest-api/parent/header/parent"


@pytest.fixture
def app() -> AppContext:
    settings = Settings(school="yourschool", username="alice", password="secret", base_url=BASE)
    client = SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        base_url=settings.base_url,
    )
    return AppContext(settings=settings, client=client, lock=asyncio.Lock())


def _mock_login() -> None:
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(return_value=httpx.Response(200, text="ok"))


def test_fritids_day_record_preserves_existing_epoch_values() -> None:
    payload = {
        "preschoolschedules": [
            {
                # 2026-09-01 00:00 in Europe/Stockholm (22:00 UTC).
                "datelong": 1_788_213_600_000,
                "startTime": 1_788_242_400_000,
                "endTime": 1_788_271_200_000,
                "parentComment": "Old text",
            }
        ]
    }
    assert _fritids_day_record(payload, dt.date(2026, 9, 1)) == (
        1_788_213_600_000,
        1_788_242_400_000,
        1_788_271_200_000,
    )


def test_fritids_day_record_refuses_missing_times() -> None:
    payload = {
        "preschoolschedules": [{"datelong": 1_788_213_600_000, "parentComment": "Comment only"}]
    }
    with pytest.raises(ValueError, match="no pickup/drop-off times"):
        _fritids_day_record(payload, dt.date(2026, 9, 1))


def test_fritids_day_record_refuses_unknown_shape() -> None:
    with pytest.raises(SchoolSoftConnectionError, match="unexpected"):
        _fritids_day_record([], dt.date(2026, 9, 1))


@respx.mock
async def test_context_uses_the_only_child(app: AppContext) -> None:
    _mock_login()
    respx.get(HEADER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"children": [{"id": 4712, "schools": [{"orgId": 175}]}]},
        )
    )

    assert await _fritids_write_context(app, None) == (4712, 175)
    await app.client.close()


@respx.mock
async def test_explicit_child_uses_the_selected_org_id(app: AppContext) -> None:
    _mock_login()
    header = respx.get(HEADER_URL)
    header.side_effect = [
        httpx.Response(
            200,
            json={
                "children": [
                    {"id": 4711, "schools": [{"orgId": 999}]},
                    {"id": 4712, "schools": [{"orgId": 175}]},
                ],
                "currentChildId": 4711,
                "currentOrgId": 999,
            },
        ),
        httpx.Response(
            200,
            json={
                "children": [
                    {"id": 4711, "schools": [{"orgId": 999}]},
                    {"id": 4712, "schools": [{"orgId": 175}]},
                ],
                "currentChildId": 4711,
                "currentOrgId": 999,
            },
        ),
    ]
    respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))

    assert await _fritids_write_context(app, 4712) == (4712, 175)
    await app.client.close()
