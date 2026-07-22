from __future__ import annotations

import json
import os
import sys

from .contracts import SemanticWorkerInvocation, SemanticWorkerOutput, build_model_request


_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def process(payload: str) -> SemanticWorkerOutput:
    invocation = SemanticWorkerInvocation.model_validate_json(payload)
    return SemanticWorkerOutput(
        request=build_model_request(invocation),
        worker_pid=os.getpid(),
        session_bus_present=bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
        secret_environment_present=any(any(marker in name.upper() for marker in _SECRET_ENV_MARKERS) for name in os.environ),
    )


def main() -> int:
    try:
        output = process(sys.stdin.read())
    except Exception:
        print(json.dumps({"error": "semantic worker rejected the strict invocation"}))
        return 2
    sys.stdout.write(output.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
