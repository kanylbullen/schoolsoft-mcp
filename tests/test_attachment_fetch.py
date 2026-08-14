"""Attachment download flow: child selection, the 404 retry, and its note."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.server import AppContext, _fetch_attachment, _select_child

BASE = "https://example.test"
DOWNLOAD_URL = f"{BASE}/yourschool/jsp/student/right_student_file_download.jsp"
NEWS_URL = f"{BASE}/yourschool/jsp/student/right_student_news.jsp"
HEADER_URL = f"{BASE}/yourschool/rest-api/parent/header/parent"

HEADER_PAYLOAD = {
    "children": [
        {"id": 4711, "firstName": "Bea", "schools": [{"orgId": 175, "className": "7B"}]},
        {"id": 4712, "firstName": "Cai", "schools": [{"orgId": 175, "className": "2A"}]},
    ],
    "currentChildId": 4711,
    "currentOrgId": 175,
}


@pytest.fixture
def app() -> AppContext:
    settings = Settings(
        school="yourschool", username="alice", password="secret", base_url=BASE
    )
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
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )


@respx.mock
async def test_download_succeeds_on_the_first_try(app: AppContext) -> None:
    _mock_login()
    news = respx.get(NEWS_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4 stub", headers={"content-type": "application/pdf"}
        )
    )

    content, headers, note = await _fetch_attachment(
        app, news_id=11854, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b"%PDF-1.4 stub"
    assert headers["content-type"] == "application/pdf"
    assert note is None
    assert not news.called  # no priming request on the happy path
    await app.client.close()


@respx.mock
async def test_404_retries_after_opening_the_news_item(app: AppContext) -> None:
    """A browser has the item open when the link is clicked; reproduce that."""
    _mock_login()
    news = respx.get(NEWS_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    download = respx.get(DOWNLOAD_URL)
    download.side_effect = [
        httpx.Response(404, text="Not Found"),
        httpx.Response(
            200, content=b"%PDF-1.4 stub", headers={"content-type": "application/pdf"}
        ),
    ]

    content, _headers, note = await _fetch_attachment(
        app, news_id=11854, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b"%PDF-1.4 stub"
    assert note is None
    assert news.called
    assert download.call_count == 2
    await app.client.close()


@respx.mock
async def test_persistent_404_returns_an_actionable_note(app: AppContext) -> None:
    _mock_login()
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(404, text="Not Found"))

    content, headers, note = await _fetch_attachment(
        app, news_id=11854, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b""
    assert headers == {}
    assert note is not None
    assert "student_id" in note
    assert "11955" in note
    await app.client.close()


@respx.mock
async def test_non_404_errors_still_raise(app: AppContext) -> None:
    """A 500 is a real server fault — don't dress it up as a child-selection hint."""
    _mock_login()
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_attachment(
            app, news_id=11854, fileid=11955, type_id=1, object_kind="news"
        )
    await app.client.close()


@respx.mock
async def test_select_child_looks_up_the_real_org_id(app: AppContext) -> None:
    """Never guess orgId=1: a wrong one is accepted while the child doesn't change."""
    _mock_login()
    respx.get(HEADER_URL).mock(return_value=httpx.Response(200, json=HEADER_PAYLOAD))
    put = respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))

    await _select_child(app, 4712)

    assert put.calls[0].request.url.params["orgId"] == "175"
    assert app.client.active_child == (4712, 175)
    await app.client.close()


@respx.mock
async def test_select_child_is_a_noop_when_already_selected(app: AppContext) -> None:
    _mock_login()
    header = respx.get(HEADER_URL).mock(
        return_value=httpx.Response(200, json=HEADER_PAYLOAD)
    )
    put = respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))

    await _select_child(app, 4712)
    await _select_child(app, 4712)

    assert put.call_count == 1
    assert header.call_count == 1
    await app.client.close()


@respx.mock
async def test_select_child_rejects_an_unknown_student(app: AppContext) -> None:
    _mock_login()
    respx.get(HEADER_URL).mock(return_value=httpx.Response(200, json=HEADER_PAYLOAD))

    with pytest.raises(ValueError, match="list_children"):
        await _select_child(app, 9999)
    await app.client.close()
