from __future__ import annotations

import argparse
import json
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .broker import CapabilityBroker
from .dbus_service import run_dbus_service
from .models import CommandRequest


class VibeRequestHandler(BaseHTTPRequestHandler):
    broker: CapabilityBroker

    def do_GET(self) -> None:
        if self.path == "/v1/status":
            self._write_json({"status": "ok", "service": "vibed"})
            return
        if self.path == "/v1/capabilities":
            self._write_json(self.broker.capabilities())
            return
        if self.path == "/v1/reviews/pending":
            self._write_json({"reviews": self.broker.pending_reviews()})
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
            result = self.broker.reject_review(str(review_id))
            self._write_json(dataclass_to_jsonable(result))
            return
        request = CommandRequest(
            utterance=utterance,
            mode=str(payload.get("mode", "auto_low_risk")),
            dry_run=bool(payload.get("dry_run", False)),
            approve=bool(payload.get("approve", False)),
            review_id=review_id,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibed", description="VibeOS daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dbus", action="store_true", help="serve the D-Bus API instead of HTTP")
    args = parser.parse_args(argv)

    broker = CapabilityBroker()
    if args.dbus:
        return run_dbus_service(broker)

    VibeRequestHandler.broker = broker
    server = ThreadingHTTPServer((args.host, args.port), VibeRequestHandler)

    def stop(_signum, _frame) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"vibed listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
