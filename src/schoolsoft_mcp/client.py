"""SchoolSoft HTTP client — handles session, login, and authenticated GETs."""

from __future__ import annotations

import json
import logging
import sys
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

import httpx

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

logger = logging.getLogger(__name__)


class SchoolSoftAuthError(Exception):
    """Raised when authentication fails."""


class SchoolSoftConnectionError(Exception):
    """Raised when the SchoolSoft server cannot be reached."""


class SchoolSoftClient:
    """Async HTTP client for SchoolSoft's JSP endpoints.

    Maintains a cookie jar across requests and re-authenticates transparently
    when the session expires.
    """

    LOGIN_PATH = "/{school}/jsp/Login.jsp"

    def __init__(
        self,
        school: str,
        username: str,
        password: str,
        *,
        usertype: int = 2,
        base_url: str = "https://sms.schoolsoft.se",
        timeout: float = 20.0,
    ) -> None:
        self._school = school
        self._username = username
        self._password = password
        self._usertype = usertype
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={
                "User-Agent": "schoolsoft-mcp/0.1 (+https://github.com/kanylbullen/schoolsoft-mcp)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        self._logged_in = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def school(self) -> str:
        return self._school

    def url_for(self, path: str) -> str:
        """Resolve a path against the school root, e.g. 'jsp/student/foo.jsp'."""
        path = path.lstrip("/")
        if path.startswith(f"{self._school}/"):
            return f"/{path}"
        return f"/{self._school}/{path}"

    async def login(self) -> None:
        """Authenticate with SchoolSoft and store session cookies.

        SchoolSoft returns a 302 redirect on both success and failure. On
        failure the redirect Location points back at the login page (or an
        error page); on success it points at the user's start page.
        """
        url = self.LOGIN_PATH.format(school=self._school)
        data = {
            "action": "login",
            "usertype": str(self._usertype),
            "ssusername": self._username,
            "sspassword": self._password,
        }

        try:
            resp = await self._client.post(url, data=data)
        except httpx.HTTPError as err:
            raise SchoolSoftConnectionError(f"Could not reach SchoolSoft: {err}") from err

        location = resp.headers.get("Location", "")
        if resp.status_code not in (301, 302, 303) or _looks_like_login_failure(location):
            raise SchoolSoftAuthError(
                "Login failed — check school slug, username, password, and usertype."
            )

        # Follow the redirect once to fully establish the session.
        if location:
            try:
                await self._client.get(_resolve_redirect(location, url))
            except httpx.HTTPError:
                # Non-fatal — cookies are already set from the POST response.
                logger.debug("Post-login redirect failed; continuing with existing cookies")

        self._logged_in = True
        logger.debug("Login successful for school=%s usertype=%s", self._school, self._usertype)

    async def fetch_bytes(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        max_redirects: int = 5,
    ) -> tuple[bytes, dict[str, str]]:
        """GET an authenticated resource as bytes, following non-login redirects.

        Returns ``(content, headers)`` where ``headers`` is a normalised
        lowercase dict (keys like ``content-type``, ``content-disposition``,
        ``content-length``).

        Re-authenticates once if the first request bounces to login. Used for
        binary file downloads (attachments) where the redirect target is a
        short-lived signed URL under ``/files/<school>/tmp_file_*.tmp``.
        """
        if not self._logged_in:
            await self.login()

        url: str = self.url_for(path)
        current_params: dict[str, str] | None = params
        attempts_after_relogin = 0

        for _ in range(max_redirects + 1):
            try:
                resp = await self._client.get(url, params=current_params)
            except httpx.HTTPError as err:
                raise SchoolSoftConnectionError(f"GET {url} failed: {err}") from err

            if _is_login_redirect(resp):
                if attempts_after_relogin >= 1:
                    raise SchoolSoftAuthError(
                        "Re-authenticated but still bounced to login — giving up."
                    )
                logger.debug("Session expired, re-authenticating and retrying %s", url)
                self._logged_in = False
                await self.login()
                attempts_after_relogin += 1
                continue

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if not location:
                    break
                url = _resolve_redirect(location, url)
                current_params = None  # query is now in the new Location
                continue

            resp.raise_for_status()
            return resp.content, {k.lower(): v for k, v in resp.headers.items()}

        raise SchoolSoftConnectionError(
            f"Too many redirects while fetching {path}"
        )

    async def fetch_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        method: str = "GET",
        body: Any = None,
    ) -> Any:
        """Call a ``/rest-api/*`` endpoint and parse the JSON response.

        The SchoolSoft React app sits in front of a JSON API at
        ``/<school>/rest-api/*``. This helper sends an ``Accept:
        application/json`` header, re-authenticates on a login bounce just
        like ``fetch_html``, and returns the parsed payload.
        """
        if not self._logged_in:
            await self.login()

        url = self.url_for(path)
        method_upper = method.upper()
        headers = {"Accept": "application/json"}
        request_kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if body is not None:
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = body

        try:
            resp = await self._client.request(method_upper, url, **request_kwargs)
        except httpx.HTTPError as err:
            raise SchoolSoftConnectionError(f"{method_upper} {url} failed: {err}") from err

        if _is_login_redirect(resp):
            logger.debug("Session expired, re-authenticating and retrying %s", url)
            self._logged_in = False
            await self.login()
            try:
                resp = await self._client.request(method_upper, url, **request_kwargs)
            except httpx.HTTPError as err:
                raise SchoolSoftConnectionError(
                    f"{method_upper} {url} failed after re-login: {err}"
                ) from err

        resp.raise_for_status()
        if not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError as err:
            raise SchoolSoftConnectionError(
                f"Expected JSON from {url}, got {resp.headers.get('content-type', '?')}: {err}"
            ) from err

    async def fetch_html(self, path: str, *, params: dict[str, str] | None = None) -> str:
        """GET an authenticated page, re-logging in once on session expiry."""
        if not self._logged_in:
            await self.login()

        url = self.url_for(path)
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as err:
            raise SchoolSoftConnectionError(f"GET {url} failed: {err}") from err

        if _is_login_redirect(resp):
            logger.debug("Session expired, re-authenticating and retrying %s", url)
            self._logged_in = False
            await self.login()
            try:
                resp = await self._client.get(url, params=params)
            except httpx.HTTPError as err:
                raise SchoolSoftConnectionError(f"GET {url} failed after re-login: {err}") from err

        if resp.status_code in (301, 302, 303):
            # Follow a non-login redirect once (some pages redirect to a canonical URL).
            location = resp.headers.get("Location", "")
            if location:
                try:
                    resp = await self._client.get(_resolve_redirect(location, url))
                except httpx.HTTPError as err:
                    raise SchoolSoftConnectionError(
                        f"Following redirect {location!r} failed: {err}"
                    ) from err

        resp.raise_for_status()
        return resp.text


def _looks_like_login_failure(location: str) -> bool:
    lowered = location.lower()
    return "login" in lowered or "error" in lowered


def _is_login_redirect(resp: httpx.Response) -> bool:
    if resp.status_code not in (301, 302, 303):
        return False
    return "login" in resp.headers.get("Location", "").lower()


def _resolve_redirect(location: str, request_url: str) -> str:
    """Resolve a Location header against the URL that produced the redirect.

    Per RFC 7231, relative redirect targets are resolved against the
    request URL — not against the host root. SchoolSoft's POST to
    ``/<school>/jsp/Login.jsp`` typically replies with
    ``Location: student/right_student_startpage.jsp`` (no leading slash),
    which must become ``/<school>/jsp/student/right_student_startpage.jsp``,
    not ``/<school>/student/...`` (which 404s).
    """
    if location.startswith(("http://", "https://")):
        return location
    # ``urljoin`` handles "/abs/path", "rel/path", and "?query-only" cleanly.
    return urljoin(request_url, location)
