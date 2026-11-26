"""Hand a downloaded file to someone other than the model: a disk path or an upload URL.

``download_material`` used to be the only way to move a file, and it put the
file in the tool result as base64 — an 81 kB PDF became 108 000 characters
the assistant could neither read nor pass on. These two exits keep the bytes
out of the conversation entirely.

The upload exit is the first outbound host besides SchoolSoft, and what it
sends is a child's school document. So a target must be https, its host must
be on an allowlist the operator wrote, and every address the name resolves
to must be public — checked when the socket is opened, not before, so a DNS
answer that changes between check and connect cannot steer it to the LAN.
The URL and headers are never logged: upload URLs usually carry their key.
"""

from __future__ import annotations

import contextlib
import contextvars
import ipaddress
import logging
import os
import re
import socket
import ssl
import typing
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import anyio
import httpcore
import httpx

logger = logging.getLogger(__name__)

UPLOAD_METHODS = frozenset({"PUT", "POST"})
MAX_REDIRECTS = 3
RESPONSE_EXCERPT_CHARS = 500
_RESPONSE_READ_LIMIT = 16_384

# Headers the caller may not set: they describe the connection or the body
# framing, which httpx owns, and a wrong one corrupts the upload silently.
_RESERVED_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "expect", "upgrade"}
)


class DeliveryRefused(Exception):
    """The target is not allowed. Nothing was sent or written."""


class UploadFailed(Exception):
    """The upload was attempted and did not complete."""


# --- target checks ---------------------------------------------------------


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_target(url: str, allowed: tuple[str, ...]) -> httpx.URL:
    """Parse ``url`` and refuse anything but https to an allowlisted host.

    Error messages name the host, never the full URL: the path and query of
    an upload URL are usually its credential.
    """
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as err:
        raise DeliveryRefused("upload_url is not a valid URL") from err
    if target.scheme != "https":
        raise DeliveryRefused(
            f"upload_url must use https, not {target.scheme or 'no scheme'!r}"
        )
    host = target.host.lower().rstrip(".")
    if not host:
        raise DeliveryRefused("upload_url has no host")
    if target.userinfo:
        raise DeliveryRefused(
            "upload_url must not carry a user name or password before the host"
        )
    if not allowed:
        raise DeliveryRefused(
            f"Uploads are switched off: SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS is empty. "
            f"To allow this recipient, the operator adds {host!r} to it and "
            "restarts the server."
        )
    if not host_allowed(host, allowed):
        raise DeliveryRefused(
            f"{host!r} is not in SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS. If the parent "
            f"trusts this recipient, the operator adds {host!r} (or its parent "
            "domain) to that list and restarts the server."
        )
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and not _is_public(literal):
        raise DeliveryRefused(f"{host!r} is not a public address")
    return target


async def _resolve(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _check_addresses(host: str, addresses: list[str]) -> None:
    """Refuse a host if *any* address it resolves to is not public.

    All, not just the one we would connect to: a name that answers with
    both a public and a private address is misconfigured or hostile, and
    either way not somewhere to send a child's file.
    """
    if not addresses:
        raise DeliveryRefused(f"{host!r} did not resolve to any address")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not _is_public(ip):
            raise DeliveryRefused(
                f"{host!r} resolves to a private, loopback or otherwise "
                "non-public address; refusing to send the file there"
            )


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolve, check, then connect to the checked address — never re-resolve."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        # Name lookup happens here, outside httpcore's own exception mapping,
        # so its failures are mapped by hand; otherwise they would escape
        # httpx as bare OSError / TimeoutError instead of a clear "could not
        # reach" from upload().
        try:
            with anyio.fail_after(timeout):
                addresses = await _resolve(host, port)
        except TimeoutError as err:
            raise httpcore.ConnectTimeout(f"looking up {host!r} timed out") from err
        except OSError as err:
            raise httpcore.ConnectError(f"could not look up {host!r}: {err}") from err
        _check_addresses(host, addresses)
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as err:
                last = err
        assert last is not None
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise DeliveryRefused("unix sockets are not an upload target")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, ssl_context: ssl.SSLContext) -> None:
        # httpx 0.28 has no public way to choose the network backend, and
        # the backend is the only layer that sees the address actually
        # connected to. AsyncHTTPTransport.__init__ only builds ``_pool``, so
        # it is skipped rather than run and its pool thrown away unclosed.
        # TLS still verifies against the URL's host name: httpcore takes SNI
        # from the origin, not from what connect_tcp got.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_PublicOnlyBackend(),
        )


# --- keeping the URL out of the logs ---------------------------------------

_QUIET = contextvars.ContextVar("schoolsoft_upload_quiet", default=False)


class _UploadLogFilter(logging.Filter):
    """Drop httpx's "HTTP Request: PUT <full url>" line during an upload."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _QUIET.get()


logging.getLogger("httpx").addFilter(_UploadLogFilter())


# --- the upload ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadResponse:
    status_code: int
    excerpt: str
    host: str
    redirected: bool
    unfollowed_redirect: bool

    @property
    def accepted(self) -> bool:
        # 303 is how a recipient says "stored; the result is over there".
        return 200 <= self.status_code < 300 or self.status_code == 303


def _ascii_filename(filename: str) -> str:
    folded = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    folded = re.sub(r'[\x00-\x1f"\\\\]', "_", folded).strip()
    return folded or "file"


def content_disposition(filename: str) -> str:
    clean = re.sub(r"[\r\n]", " ", filename)
    return (
        f'attachment; filename="{_ascii_filename(clean)}"; '
        f"filename*=UTF-8''{quote(clean, safe='')}"
    )


# RFC 9110 token for names; no control characters in values (tab is allowed).
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEADER_VALUE_BAD = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def _clean_headers(headers: dict[str, str] | None, *, multipart: bool) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for name, value in (headers or {}).items():
        key = str(name).strip()
        lowered = key.lower()
        if not _HEADER_NAME.fullmatch(key) or _HEADER_VALUE_BAD.search(str(value)):
            raise DeliveryRefused("headers contain an invalid name or value")
        if lowered in _RESERVED_HEADERS:
            raise DeliveryRefused(f"header {key!r} is set by the server and cannot be overridden")
        if multipart and lowered == "content-type":
            raise DeliveryRefused(
                "Content-Type cannot be set together with form_field: the multipart "
                "boundary goes in that header"
            )
        cleaned[key] = str(value)
    return cleaned


def _build_request(
    client: httpx.AsyncClient,
    *,
    method: str,
    target: httpx.URL,
    content: bytes,
    filename: str,
    content_type: str,
    form_field: str | None,
    headers: dict[str, str],
) -> httpx.Request:
    if form_field:
        return client.build_request(
            method,
            target,
            files={form_field: (filename, content, content_type)},
            headers=headers,
        )
    lowered = {k.lower() for k in headers}
    base = {}
    if "content-type" not in lowered:
        base["Content-Type"] = content_type
    if "content-disposition" not in lowered:
        base["Content-Disposition"] = content_disposition(filename)
    return client.build_request(method, target, content=content, headers={**base, **headers})


async def _read_excerpt(response: httpx.Response) -> str:
    collected = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            collected.extend(chunk)
            if len(collected) >= _RESPONSE_READ_LIMIT:
                break
    except httpx.HTTPError:
        # The status line has arrived, so the file did too. A stalled or cut-off
        # answer body must not turn that into an error that says it "may not
        # have arrived" and drops the status.
        if not collected:
            return "(the recipient's answer could not be read)"
    encoding = response.charset_encoding or "utf-8"
    try:
        text = bytes(collected).decode(encoding, errors="replace")
    except LookupError:
        text = bytes(collected).decode("utf-8", errors="replace")
    return text[:RESPONSE_EXCERPT_CHARS]


_URL_IN_TEXT = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _describe(err: Exception) -> str:
    # Any URL, not just the one passed in: after a redirect the error names
    # the Location, which can carry its own key.
    text = _URL_IN_TEXT.sub("<upload_url>", str(err))
    return f"{type(err).__name__}: {text}" if text else type(err).__name__


def _same_origin(a: httpx.URL, b: httpx.URL) -> bool:
    return (a.scheme, a.host.lower(), a.port) == (b.scheme, b.host.lower(), b.port)


# Describe the body, not the caller's access to the recipient, so they may
# follow a redirect to another host. Everything else might be a key.
_BODY_HEADERS = frozenset({"content-type", "content-disposition"})


async def upload(
    *,
    url: str,
    method: str,
    content: bytes,
    filename: str,
    content_type: str,
    form_field: str | None,
    headers: dict[str, str] | None,
    allowed_domains: tuple[str, ...],
    timeout_s: float,
    ssl_context: ssl.SSLContext | None = None,
) -> UploadResponse:
    """Send ``content`` once. Follows 307/308 only, re-checking each hop.

    Never retries: a timeout after the body went out may still have stored
    the file, and a second attempt would make a duplicate the recipient has
    to clean up.
    """
    verb = method.strip().upper()
    if verb not in UPLOAD_METHODS:
        raise DeliveryRefused(f"method must be PUT or POST, got {method!r}")
    target = check_target(url, allowed_domains)
    if form_field is not None and not form_field.strip():
        form_field = None
    extra = _clean_headers(headers, multipart=form_field is not None)
    origin = target
    context = ssl_context or httpx.create_ssl_context(trust_env=False)

    token = _QUIET.set(True)
    redirected = False
    try:
        async with httpx.AsyncClient(
            transport=_GuardedTransport(context),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 15.0)),
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                request = _build_request(
                    client,
                    method=verb,
                    target=target,
                    content=content,
                    filename=filename,
                    content_type=content_type,
                    form_field=form_field,
                    headers=extra,
                )
                try:
                    response = await client.send(request, stream=True)
                except DeliveryRefused:
                    raise
                except httpx.TimeoutException as err:
                    raise UploadFailed(
                        f"Timed out talking to {target.host!r} ({type(err).__name__}). "
                        "The file may or may not have arrived — check with the "
                        "recipient before sending it again."
                    ) from None
                except httpx.HTTPError as err:
                    raise UploadFailed(
                        f"Could not send to {target.host!r}: {_describe(err)}"
                    ) from None
                try:
                    location = response.headers.get("location")
                    if response.status_code in (307, 308) and location:
                        await response.aclose()
                        target = check_target(str(target.join(location)), allowed_domains)
                        redirected = True
                        if not _same_origin(origin, target):
                            # An API key for one service must not reach
                            # another just because both are allowlisted.
                            extra = {
                                k: v for k, v in extra.items() if k.lower() in _BODY_HEADERS
                            }
                        continue
                    excerpt = await _read_excerpt(response)
                finally:
                    await response.aclose()
                return UploadResponse(
                    status_code=response.status_code,
                    excerpt=excerpt,
                    host=target.host,
                    redirected=redirected,
                    unfollowed_redirect=300 <= response.status_code < 400,
                )
    finally:
        _QUIET.reset(token)
    raise UploadFailed(f"More than {MAX_REDIRECTS} redirects; nothing more was sent")


# --- save_to ---------------------------------------------------------------


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f]", "_", base).strip().lstrip(".")
    return base or "file"


def save_path(save_to: str, filename: str, allowed_dirs: tuple[str, ...]) -> Path:
    """Where ``save_to`` points, refused unless it is inside an allowed dir.

    A directory (existing, or written with a trailing slash) gets the file's
    own name. A relative path is taken from the first allowed directory.
    """
    if not allowed_dirs:
        raise DeliveryRefused(
            "save_to is switched off: SCHOOLSOFT_SAVE_DIRS is empty. The operator "
            "sets it to the directory files may be saved in."
        )
    raw = save_to.strip()
    if not raw:
        raise DeliveryRefused("save_to is empty")
    if "\x00" in raw:
        raise DeliveryRefused("save_to contains a NUL character")
    candidate = Path(os.path.expanduser(raw))
    if not candidate.is_absolute():
        candidate = Path(allowed_dirs[0]) / candidate
    if raw.endswith(("/", os.sep)) or candidate.is_dir():
        candidate = candidate / safe_filename(filename)
    resolved = Path(os.path.realpath(candidate))
    # realpath on the roots too: the settings already do it, but a caller that
    # builds Settings by hand (or a symlinked temp dir) would otherwise never match.
    roots = [Path(os.path.realpath(d)) for d in allowed_dirs]
    if not any(resolved.is_relative_to(root) and resolved != root for root in roots):
        raise DeliveryRefused(
            "save_to must be inside one of the directories in SCHOOLSOFT_SAVE_DIRS: "
            + ", ".join(allowed_dirs)
        )
    return resolved


def write_new_file(path: Path, content: bytes, allowed_dirs: tuple[str, ...]) -> None:
    """Write ``content`` to a file that must not exist yet.

    ``xb`` refuses an existing file — and an existing symlink — so a saved
    file never overwrites anything, here or wherever a link points.
    """
    roots = [Path(os.path.realpath(d)) for d in allowed_dirs]
    root = next(r for r in roots if path.is_relative_to(r))
    if not root.is_dir():
        raise DeliveryRefused(f"The save directory {root} does not exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not Path(os.path.realpath(path.parent)).is_relative_to(root):
        raise DeliveryRefused("save_to escapes the allowed directory")
    created = False
    try:
        with open(path, "xb") as handle:
            created = True
            handle.write(content)
    except FileExistsError:
        raise DeliveryRefused(
            f"{path} already exists; pass another save_to (nothing is overwritten)"
        ) from None
    except BaseException:
        # A half-written file would make every retry fail with "already exists".
        if created:
            with contextlib.suppress(OSError):
                path.unlink()
        raise
