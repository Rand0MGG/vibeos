# Goal 04 Model Gateway v1 and SecretRef boundary

Last updated: 2026-07-22.

## Authority and process topology

`vibeos.model_gateway.ModelGateway` is the only production model-call authority
introduced by Goal 04. Its v1 contracts bind one request to a task and attempt,
one D0 context item, an explicit timeout/total/token budget, cancellation token,
purpose, operation and strict response schema.

```text
Core / Durable Task
  -> semantic_worker subprocess (scrubbed environment, no session bus)
  -> ModelRequest v1 + opaque SecretRef + provider route metadata
  -> transport_worker subprocess (session bus allowed)
  -> secret-tool / freedesktop Secret Service, just-in-time resolve
  -> OpenAI-compatible HTTPS adapter
  -> strict ModelResponse or classified GatewayFailure + redacted receipt
```

Only `transport_worker` composes `SecretToolSecretStore` with the provider HTTP
adapter. Core, planner and the semantic worker never receive a secret value.
The semantic worker environment is rebuilt from an allowlist and omits the
session bus and secret-like environment names. Provider secrets travel only on
the stdin/stdout edge between `secret-tool` and the transport process; they do
not enter argv, provider route metadata, task state, events, outbox, traces,
model context or error text.

The process boundary limits accidental or architectural disclosure to model
code, semantic workers, ordinary Core paths, persistence and logs. It does not
claim resistance to an arbitrary compromised process running as the same Unix
UID. Goal 05 may strengthen the broker/grant mechanism, but must extend these
v1 request, response, budget and failure authorities rather than replace them.

## Deterministic service-diagnosis boundary

Goal 04 admits only `service_diagnosis` / `diagnose_fixed_user_service` with one
`application/vnd.vibeos.service-facts.v2+json` D0 item. The fact digest binds
the response to the supplied context. Deterministic validation rejects stale
facts, another unit, arguments, a mismatched digest/effect, restart without a
failed pre-state, or mutation when the fixture is already active. The model can
describe a diagnosis and propose `start`, `restart` or `none`; it cannot grant
authority or execute the proposal.

429, provider 5xx, timeout, invalid JSON, schema mismatch, token/wall-clock
budget exhaustion, cancellation and unknown delivery are distinct fail-closed
results. A locked keyring is a durable `WAITING` condition keyed by
`secret-service:unlocked:<secret-id>`; the matching unlock event resumes the
same task state.

## Legacy provider-call inventory

The old direct HTTP implementation is disabled: it no longer reads credentials
or sends provider requests. Its import surface remains temporarily so the
following semantic callers fail closed without a big-bang rewrite.

| Callers | Current owner | Goal 05 deletion gate |
| --- | --- | --- |
| `intent.py`, `understanding.py`, `clarification.py` | understanding migration | Each purpose has a typed Gateway v1 schema and recorded route policy. |
| `goal_synthesizer.py`, `candidate_selection.py`, `strategy.py`, `replanner.py` | planning migration | Each purpose uses task/attempt binding, total budget, cancellation and strict result validation. |
| `semantic_acceptance.py` | acceptance migration | Acceptance input/output is typed and model output remains non-authoritative. |
| `command_service.py`, `daemon.py` | budget compatibility | All migrated purposes use Gateway budget authority; the old context manager can then be deleted. |

`architecture_guard.py` rejects any new `provider_client` caller outside this
inventory and rejects provider authorization material outside the Gateway
transport adapter.

## Evidence state

Offline acceptance covers synthetic D0 facts, strict success, every required
failure class, locked/unlocked durable transitions, leak canary, secret-tool
stdin/argv handling, route persistence and a real semantic subprocess without
session bus or secret environment. A controlled real-provider smoke is a
separate environment gate and must not be reported as passed unless a user
credential is present in the session Secret Service.
