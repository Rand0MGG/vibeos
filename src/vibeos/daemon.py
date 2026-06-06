from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from dataclasses import asdict
from urllib.parse import parse_qs, urlparse

from .broker import CapabilityBroker
from .dbus_service import run_dbus_service
from .models import CommandRequest


class VibeRequestHandler(BaseHTTPRequestHandler):
    broker: CapabilityBroker
    status_payload: dict[str, Any]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/v1/status":
            self._write_json(self.status_payload)
            return
        if path == "/v1/apps":
            self._write_json({"apps": [asdict(app) for app in self.broker.apps.list_apps()]})
            return
        if path == "/v1/windows":
            self._write_json({"windows": [asdict(window) for window in self.broker.windows.list_windows()]})
            return
        if path == "/v1/capabilities":
            self._write_json(self.broker.capabilities())
            return
        if path == "/v1/reviews/pending":
            self._write_json({"reviews": self.broker.pending_reviews()})
            return
        if path == "/v1/audit/tail":
            query = parse_qs(parsed.query)
            raw_count = query.get("n", ["20"])[0]
            try:
                count = max(0, int(raw_count))
            except ValueError:
                count = 20
            self._write_json({"entries": self.broker.audit.tail(count)})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/v1/command":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        utterance = str(payload.get("utterance", "")).strip()
        review_id = payload.get("review_id")
        reject = bool(payload.get("reject", False))
        if not utterance and not review_id:
            self.send_error(400, "missing utterance or review_id")
            return
        if review_id and reject:
            result = self.broker.reject_review(str(review_id), transport="http")
            self._write_json(dataclass_to_jsonable(result))
            return
        request = CommandRequest(
            utterance=utterance,
            mode=str(payload.get("mode", "auto_low_risk")),
            dry_run=bool(payload.get("dry_run", False)),
            approve=bool(payload.get("approve", False)),
            review_id=review_id,
            debug=bool(payload.get("debug", False)),
            transport="http",
        )
        result = self.broker.handle(request)
        self._write_json(dataclass_to_jsonable(result))

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("vibed: " + format % args + "\n")

    def _write_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def dataclass_to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: dataclass_to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_jsonable(item) for key, item in value.items()}
    return value


def create_http_server(
    broker: CapabilityBroker,
    host: str,
    port: int,
    status_payload: dict[str, Any] | None = None,
) -> ThreadingHTTPServer:
    VibeRequestHandler.broker = broker
    VibeRequestHandler.status_payload = status_payload or build_status_payload(transports=["http"], host=host, port=port)
    return ThreadingHTTPServer((host, port), VibeRequestHandler)


def run_http_server(server: ThreadingHTTPServer) -> None:
    host, port = server.server_address[:2]
    print(f"vibed listening on http://{host}:{port}")
    server.serve_forever()


def start_http_server_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=run_http_server, args=(server,), name="vibed-http", daemon=True)
    thread.start()
    return thread


def build_status_payload(transports: list[str], host: str | None = None, port: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "service": "vibed",
        "transports": transports,
    }
    if host is not None:
        payload["host"] = host
    if port is not None:
        payload["port"] = port
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibed", description="VibeOS daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dbus", action="store_true", help="serve the D-Bus API and keep the local HTTP API available")
    args = parser.parse_args(argv)

    broker = CapabilityBroker()
    status_payload = build_status_payload(
        transports=["http", "dbus"] if args.dbus else ["http"],
        host=args.host,
        port=args.port,
    )
    server = create_http_server(broker, args.host, args.port, status_payload=status_payload)
    server_thread: threading.Thread | None = None
    stop_dbus_callbacks: list[Callable[[], None]] = []

    def stop(_signum, _frame) -> None:
        server.shutdown()
        for callback in stop_dbus_callbacks:
            callback()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.dbus:
        server_thread = start_http_server_thread(server)
        try:
            return run_dbus_service(
                broker,
                status_payload=status_payload,
                register_stop_callback=stop_dbus_callbacks.append,
            )
        finally:
            server.shutdown()
            server.server_close()
            if server_thread is not None:
                server_thread.join(timeout=5)

    try:
        run_http_server(server)
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
