from __future__ import annotations

import json
import sys

from .contracts import TransportEnvelope
from .provider import OpenAICompatibleTransport
from .secrets import SecretToolSecretStore


def main() -> int:
    try:
        envelope = TransportEnvelope.model_validate_json(sys.stdin.read())
        result = OpenAICompatibleTransport(SecretToolSecretStore()).execute(envelope.route, envelope.request)
    except Exception:
        print(json.dumps({"error": "provider transport rejected the request"}))
        return 2
    sys.stdout.write(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
