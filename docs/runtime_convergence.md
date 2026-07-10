# Runtime Convergence Architecture

Last updated: 2026-07-10

## Default supported-task path

`GoalLoop` is the sole orchestration path for supported tasks:

```text
request ingress
  -> planning bootstrap
  -> GoalLoop
       -> observe pre
       -> step safety review
       -> execute one identified step
       -> observe post
       -> verify / accept
       -> retry, repair, replan, suspend, resume, or finish
  -> CommandResult, audit, and trace finalization
```

`CapabilityBroker` owns request ingress, planning bootstrap, dependency assembly, and response formatting. It does not own a second execution/retry/replan loop.

## Ownership boundaries

- `GoalLoop`: typed loop state and all task-control transitions.
- `AgentRuntime`: session/goal/turn identity, tool registry, execution receipts, and compatibility ledger projection. It is not a competing control loop.
- `PermissionPolicy`: the only source of `allow`, `deny`, and `review_required` decisions.
- `ReviewStore`: durable local review state and atomic claim/release/consume transitions.
- `TaskTraceStore` and `AuditLog`: observability outputs; neither is the authority for approval state.

## Local durability and review semantics

`ReviewStore` stores new review events in a local SQLite database next to the configured review path. Existing JSONL review files are imported once for compatibility; JSONL is no longer the authoritative state store.

Before a real approved side effect is dispatched, the daemon atomically claims the review. Concurrent attempts cannot claim the same approval twice. A successful attempt consumes the review. A failed attempt releases the claim back to `approved`, allowing an explicit retry without replaying a completed side effect.

Each GoalLoop execution has a stable `run_id`, `step_id`, and deterministic `attempt_id`. Step results include the attempt id and audit receipt.

## Evidence policy

Routes with declared verifiers, such as browser and media flows, require observation-backed progress. Routes without a verifier may accept a successful bounded adapter receipt as progress. Dry runs never require an external state change.

## Trace privacy policy

Normal traces store structural metadata, hashes and bounded normalized data, but omit raw user utterances and raw model request/response artifacts. Debug traces may retain raw diagnostic artifacts, but credential-like fields are always redacted and strings are truncated.

## Verification

Run the local test suite in the configured WSL environment:

```bash
python -m pytest -q
```

Focused regression coverage includes default GoalLoop routing, review resume, duplicate approval protection, failed-approval retry, trace privacy, and equivalent structured HTTP/D-Bus daemon failures.
