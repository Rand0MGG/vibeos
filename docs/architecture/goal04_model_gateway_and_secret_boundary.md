# Goal 04 Model Gateway v1 and SecretRef boundary

Last updated: 2026-07-31.

## Authority and process topology

`vibeos.model_gateway.ModelGateway` is the only production model-call authority
introduced by Goal 04. Its v1 contracts bind each request to a task/run and
attempt, an explicit timeout/total/token budget, cancellation token, allowlisted
purpose, operation and response contract. The fixed service request additionally
binds exactly one D0 context item and its strict domain schema.

```text
Core / Durable Task
  -> semantic_worker subprocess (scrubbed environment, no session bus)
  -> ModelRequest v1 or bounded compatibility request + opaque SecretRef route
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

## Compatibility remediation and remaining inventory

The old direct HTTP implementation remains deleted: it neither reads
credentials nor sends provider requests. On 2026-07-31 the retained
`provider_client` surface was converted into an authority-free facade over this
same Gateway after ordinary `vibe ask` was found to have no live provider path.
Every caller supplies an allowlisted purpose; route selection is deterministic
(`VIBEOS_MODEL_ROUTE`, one exact metadata match, or one configured route), and
ambiguous route sets fail closed. The semantic worker still has no session bus
or secret environment, and only the existing transport worker resolves the
SecretRef.

| Callers | Gateway compatibility purpose | Goal 05 deletion gate |
| --- | --- | --- |
| `intent.py`, `understanding.py`, `clarification.py` | `intent_parse`, `goal_understanding`, `understanding_transition`, `clarification` | Replace the bounded JSON-object compatibility response with purpose-specific schemas and data policy. |
| `goal_synthesizer.py`, `candidate_selection.py`, `strategy.py`, `replanner.py` | `goal_synthesis`, `route_selection`, `strategy_selection`, `replanning` | Add purpose-specific schemas/route policy, then delete the facade calls. |
| `semantic_acceptance.py` | `semantic_acceptance` | Type summary/decision separately and retain non-authoritative validation. |
| `command_service.py`, `daemon.py` | shared command budget | Replace the compatibility context with native Gateway task/attempt/deadline bindings. |

`architecture_guard.py` rejects any new `provider_client` caller outside this
inventory and rejects provider authorization material outside the Gateway
transport adapter.

## Evidence state

Offline acceptance covers synthetic D0 facts, strict success, every required
failure class, locked/unlocked durable transitions, leak canary, secret-tool
stdin/argv handling, route persistence and a real semantic subprocess without
session bus or secret environment. The compatibility remediation adds a shared
transport/leak test and a production-shaped plain `vibe ask` test proving that
`goal_understanding` reaches Gateway. The dedicated controlled DeepSeek V4 Pro
service smoke passed with a user-owned SecretRef; the compatibility purposes
still require a fresh real-provider VM smoke before that extension is considered
externally accepted.
