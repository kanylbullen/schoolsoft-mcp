import httpx
import pytest
import respx

from schoolsoft_mcp.client import (
    SchoolSoftAuthError,
    SchoolSoftClient,
    _resolve_redirect,
)

BASE = "https://example.test"


@pytest.fixture
def client() -> SchoolSoftClient:
    return SchoolSoftClient(
        school="yourschool",
        username="alice",
        password="secret",
        base_url=BASE,
    )


@respx.mock
async def test_login_success_follows_redirect(client: SchoolSoftClient) -> None:
    login = respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    start = respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )
    await client.login()
    assert login.called
    assert start.called
    await client.close()


@respx.mock
async def test_login_failure_raises(client: SchoolSoftClient) -> None:
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/Login.jsp?error=1"})
    )
    with pytest.raises(SchoolSoftAuthError):
        await client.login()
    await client.close()


@respx.mock
async def test_fetch_html_reauths_on_session_expiry(client: SchoolSoftClient) -> None:
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )
    target = respx.get(f"{BASE}/yourschool/jsp/student/right_student_lunchmenu.jsp")
    target.side_effect = [
        httpx.Response(302, headers={"Location": "/yourschool/jsp/Login.jsp"}),
        httpx.Response(200, text="<html>menu</html>"),
    ]

    html = await client.fetch_html("jsp/student/right_student_lunchmenu.jsp")
    assert "menu" in html
    assert target.call_count == 2
    await client.close()


@respx.mock
async def test_fetch_json_reauths_on_401(client: SchoolSoftClient) -> None:
    """REST endpoints return 401 (not a redirect) when the session expires."""
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )
    target = respx.get(f"{BASE}/yourschool/rest-api/parent/header/parent")
    target.side_effect = [
        httpx.Response(401, json={"error": "Unauthorized"}),
        httpx.Response(200, json={"children": []}),
    ]

    payload = await client.fetch_json("rest-api/parent/header/parent")
    assert payload == {"children": []}
    assert target.call_count == 2
    await client.close()


@respx.mock
async def test_fetch_json_reauths_on_403(client: SchoolSoftClient) -> None:
    """403 is also treated as a session-expired signal."""
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )
    target = respx.get(f"{BASE}/yourschool/rest-api/parent/calendar/lessons/week/21")
    target.side_effect = [
        httpx.Response(403, json={"error": "Forbidden"}),
        httpx.Response(200, json=[]),
    ]

    payload = await client.fetch_json("rest-api/parent/calendar/lessons/week/21")
    assert payload == []
    assert target.call_count == 2
    await client.close()


@respx.mock
async def test_fetch_bytes_reauths_on_401(client: SchoolSoftClient) -> None:
    """File-download paths get the same auto-reauth treatment as fetch_json."""
    respx.post(f"{BASE}/yourschool/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{BASE}/yourschool/jsp/start.jsp").mock(
        return_value=httpx.Response(200, text="ok")
    )
    target = respx.get(
        f"{BASE}/yourschool/jsp/student/right_student_file_download.jsp"
    )
    target.side_effect = [
        httpx.Response(401),
        httpx.Response(200, content=b"PDF-CONTENT", headers={"content-type": "application/pdf"}),
    ]

    content, headers = await client.fetch_bytes(
        "jsp/student/right_student_file_download.jsp"
    )
    assert content == b"PDF-CONTENT"
    assert headers["content-type"] == "application/pdf"
    assert target.call_count == 2
    await client.close()


def test_url_for_normalizes_paths(client: SchoolSoftClient) -> None:
    assert client.url_for("jsp/foo.jsp") == "/yourschool/jsp/foo.jsp"
    assert client.url_for("/jsp/foo.jsp") == "/yourschool/jsp/foo.jsp"
    assert client.url_for("yourschool/jsp/foo.jsp") == "/yourschool/jsp/foo.jsp"


def test_resolve_redirect_absolute_url() -> None:
    """A fully-qualified Location wins regardless of the request URL."""
    assert (
        _resolve_redirect(
            "https://other.example/path", "https://sms.test/yourschool/jsp/Login.jsp"
        )
        == "https://other.example/path"
    )


def test_resolve_redirect_absolute_path() -> None:
    """A Location starting with '/' resolves against the host root."""
    assert (
        _resolve_redirect(
            "/yourschool/jsp/student/start.jsp",
            "https://sms.test/yourschool/jsp/Login.jsp",
        )
        == "https://sms.test/yourschool/jsp/student/start.jsp"
    )


def test_resolve_redirect_relative_target() -> None:
    """A bare relative target resolves against the request URL's directory.

    SchoolSoft's POST /<school>/jsp/Login.jsp responds with
    ``Location: student/right_student_startpage.jsp`` — the resolved URL
    must keep ``/jsp/`` in the path, not drop it.
    """
    assert (
        _resolve_redirect(
            "student/right_student_startpage.jsp",
            "https://sms.test/yourschool/jsp/Login.jsp",
        )
        == "https://sms.test/yourschool/jsp/student/right_student_startpage.jsp"
    )


def test_resolve_redirect_query_only() -> None:
    """A query-only Location keeps the request path, swapping the query."""
    assert (
        _resolve_redirect(
            "?action=view&requestid=42",
            "https://sms.test/yourschool/jsp/student/news.jsp",
        )
        == "https://sms.test/yourschool/jsp/student/news.jsp?action=view&requestid=42"
    )
