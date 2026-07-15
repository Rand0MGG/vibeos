from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HttpRequest:
    method: str
    target: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


HttpHandler = Callable[[HttpRequest], Awaitable[HttpResponse]]


class AsyncHttpServer:
    name = "http"

    def __init__(self, host: str, port: int, handler: HttpHandler, *, request_timeout_seconds: float = 15.0) -> None:
        self.host = host
        self.port = port
        self._handler = handler
        self._request_timeout_seconds = request_timeout_seconds
        self._server: asyncio.Server | None = None
        self._status = "stopped"
        self._message = "HTTP compatibility adapter is stopped"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port, limit=MAX_HEADER_BYTES)
        sockets = self._server.sockets or ()
        if sockets:
            address = sockets[0].getsockname()
            self.port = int(address[1])
        self._status = "ready"
        self._message = f"thin compatibility adapter listening on {self.host}:{self.port}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._status = "stopped"
        self._message = "HTTP compatibility adapter is stopped"

    def health_status(self) -> tuple[str, str]:
        return self._status, self._message

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(self._read_request(reader), timeout=self._request_timeout_seconds)
            response = await asyncio.wait_for(self._handler(request), timeout=self._request_timeout_seconds)
        except _HttpProtocolError as exc:
            response = HttpResponse(exc.status, (f'{{"error":"{exc.message}"}}').encode("utf-8"))
        except asyncio.TimeoutError:
            response = HttpResponse(408, b'{"error":"request_timeout"}')
        except Exception:
            response = HttpResponse(500, b'{"error":"daemon_internal_error"}')
        writer.write(_encode_response(response))
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _read_request(self, reader: asyncio.StreamReader) -> HttpRequest:
        request_line = await reader.readline()
        if not request_line or len(request_line) >= MAX_HEADER_BYTES:
            raise _HttpProtocolError(400, "invalid_request_line")
        try:
            method, target, version = request_line.decode("ascii").strip().split(" ", 2)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _HttpProtocolError(400, "invalid_request_line") from exc
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise _HttpProtocolError(400, "unsupported_http_version")
        headers: dict[str, str] = {}
        consumed = len(request_line)
        while True:
            line = await reader.readline()
            consumed += len(line)
            if consumed > MAX_HEADER_BYTES:
                raise _HttpProtocolError(431, "headers_too_large")
            if line in {b"\r\n", b"\n", b""}:
                break
            try:
                name, value = line.decode("ascii").split(":", 1)
            except (UnicodeDecodeError, ValueError) as exc:
                raise _HttpProtocolError(400, "invalid_header") from exc
            headers[name.strip().lower()] = value.strip()
        raw_length = headers.get("content-length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise _HttpProtocolError(400, "invalid_content_length") from exc
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            raise _HttpProtocolError(413, "body_too_large")
        body = await reader.readexactly(content_length) if content_length else b""
        return HttpRequest(method=method, target=target, headers=headers, body=body)


@dataclass(frozen=True)
class _HttpProtocolError(Exception):
    status: int
    message: str


def _encode_response(response: HttpResponse) -> bytes:
    reasons = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        408: "Request Timeout",
        413: "Payload Too Large",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    reason = reasons.get(response.status, "Error")
    headers = (
        f"HTTP/1.1 {response.status} {reason}\r\nContent-Type: {response.content_type}\r\nContent-Length: {len(response.body)}\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    return headers + response.body
