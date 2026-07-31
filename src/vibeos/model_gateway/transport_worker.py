from __future__ import annotations

import json
import sys

from .contracts import JsonObjectTransportEnvelope, TransportEnvelope
from .provider import OpenAICompatibleTransport
from .secrets import SecretToolSecretStore


def main() -> int:
    try:
        payload = sys.stdin.read()
        decoded = json.loads(payload)
        transport = OpenAICompatibleTransport(SecretToolSecretStore())
        if isinstance(decoded, dict) and isinstance(decoded.get("request"), dict) and decoded["request"].get("purpose") != "service_diagnosis":
            json_envelope = JsonObjectTransportEnvelope.model_validate_json(payload)
            output = transport.execute_json_object(json_envelope.route, json_envelope.request).model_dump_json()
        else:
            service_envelope = TransportEnvelope.model_validate_json(payload)
            output = transport.execute(service_envelope.route, service_envelope.request).model_dump_json()
    except Exception:
        print(json.dumps({"error": "provider transport rejected the request"}))
        return 2
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
