"""Attachment download flow: child selection, the 404 retry, and its note."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from schoolsoft_mcp import server
from schoolsoft_mcp.client import SchoolSoftAuthError, SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.server import AppContext, _fetch_attachment, _select_child

BASE = "https://example.test"
DOWNLOAD_URL = f"{BASE}/yourschool/jsp/student/right_student_file_download.jsp"
NEWS_URL = f"{BASE}/yourschool/jsp/student/right_student_news.jsp"
HEADER_URL = f"{BASE}/yourschool/rest-api/parent/header/parent"

HEADER_PAYLOAD = {
    "children": [
        {"id": 900017, "firstName": "Bea", "schools": [{"orgId": 900003, "className": "7B"}]},
        {"id": 4712, "firstName": "Cai", "schools": [{"orgId": 900003, "className": "2A"}]},
    ],
    "currentChildId": 900017,
    "currentOrgId": 900003,
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


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retry sequence, drop the wall-clock waits."""
    monkeypatch.setattr(
        server, "_ATTACHMENT_RETRY_DELAYS", (0.0,) * len(server._ATTACHMENT_RETRY_DELAYS)
    )


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
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
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
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
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
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
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
            app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
        )
    await app.client.close()


@respx.mock
async def test_select_child_looks_up_the_real_org_id(app: AppContext) -> None:
    """Never guess orgId=900001: a wrong one is accepted while the child doesn't change."""
    _mock_login()
    respx.get(HEADER_URL).mock(return_value=httpx.Response(200, json=HEADER_PAYLOAD))
    put = respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))

    await _select_child(app, 4712)

    assert put.calls[0].request.url.params["orgId"] == "900003"
    assert app.client.active_child == (4712, 900003)
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


@respx.mock
async def test_refused_after_reauth_gets_the_note_too(app: AppContext) -> None:
    """A 403 never reaches us as an HTTPStatusError — it arrives as an access error."""
    _mock_login()
    news = respx.get(NEWS_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    # Every attempt re-logs in and stays refused, so the count isn't fixed.
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(403))

    content, _headers, note = await _fetch_attachment(
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b""
    assert note is not None
    assert "student_id" in note
    assert news.called
    await app.client.close()


@respx.mock
async def test_bad_credentials_are_not_dressed_up_as_a_child_problem(
    app: AppContext,
) -> None:
    _mock_login()
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/Login.jsp?error=1"})
    )

    with pytest.raises(SchoolSoftAuthError):
        await _fetch_attachment(
            app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
        )
    await app.client.close()


@respx.mock
async def test_missing_org_id_fails_loudly(app: AppContext) -> None:
    """Guessing orgId=900001 would switch nothing and answer for the previous child."""
    _mock_login()
    respx.get(HEADER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "children": [{"id": 900017, "name": "Bea"}, {"id": 4712, "name": "Cai"}],
                "currentChildId": 900017,
                "currentOrgId": 900003,
            },
        )
    )
    put = respx.put(HEADER_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="org_id"):
        await _select_child(app, 4712)

    assert not put.called
    assert app.client.active_child is None
    await app.client.close()


@respx.mock
async def test_retries_until_the_temp_file_lands(app: AppContext) -> None:
    """A large attachment 404s while SchoolSoft is still copying it.

    Observed on a 3.9 MB PDF: the first fetch 404d, the very next one got
    the file. One immediate retry wasn't enough, so back off and try again.
    """
    _mock_login()
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    download = respx.get(DOWNLOAD_URL)
    download.side_effect = [
        httpx.Response(404, text="Not Found"),
        httpx.Response(404, text="Not Found"),
        httpx.Response(404, text="Not Found"),
        httpx.Response(
            200, content=b"%PDF-1.4 stub", headers={"content-type": "application/pdf"}
        ),
    ]

    content, _headers, note = await _fetch_attachment(
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b"%PDF-1.4 stub"
    assert note is None
    assert download.call_count == 4
    await app.client.close()


@respx.mock
async def test_a_failed_priming_request_does_not_abort_the_retries(
    app: AppContext,
) -> None:
    """Opening the news item is best-effort — the retries matter more."""
    _mock_login()
    respx.get(NEWS_URL).mock(return_value=httpx.Response(500, text="boom"))
    download = respx.get(DOWNLOAD_URL)
    download.side_effect = [
        httpx.Response(404, text="Not Found"),
        httpx.Response(
            200, content=b"%PDF-1.4 stub", headers={"content-type": "application/pdf"}
        ),
    ]

    content, _headers, note = await _fetch_attachment(
        app, news_id=900035, fileid=11955, type_id=1, object_kind="news"
    )

    assert content == b"%PDF-1.4 stub"
    assert note is None
    await app.client.close()
