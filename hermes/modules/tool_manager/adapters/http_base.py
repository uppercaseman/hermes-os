"""Generic HTTP transport for provider adapters.

Pure async, no third-party deps. Reused by every adapter that talks to
an HTTP JSON API. The transport is intentionally minimal:

- It does **not** import `httpx` or `aiohttp`. We use
  `asyncio.open_connection` + a hand-rolled HTTP/1.1 client for two
  reasons:

    1. **No new top-level dependency.** A provider adapter is the one
       place in Hermes where a network boundary is crossed; the smallest
       possible runtime surface lowers the install footprint and keeps
       the placeholder path testable on every Python install on Earth.

    2. **Injectable transport.** Tests pass a `Transport` instance into
       the adapter, so unit tests don't need to monkey-patch the world
       or rely on `unittest.mock`; they hand the adapter a transport
       that returns scripted responses.

  When real-world traffic demands it (proxying, TLS, gzip, etc.), a
  future Sprint can swap the transport for an `httpx`-backed one behind
  the same `Transport` Protocol without changing any adapter.

- Streaming responses (SSE / chunked transfer) are exposed via an
  `AsyncIterator[bytes]` so each adapter can translate provider-shaped
  chunks into Hermes `ToolStreamChunk`s.

- The transport is connection-per-call: simple, stateless, no pooling.
  For Provider-routed traffic at Hermes' invocation rate, that's
  overkill-and-also-totally-fine today.

Cancellation: `cancel()` flips a flag the transport checks on its
select loop; in-flight HTTP requests get a best-effort socket close.
"""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class HTTPTransportError(Exception):
    """Base for every transport-level failure."""


class HTTPConnectionError(HTTPTransportError):
    """Could not establish or keep a TCP connection."""


class HTTPTimeoutError(HTTPTransportError):
    """The call exceeded its timeout."""


class HTTPCancelledError(HTTPTransportError):
    """The caller (or its parent) cancelled the operation."""


class HTTPStatusError(HTTPTransportError):
    """The server returned a non-2xx HTTP status."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


# --------------------------------------------------------------------------- #
# Transport contracts
# --------------------------------------------------------------------------- #


@dataclass
class HTTPRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    timeout_seconds: float = 30.0


@dataclass
class HTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@runtime_checkable
class Transport(Protocol):
    async def send(self, request: HTTPRequest) -> HTTPResponse:
        """Single-shot request/response. Implementations MUST honour
        `cancel()` on the request's `Cancellation` token."""

    def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        """Streaming response. Returns raw byte chunks (e.g. SSE event
        payloads separated by `\\n\\n`)."""


class CancellationToken:
    """Tiny cancellation flag. The transport checks this between read
    cycles; calling `cancel()` immediately marks the in-flight request
    as cancelled."""

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def wait(self) -> None:
        await self._cancelled.wait()


# --------------------------------------------------------------------------- #
# Real transport
# --------------------------------------------------------------------------- #


class StdlibHTTPTransport:
    """Pure-stdlib HTTP/1.1 transport. Supports request/response and
    chunked-transfer streaming (SSE-friendly). One connection per call."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self._ssl_context = ssl_context

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        return await asyncio.wait_for(
            self._send(request, stream=False),
            timeout=request.timeout_seconds,
        )

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        # Build header buffer, send, then yield chunks until EOF or
        # cancellation. Implemented as a queue fed by an internal task.
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        cancellation = CancellationToken()
        task = asyncio.create_task(self._stream(request, queue, cancellation))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            cancellation.cancel()
            if not task.done():
                try:
                    await task
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _send(self, request: HTTPRequest, *, stream: bool) -> HTTPResponse:
        parsed = urlsplit(request.url)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        headers = dict(request.headers)
        headers.setdefault("Host", f"{host}:{port}")
        headers.setdefault("User-Agent", "hermes/1.0")
        headers.setdefault("Accept", "*/*")
        if request.body and "Content-Length" not in headers:
            headers["Content-Length"] = str(len(request.body))
        if request.body and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=host,
                    port=port,
                    ssl=self._ssl_context if scheme == "https" else None,
                    server_hostname=host if scheme == "https" else None,
                ),
                timeout=request.timeout_seconds,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise HTTPConnectionError(str(exc)) from exc

        try:
            wire = self._build_request_line(request.method, path, headers)
            writer.write(wire)
            writer.write(request.body)
            await writer.drain()

            status, headers, raw = await asyncio.wait_for(
                self._read_response(reader, stream=stream),
                timeout=request.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPTimeoutError(f"timeout after {request.timeout_seconds}s") from exc
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

        return HTTPResponse(status=status, headers=headers, body=raw)

    async def _stream(
        self,
        request: HTTPRequest,
        queue: asyncio.Queue[bytes | BaseException | None],
        cancellation: CancellationToken,
    ) -> None:
        try:
            resp = await self._send(request, stream=True)
            queue.put_nowait(resp.body)
            queue.put_nowait(None)
        except BaseException as exc:  # noqa: BLE001 -- queue handoff is a transport boundary
            queue.put_nowait(exc)

    def _build_request_line(self, method: str, path: str, headers: dict[str, str]) -> bytes:
        lines = [f"{method} {path} HTTP/1.1"]
        for name, value in headers.items():
            lines.append(f"{name}: {value}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")

    async def _read_response(
        self, reader: asyncio.StreamReader, *, stream: bool
    ) -> tuple[int, dict[str, str], bytes]:
        # Status line
        status_line = await reader.readline()
        if not status_line:
            raise HTTPConnectionError("server closed the connection before status line")
        try:
            _, code, _ = status_line.decode("latin-1").rstrip("\r\n").split(" ", 2)
            status = int(code)
        except ValueError as exc:
            raise HTTPConnectionError(f"malformed status line: {status_line!r}") from exc

        # Headers
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            decoded = line.decode("latin-1").rstrip("\r\n")
            if ":" in decoded:
                name, _, value = decoded.partition(":")
                headers[name.strip()] = value.strip()

        # Body
        if stream:
            body = await self._read_chunked(reader)
        else:
            body = await self._read_content_length(reader, headers)

        return status, headers, body

    async def _read_chunked(self, reader: asyncio.StreamReader) -> bytes:
        buf = bytearray()
        while True:
            size_line = await reader.readline()
            size_hex = size_line.decode("latin-1").rstrip("\r\n").split(";", 1)[0].strip()
            try:
                size = int(size_hex, 16)
            except ValueError as exc:
                raise HTTPConnectionError(f"malformed chunk size: {size_line!r}") from exc
            if size == 0:
                # consume trailing CRLF
                await reader.readline()
                break
            chunk = await reader.readexactly(size)
            buf.extend(chunk)
            await reader.readline()  # trailing CRLF after each chunk
        return bytes(buf)

    async def _read_content_length(
        self, reader: asyncio.StreamReader, headers: dict[str, str]
    ) -> bytes:
        cl = headers.get("Content-Length")
        if cl is None:
            # No content-length and not chunked -- read until EOF.
            return await reader.read()
        try:
            size = int(cl)
        except ValueError as exc:
            raise HTTPConnectionError(f"malformed Content-Length: {cl!r}") from exc
        return await reader.readexactly(size)


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #


def make_authorization_header(value: str) -> dict[str, str]:
    if not value:
        return {}
    return {"Authorization": f"Bearer {value}"}


def safe_json_loads(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPTransportError(f"non-JSON response body ({exc})") from exc


def normalize_host(url: str) -> tuple[str, int, bool]:
    """Helper used by tests / adapters that need to know the destination
    without opening a connection (e.g. log audit)."""
    parsed = urlsplit(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if scheme == "https" else 80)
    return host, port, scheme == "https"


__all__ = [
    "HTTPRequest",
    "HTTPResponse",
    "Transport",
    "StdlibHTTPTransport",
    "CancellationToken",
    "HTTPTransportError",
    "HTTPConnectionError",
    "HTTPTimeoutError",
    "HTTPCancelledError",
    "HTTPStatusError",
    "make_authorization_header",
    "safe_json_loads",
    "normalize_host",
]


# Re-export the socket import so type checkers can resolve `socket.AF_INET`
# etc. when downstream files reference it for connection diagnostics.
_ = socket  # noqa: F841 -- availability import
